"""实现 AV1-P10 档案与项目清单的建议、人工确认和删除关联。"""

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    ChecklistItem,
    ChecklistLink,
    ChecklistLinkStatus,
    Document,
    Project,
    utc_now,
)
from app.schemas.archive_catalog import (
    ChecklistLinkCreate,
    ChecklistLinkListRead,
    ChecklistLinkRead,
    ChecklistLinkSuggestionListRead,
    ChecklistLinkSuggestionRead,
)
from app.services.archive.reads import list_archive_field_values


CHECKLIST_LINK_CONFIRMED = "CHECKLIST_LINK_CONFIRMED"
CHECKLIST_LINK_DELETED = "CHECKLIST_LINK_DELETED"
CHECKLIST_LINK_RESOURCE_TYPE = "CHECKLIST_LINK"


def _field_text(document_id: UUID, field_name: ArchiveFieldName, session: Session):
    """读取用于匹配清单的单个归档字段。

    Args:
        document_id: 档案文档身份。
        field_name: 要读取的档案字段名称。
        session: 当前数据库会话。
    """
    field = session.exec(
        select(ArchiveFieldValue).where(
            ArchiveFieldValue.document_id == document_id,
            ArchiveFieldValue.field_name == field_name,
        )
    ).first()
    if field is None:
        return None
    return field.text_value


def list_link_suggestions(*, document: Document, session: Session) -> ChecklistLinkSuggestionListRead:
    """按资料类型和项目阶段生成建议，不自动创建关联。

    Args:
        document: 已通过项目范围校验的文档记录。
        session: 当前数据库会话。

    Returns:
        与文档已确认字段匹配的清单建议，以及当前是否已关联的标记。
    """
    if document.project_id is None:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")
    document_type = _field_text(document.id, ArchiveFieldName.DOCUMENT_TYPE, session)
    project_stage = _field_text(document.id, ArchiveFieldName.PROJECT_STAGE, session)
    if document_type is None or project_stage is None:
        return ChecklistLinkSuggestionListRead(items=[])
    items = session.exec(
        select(ChecklistItem)
        .where(
            ChecklistItem.project_id == document.project_id,
            ChecklistItem.document_type == document_type,
            ChecklistItem.project_stage == project_stage,
        )
        .order_by(ChecklistItem.updated_at.desc(), ChecklistItem.id.desc())
    ).all()
    confirmed_link_item_ids = set(
        session.exec(
            select(ChecklistLink.checklist_item_id).where(
                ChecklistLink.document_id == document.id,
                ChecklistLink.status == ChecklistLinkStatus.CONFIRMED,
            )
        ).all()
    )
    return ChecklistLinkSuggestionListRead(
        items=[
            ChecklistLinkSuggestionRead(
                checklist_item_id=item.id,
                name=item.name,
                document_type=item.document_type,
                project_stage=item.project_stage,
                is_required=item.is_required,
                already_linked=item.id in confirmed_link_item_ids,
            )
            for item in items
        ]
    )


def list_document_links(*, document: Document, session: Session) -> ChecklistLinkListRead:
    """列出一份项目文档已有的确认或失效关联。

    Args:
        document: 已通过项目范围校验的文档记录。
        session: 当前数据库会话。

    Returns:
        按创建时间倒序排列的关联历史投影。
    """
    links = session.exec(
        select(ChecklistLink)
        .where(ChecklistLink.document_id == document.id)
        .order_by(ChecklistLink.created_at.desc(), ChecklistLink.id.desc())
    ).all()
    return ChecklistLinkListRead(items=[ChecklistLinkRead.model_validate(link) for link in links])


def create_document_link(
    *,
    document: Document,
    actor_id: UUID,
    payload: ChecklistLinkCreate,
    session: Session,
) -> ChecklistLinkRead:
    """原子确认同项目档案—清单关联并写入脱敏审计。

    Args:
        document: 已通过项目范围校验的档案文档。
        actor_id: 已认证执行确认的用户身份。
        payload: 包含清单项身份和两侧版本号的确认请求。
        session: 当前数据库事务会话。

    Returns:
        已提交并刷新后的关联记录。

    Raises:
        AppError: 档案/清单项越权、版本冲突、状态不允许或保存失败。
    """
    # 锁定档案、清单项和已有关联后再做版本与状态判断；这样两个并发确认不会
    # 同时通过“未关联”检查，也不会把旧版本的客户端写入覆盖到新状态上。
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document.id)
        .with_for_update()
    ).first()
    if archive_document is None or archive_document.status != ArchiveDocumentStatus.CONFIRMED:
        raise AppError(409, "CHECKLIST_LINK_NOT_ALLOWED", "只有已确认档案可以建立关联。")
    if archive_document.version != payload.expected_document_version:
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取档案。")
    if document.project_id is None:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")
    item = session.exec(
        select(ChecklistItem)
        .where(ChecklistItem.id == payload.checklist_item_id)
        .with_for_update()
    ).first()
    if item is None or item.project_id != document.project_id:
        raise AppError(404, "CHECKLIST_ITEM_NOT_FOUND", "清单项不存在或不属于当前项目。")
    if item.version != payload.expected_checklist_item_version:
        raise AppError(409, "VERSION_CONFLICT", "清单项版本已变化，请重新读取。")
    existing = session.exec(
        select(ChecklistLink)
        .where(
            ChecklistLink.document_id == document.id,
            ChecklistLink.checklist_item_id == item.id,
        )
        .with_for_update()
    ).first()
    if existing is not None and existing.status == ChecklistLinkStatus.CONFIRMED:
        raise AppError(409, "CHECKLIST_LINK_NOT_ALLOWED", "该档案与清单项已经关联。")
    now = utc_now()
    if existing is None:
        link = ChecklistLink(
            document_id=document.id,
            checklist_item_id=item.id,
            status=ChecklistLinkStatus.CONFIRMED,
            confirmed_by=actor_id,
            confirmed_at=now,
        )
        session.add(link)
    else:
        link = existing
        link.status = ChecklistLinkStatus.CONFIRMED
        link.confirmed_by = actor_id
        link.confirmed_at = now
        link.invalidated_at = None
        link.invalidated_reason = None
        link.version += 1
        link.updated_at = now
        session.add(link)
    session.add(
        ArchiveAuditLog(
            project_id=document.project_id,
            actor_id=actor_id,
            operation_type=CHECKLIST_LINK_CONFIRMED,
            resource_type=CHECKLIST_LINK_RESOURCE_TYPE,
            resource_id=link.id,
            redacted_summary={"status": ChecklistLinkStatus.CONFIRMED.value},
        )
    )
    try:
        # 关联事实和脱敏审计必须同事务提交；只保存其中一项会造成用户看到的状态
        # 与审计记录不一致，且无法从审计判断这次操作是否真正生效。
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "CHECKLIST_LINK_CREATE_FAILED", "清单关联保存失败。") from exc
    session.refresh(link)
    return ChecklistLinkRead.model_validate(link)


def delete_document_link(
    *,
    document: Document,
    link_id: UUID,
    actor_id: UUID,
    session: Session,
) -> None:
    """删除一条项目内关联并保留脱敏删除审计。

    Args:
        document: 已通过项目范围校验的档案文档。
        link_id: 待删除的项目内关联身份。
        actor_id: 已认证执行删除的用户身份。
        session: 当前数据库事务会话。

    Raises:
        AppError: 关联不存在、关联不属于当前文档或保存失败。
    """
    link = session.get(ChecklistLink, link_id)
    if link is None or link.document_id != document.id or document.project_id is None:
        raise AppError(404, "CHECKLIST_LINK_NOT_FOUND", "清单关联不存在或不属于当前文档。")
    session.delete(link)
    session.add(
        ArchiveAuditLog(
            project_id=document.project_id,
            actor_id=actor_id,
            operation_type=CHECKLIST_LINK_DELETED,
            resource_type=CHECKLIST_LINK_RESOURCE_TYPE,
            resource_id=link_id,
            redacted_summary={},
        )
    )
    try:
        # 删除事实与脱敏审计一起提交，避免删除成功却没有可追踪事件，或审计成功但
        # 关联仍然可见的半完成状态。
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "CHECKLIST_LINK_DELETE_FAILED", "清单关联删除失败。") from exc
