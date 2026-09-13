"""提供智慧档案服务之间共享的只读投影和可见性查询。"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    ArchiveOperation,
    ArchiveOperationStatus,
    Document,
    FieldEvidence,
    FieldReviewStatus,
    ParsedSnapshot,
)
from app.schemas import ArchiveDraftRead, FieldDraftRead, FieldEvidenceRead, ParsedSnapshotRead
from app.schemas.document import FieldSummaryRead, LastErrorRead, ProcessDocumentRead


_FIELD_NAMES: tuple[ArchiveFieldName, ...] = tuple(ArchiveFieldName)
_EDITABLE_STATUSES = {
    ArchiveDocumentStatus.PENDING_CONFIRMATION,
    ArchiveDocumentStatus.PENDING_RECONFIRMATION,
}


def list_archive_field_values(
    document_id: UUID, session: Session
) -> list[ArchiveFieldValue]:
    """按固定七字段顺序读取档案字段值。

    参数:
        document_id: 要读取的文档身份。
        session: 当前数据库会话。
    """
    values = list(
        session.exec(
            select(ArchiveFieldValue).where(
                ArchiveFieldValue.document_id == document_id
            )
        ).all()
    )
    by_name = {value.field_name: value for value in values}
    return [by_name[name] for name in _FIELD_NAMES if name in by_name]


def archive_field_value(field: ArchiveFieldValue | None) -> object:
    """投影字段当前实际使用的值列。

    参数:
        field: 数据库中的档案字段值，允许为空。
    """
    if field is None:
        return None
    if field.field_name == ArchiveFieldName.DOCUMENT_DATE:
        return field.date_value
    if field.field_name == ArchiveFieldName.KEYWORDS:
        return field.json_value
    return field.text_value


def build_process_document_read(
    *,
    document: Document,
    archive_document: ArchiveDocument,
    field_values: Sequence[ArchiveFieldValue],
    index_context_chunk_count: int | None = None,
) -> ProcessDocumentRead:
    """组合项目文档处理响应，集中维护字段检查统计口径。

    参数:
        document: 原始文档记录。
        archive_document: 档案生命周期扩展记录。
        field_values: 当前文档的字段值。
        index_context_chunk_count: 索引上下文 Chunk 数量。
    """
    return ProcessDocumentRead(
        id=document.id,
        filename=document.filename,
        file_hash=document.file_hash,
        status=archive_document.status,
        last_error=LastErrorRead(
            code=archive_document.last_error_code,
            message=archive_document.last_error_summary,
        ),
        field_summary=FieldSummaryRead(
            checked_count=sum(
                value.review_status != FieldReviewStatus.PENDING_CHECK
                for value in field_values
            ),
            total_count=len(_FIELD_NAMES),
        ),
        confirmed_at=archive_document.confirmed_at,
        version=archive_document.version,
        uploaded_at=document.created_at,
        updated_at=document.updated_at,
        index_context_chunk_count=index_context_chunk_count,
    )


def build_archive_draft_read(
    *,
    document: Document,
    archive_document: ArchiveDocument,
    field_values: Sequence[ArchiveFieldValue],
    session: Session,
) -> ArchiveDraftRead:
    """构造草稿详情及字段证据嵌套响应。

    参数:
        document: 原始文档记录。
        archive_document: 档案生命周期扩展记录。
        field_values: 当前文档字段值。
        session: 当前数据库会话。
    """
    evidence_by_field: dict[UUID, list[FieldEvidence]] = {}
    if field_values:
        evidences = session.exec(
            select(FieldEvidence).where(
                FieldEvidence.field_value_id.in_([value.id for value in field_values])
            )
        ).all()
        for evidence in evidences:
            evidence_by_field.setdefault(evidence.field_value_id, []).append(evidence)

    snapshot = (
        session.get(ParsedSnapshot, archive_document.current_snapshot_id)
        if archive_document.current_snapshot_id is not None
        else None
    )
    return ArchiveDraftRead(
        document=build_process_document_read(
            document=document,
            archive_document=archive_document,
            field_values=field_values,
        ),
        fields=[
            FieldDraftRead(
                id=value.id,
                field_name=value.field_name,
                text_value=value.text_value,
                date_value=value.date_value,
                json_value=value.json_value,
                review_status=value.review_status,
                source=value.source,
                no_source_evidence=value.no_source_evidence,
                updated_by=value.updated_by,
                updated_at=value.updated_at,
                evidences=[
                    FieldEvidenceRead.model_validate(evidence)
                    for evidence in evidence_by_field.get(value.id, [])
                ],
            )
            for value in field_values
        ],
        snapshot=ParsedSnapshotRead.model_validate(snapshot) if snapshot else None,
        next_actions=(
            ["UPDATE_FIELDS"] if archive_document.status in _EDITABLE_STATUSES else []
        ),
    )


def list_visibility_blocked_document_ids(
    project_id: UUID, session: Session
) -> set[UUID]:
    """读取项目内存在可见性阻断操作的文档集合。

    参数:
        project_id: 项目身份。
        session: 当前数据库会话。
    """
    rows = session.exec(
        select(ArchiveOperation.document_id)
        .join(Document, Document.id == ArchiveOperation.document_id)
        .where(
            Document.project_id == project_id,
            ArchiveOperation.visibility_blocking,
            ArchiveOperation.operation_status.in_(
                (ArchiveOperationStatus.RUNNING, ArchiveOperationStatus.FAILED)
            ),
        )
    ).all()
    return set(rows)
