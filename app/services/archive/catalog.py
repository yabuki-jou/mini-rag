"""实现 AV1-P10 文档处理列表、正式档案目录和脱敏审计查询。"""

from dataclasses import dataclass
from datetime import date
from enum import Enum
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
    FieldEvidence,
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
    """把七个字段按固定名称索引，供目录筛选和摘要构建复用。

    Args:
        document_id: 要读取字段值的文档身份。
        session: 当前数据库会话。
    """
    return {field.field_name: field for field in list_archive_field_values(document_id, session)}


def _enum_value(field: ArchiveFieldValue | None, enum_type):
    """把数据库文本字段安全转换为固定字典值。

    Args:
        field: 待转换的档案字段值，可以为空。
        enum_type: 目标枚举类型。
    """
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
    """组合正式目录中的结构化档案摘要，不返回原文正文。

    Args:
        document: 原始文档记录。
        archive_document: 文档对应的档案生命周期记录。
        fields: 按字段名索引的当前字段值。
    """
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
    """分页返回项目内全部仍存在的归档文档处理状态。

    Args:
        project_id: 已由路由依赖确认的项目身份。
        page: 从 1 开始的页码。
        page_size: 单页数量。
        status: 可选的档案生命周期状态筛选。
        session: 当前数据库会话。

    Returns:
        按更新时间和文档 ID 稳定排序的处理状态分页投影。
    """
    statement = (
        select(Document, ArchiveDocument)
        .join(ArchiveDocument, ArchiveDocument.document_id == Document.id)
        .where(Document.project_id == project_id)
        .order_by(ArchiveDocument.updated_at.desc(), Document.id.desc())
    )
    if status is not None:
        statement = statement.where(ArchiveDocument.status == status)
    rows = list(session.exec(statement).all())
    # 先得到同一查询结果的总数再切片，保证 total 与当前页来自同一组项目范围数据；
    # 这里的分页是服务层投影分页，不把客户端提交的偏移条件直接交给数据库。
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
    """只返回 CONFIRMED 且没有可见性阻断的正式档案，并执行结构化筛选。

    Args:
        project_id: 已由服务端绑定的项目身份。
        page: 从 1 开始的页码。
        page_size: 单页数量。
        document_type: 可选的资料类型筛选。
        project_stage: 可选的项目阶段筛选。
        document_date_from: 可选的文档日期下界。
        document_date_to: 可选的文档日期上界。
        document_date_is_null: 是否只返回未登记文档日期的档案。
        authoring_organization: 可选的编制单位筛选。
        session: 当前数据库会话。

    Returns:
        包含契约声明的文档标识和登记字段、但不含原文正文的正式目录分页投影。

    Raises:
        AppError: 日期筛选组合不合法。
    """
    if document_date_is_null and (document_date_from is not None or document_date_to is not None):
        raise AppError(422, "VALIDATION_ERROR", "日期为空筛选不能与日期区间同时使用。")
    if document_date_from is not None and document_date_to is not None and document_date_from > document_date_to:
        raise AppError(422, "VALIDATION_ERROR", "日期区间起点不能晚于终点。")

    rows = _formal_archive_rows(
        project_id=project_id,
        document_type=document_type,
        project_stage=project_stage,
        document_date_from=document_date_from,
        document_date_to=document_date_to,
        document_date_is_null=document_date_is_null,
        authoring_organization=authoring_organization,
        session=session,
    )
    filtered: list[ArchiveSummaryRead] = []
    for document, archive_document, fields in rows:
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


def _formal_archive_rows(
    *,
    project_id: UUID,
    document_type: ArchiveDocumentType | None,
    project_stage: ProjectStage | None,
    document_date_from: date | None,
    document_date_to: date | None,
    document_date_is_null: bool,
    authoring_organization: str | None,
    session: Session,
) -> list[tuple[Document, ArchiveDocument, dict[ArchiveFieldName, ArchiveFieldValue]]]:
    """共享正式目录的范围、阻断、筛选和稳定排序谓词。

    Args:
        project_id: 当前项目身份。
        document_type: 可选的资料类型筛选。
        project_stage: 可选的项目阶段筛选。
        document_date_from: 可选的文档日期下界。
        document_date_to: 可选的文档日期上界。
        document_date_is_null: 是否只保留未登记文档日期的档案。
        authoring_organization: 可选的编制单位筛选。
        session: 当前数据库会话。
    """
    # 可见性阻断先按项目整体取出，再与 CONFIRMED 查询结果交集；这样目录、助手
    # 和详情入口共享同一“删除/失败操作期间不可见”的业务口径。
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
    filtered: list[tuple[Document, ArchiveDocument, dict[ArchiveFieldName, ArchiveFieldValue]]] = []
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
        filtered.append((document, archive_document, fields))
    return filtered


def list_agent_confirmed_document_titles(
    *,
    project_id: UUID,
    document_ids: set[UUID],
    session: Session,
) -> dict[UUID, str | None]:
    """读取当前项目可见正式档案的已确认标题，供证据候选绑定文档身份。

    Args:
        project_id: 已由服务端鉴权并绑定到档案助手会话的项目 ID。
        document_ids: 已通过正式检索初步范围校验的候选文档 ID。
        session: 当前短生命周期数据库 Session。

    Returns:
        仍处于可见正式范围的候选文档及其可空标题；不在范围内的 ID 不返回。
    """
    if not document_ids:
        return {}
    rows = _formal_archive_rows(
        project_id=project_id,
        document_type=None,
        project_stage=None,
        document_date_from=None,
        document_date_to=None,
        document_date_is_null=False,
        authoring_organization=None,
        session=session,
    )
    titles: dict[UUID, str | None] = {}
    for document, _, fields in rows:
        if document.id not in document_ids:
            continue
        title = archive_field_value(fields.get(ArchiveFieldName.TITLE))
        titles[document.id] = str(title) if title is not None else None
    return titles


_AGENT_CATALOG_FIELDS: tuple[ArchiveFieldName, ...] = (
    ArchiveFieldName.TITLE,
    ArchiveFieldName.DOCUMENT_TYPE,
    ArchiveFieldName.DOCUMENT_DATE,
    ArchiveFieldName.AUTHORING_ORGANIZATION,
    ArchiveFieldName.PROJECT_STAGE,
)


@dataclass(frozen=True)
class AgentCatalogPage:
    """保存不含持久化标识的目录工具安全投影。

    Attributes:
        page: 当前目录页码。
        page_size: 当前目录页大小。
        total: 当前项目可见正式档案总数。
        items: 经过脱敏处理的目录条目列表。
    """

    page: int
    page_size: int
    total: int
    items: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        """转换为可写入 ToolMessage 的纯 JSON 数据。"""
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "items": [dict(item) for item in self.items],
        }


def _safe_field_value(
    field: ArchiveFieldValue | None,
    *,
    current_snapshot_id: UUID | None,
    session: Session,
) -> dict[str, object]:
    """投影目录字段值、来源和当前快照证据标记。

    Args:
        field: 待投影的档案字段值，可以为空。
        current_snapshot_id: 当前正式解析快照身份。
        session: 当前数据库会话。
    """
    value = archive_field_value(field)
    if isinstance(value, Enum):
        value = value.value
    elif isinstance(value, date):
        value = value.isoformat()
    has_source_evidence = False
    if field is not None and current_snapshot_id is not None and not field.no_source_evidence:
        # evidence 必须属于当前快照；旧快照证据不能证明当前正式版本的字段来源。
        has_source_evidence = session.exec(
            select(FieldEvidence.id)
            .where(
                FieldEvidence.field_value_id == field.id,
                FieldEvidence.snapshot_id == current_snapshot_id,
            )
            .limit(1)
        ).first() is not None
    return {
        "value": value,
        "source": field.source.value if field is not None and field.source is not None else None,
        "has_source_evidence": has_source_evidence,
    }


def list_agent_formal_archives(
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
) -> AgentCatalogPage:
    """返回档案助手专用的正式目录脱敏投影。

    Args:
        project_id: 已由服务端绑定的项目身份，不能由模型覆盖。
        page: 从 1 开始的目录页码。
        page_size: 单页数量，受工具白名单限制为 1 到 20。
        document_type: 可选的资料类型筛选。
        project_stage: 可选的项目阶段筛选。
        document_date_from: 可选的文档日期下界。
        document_date_to: 可选的文档日期上界。
        document_date_is_null: 是否只返回未登记文档日期的档案。
        authoring_organization: 可选的编制单位筛选。
        session: 当前数据库会话。

    Returns:
        只含五个登记字段、文件名和临时 ``document_ref`` 的安全目录投影。

    Raises:
        AppError: 工具分页或日期筛选参数不合法。
    """
    if page < 1:
        raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "页码必须为正整数。")
    if page_size < 1 or page_size > 20:
        raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "页大小必须在 1 到 20 之间。")
    if document_date_is_null and (document_date_from is not None or document_date_to is not None):
        raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "日期为空筛选不能与日期区间同时使用。")
    if document_date_from is not None and document_date_to is not None and document_date_from > document_date_to:
        raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "日期区间起点不能晚于终点。")
    rows = _formal_archive_rows(
        project_id=project_id,
        document_type=document_type,
        project_stage=project_stage,
        document_date_from=document_date_from,
        document_date_to=document_date_to,
        document_date_is_null=document_date_is_null,
        authoring_organization=authoring_organization,
        session=session,
    )
    total = len(rows)
    start = (page - 1) * page_size
    items: list[dict[str, object]] = []
    # document_ref 只在本次目录响应中按页内顺序生成，不暴露 Document UUID，也不
    # 允许后续证据工具把它当成持久化查询标识。
    for document, archive_document, fields in rows[start : start + page_size]:
        items.append(
            {
                "document_ref": f"A{len(items) + 1}",
                "filename": document.filename,
                "confirmed_at": archive_document.confirmed_at.isoformat()
                if archive_document.confirmed_at is not None
                else None,
                "fields": {
                    field_name.value: _safe_field_value(
                        fields.get(field_name),
                        current_snapshot_id=archive_document.current_snapshot_id,
                        session=session,
                    )
                    for field_name in _AGENT_CATALOG_FIELDS
                },
            }
        )
    return AgentCatalogPage(page=page, page_size=page_size, total=total, items=items)


def render_agent_catalog_text(page: AgentCatalogPage) -> str:
    """按固定分页和五字段模板生成目录正文。

    Args:
        page: 已完成范围过滤和脱敏投影的目录页。

    Returns:
        可写入 ToolMessage 的稳定目录文本，不包含持久化内部标识。
    """
    if not page.items:
        return "当前项目没有可见的正式档案。"
    field_labels = (
        (ArchiveFieldName.TITLE, "标题"),
        (ArchiveFieldName.DOCUMENT_TYPE, "资料类型"),
        (ArchiveFieldName.DOCUMENT_DATE, "文档日期"),
        (ArchiveFieldName.AUTHORING_ORGANIZATION, "编制单位"),
        (ArchiveFieldName.PROJECT_STAGE, "项目阶段"),
    )
    lines = [f"第 {page.page} 页，本页 {len(page.items)} 份，共 {page.total} 份："]
    for index, item in enumerate(page.items, start=1):
        fields = item["fields"]
        field_text = []
        for field_name, label in field_labels:
            field = fields[field_name.value]
            value = field["value"] if field["value"] is not None else "未登记"
            source = field["source"] if field["source"] is not None else "null"
            evidence = "true" if field["has_source_evidence"] else "false"
            field_text.append(
                f"{label}：{value}（source={source}, has_source_evidence={evidence}）"
            )
        lines.append(f"{index}. 文件名：{item['filename']}；" + "；".join(field_text))
    return "\n".join(lines)


def read_formal_archive(*, document: Document, session: Session) -> ArchiveDetailRead:
    """读取单份正式档案详情；非正式或被阻断文档统一隐藏。

    Args:
        document: 已通过项目依赖确认归属的文档记录。
        session: 当前数据库会话。

    Returns:
        当前正式档案的结构化字段和快照证据详情，不返回原文件正文。

    Raises:
        AppError: 文档不在正式可见范围。
    """
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
