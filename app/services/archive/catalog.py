"""实现 AV1-P10 文档处理列表、正式档案目录和脱敏审计查询。"""

from datetime import date
from uuid import UUID

from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    Document,
    ProjectStage,
    ArchiveDocumentType,
)
from app.schemas.archive_catalog import (
    ArchiveDetailRead,
    ArchivePageRead,
    ArchiveSummaryRead,
    ProcessDocumentPageRead,
)
from app.services.archive.reads import (
    archive_field_value,
    build_archive_draft_read,
    build_process_document_read,
    list_archive_field_values,
    list_visibility_blocked_document_ids,
)


def _field_map(document_id: UUID, session: Session) -> dict[ArchiveFieldName, ArchiveFieldValue]:
    """把七个字段按固定名称索引，供目录筛选和摘要构建复用。"""
    return {field.field_name: field for field in list_archive_field_values(document_id, session)}


def _enum_value(field: ArchiveFieldValue | None, enum_type):
    """把数据库文本字段安全转换为固定字典值。"""
    if field is None or archive_field_value(field) is None:
        return None
    try:
        return enum_type(str(archive_field_value(field)))
    except ValueError:
        return None


def _build_archive_summary(
    *,
    document: Document,
    archive_document: ArchiveDocument,
    fields: dict[ArchiveFieldName, ArchiveFieldValue],
) -> ArchiveSummaryRead:
    """组合正式目录中的结构化档案摘要，不返回原文正文。"""
    title = archive_field_value(fields.get(ArchiveFieldName.TITLE))
    organization = archive_field_value(fields.get(ArchiveFieldName.AUTHORING_ORGANIZATION))
    document_date = archive_field_value(fields.get(ArchiveFieldName.DOCUMENT_DATE))
    return ArchiveSummaryRead(
        id=document.id,
        filename=document.filename,
        status=archive_document.status,
        title=str(title) if title is not None else None,
        document_type=_enum_value(fields.get(ArchiveFieldName.DOCUMENT_TYPE), ArchiveDocumentType),
        document_date=document_date if isinstance(document_date, date) else None,
        authoring_organization=str(organization) if organization is not None else None,
        project_stage=_enum_value(fields.get(ArchiveFieldName.PROJECT_STAGE), ProjectStage),
        confirmed_at=archive_document.confirmed_at,
        version=archive_document.version,
    )


def list_process_documents(
    *,
    project_id: UUID,
    page: int,
    page_size: int,
    status: ArchiveDocumentStatus | None,
    session: Session,
) -> ProcessDocumentPageRead:
    """分页返回项目内全部仍存在的归档文档处理状态。"""
    statement = (
        select(Document, ArchiveDocument)
        .join(ArchiveDocument, ArchiveDocument.document_id == Document.id)
        .where(Document.project_id == project_id)
        .order_by(ArchiveDocument.updated_at.desc(), Document.id.desc())
    )
    if status is not None:
        statement = statement.where(ArchiveDocument.status == status)
    rows = list(session.exec(statement).all())
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    return ProcessDocumentPageRead(
        items=[
            build_process_document_read(
                document=document,
                archive_document=archive_document,
                field_values=list_archive_field_values(document.id, session),
            )
            for document, archive_document in page_rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


def list_formal_archives(
    *,
    project_id: UUID,
    page: int,
    page_size: int,
    document_type: ArchiveDocumentType | None,
    project_stage: ProjectStage | None,
    document_date_from: date | None,
    document_date_to: date | None,
    document_date_is_null: bool,
    authoring_organization: str | None,
    session: Session,
) -> ArchivePageRead:
    """只返回 CONFIRMED 且没有可见性阻断的正式档案，并执行结构化筛选。"""
    if document_date_is_null and (document_date_from is not None or document_date_to is not None):
        raise AppError(422, "VALIDATION_ERROR", "日期为空筛选不能与日期区间同时使用。")
    if document_date_from is not None and document_date_to is not None and document_date_from > document_date_to:
        raise AppError(422, "VALIDATION_ERROR", "日期区间起点不能晚于终点。")

    blocked_ids = list_visibility_blocked_document_ids(project_id, session)
    rows = session.exec(
        select(Document, ArchiveDocument)
        .join(ArchiveDocument, ArchiveDocument.document_id == Document.id)
        .where(
            Document.project_id == project_id,
            ArchiveDocument.status == ArchiveDocumentStatus.CONFIRMED,
        )
        .order_by(ArchiveDocument.confirmed_at.desc(), Document.id.desc())
    ).all()
    filtered: list[ArchiveSummaryRead] = []
    for document, archive_document in rows:
        if document.id in blocked_ids:
            continue
        fields = _field_map(document.id, session)
        current_type = _enum_value(fields.get(ArchiveFieldName.DOCUMENT_TYPE), ArchiveDocumentType)
        current_stage = _enum_value(fields.get(ArchiveFieldName.PROJECT_STAGE), ProjectStage)
        current_date = archive_field_value(fields.get(ArchiveFieldName.DOCUMENT_DATE))
        current_org = archive_field_value(fields.get(ArchiveFieldName.AUTHORING_ORGANIZATION))
        if document_type is not None and current_type != document_type:
            continue
        if project_stage is not None and current_stage != project_stage:
            continue
        if document_date_is_null and current_date is not None:
            continue
        if document_date_from is not None and (current_date is None or current_date < document_date_from):
            continue
        if document_date_to is not None and (current_date is None or current_date > document_date_to):
            continue
        if authoring_organization is not None and current_org != authoring_organization:
            continue
        filtered.append(
            _build_archive_summary(
                document=document,
                archive_document=archive_document,
                fields=fields,
            )
        )
    total = len(filtered)
    start = (page - 1) * page_size
    return ArchivePageRead(items=filtered[start : start + page_size], page=page, page_size=page_size, total=total)


def read_formal_archive(*, document: Document, session: Session) -> ArchiveDetailRead:
    """读取单份正式档案详情；非正式或被阻断文档统一隐藏。"""
    archive_document = session.get(ArchiveDocument, document.id)
    if archive_document is None or archive_document.status != ArchiveDocumentStatus.CONFIRMED:
        raise AppError(404, "ARCHIVE_NOT_FORMAL", "该档案当前不在正式范围。")
    if document.id in list_visibility_blocked_document_ids(document.project_id, session):
        raise AppError(404, "ARCHIVE_NOT_FORMAL", "该档案当前不在正式范围。")
    fields = _field_map(document.id, session)
    summary = _build_archive_summary(
        document=document,
        archive_document=archive_document,
        fields=fields,
    )
    draft = build_archive_draft_read(
        document=document,
        archive_document=archive_document,
        field_values=list_archive_field_values(document.id, session),
        session=session,
    )
    return ArchiveDetailRead(**summary.model_dump(), fields=draft.fields)
