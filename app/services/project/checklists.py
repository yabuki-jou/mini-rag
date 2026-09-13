"""实现 FR-031 清单项读取与 PostgreSQL 派生状态计算。"""

from uuid import UUID

from sqlalchemy import delete, func, update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ChecklistItem,
    ChecklistLink,
    ChecklistLinkStatus,
    Document,
    Project,
    utc_now,
)
from app.schemas import (
    ChecklistFulfillmentStatus,
    ChecklistItemCreate,
    ChecklistItemCreateResponse,
    ChecklistItemListRead,
    ChecklistItemRead,
    ChecklistItemUpdate,
)


CHECKLIST_ITEM_CREATED = "CHECKLIST_ITEM_CREATED"
CHECKLIST_ITEM_UPDATED = "CHECKLIST_ITEM_UPDATED"
CHECKLIST_ITEM_DELETED = "CHECKLIST_ITEM_DELETED"
CHECKLIST_ITEM_RESOURCE_TYPE = "CHECKLIST_ITEM"
CHECKLIST_LINK_INVALIDATED_BY_ITEM_UPDATE = "CHECKLIST_MATCHING_FIELDS_CHANGED"


def create_checklist_item(
    *,
    project_id: UUID,
    actor_id: UUID,
    payload: ChecklistItemCreate,
    session: Session,
) -> ChecklistItemCreateResponse:
    """原子创建清单项、递增项目版本并写入脱敏审计。

    项目版本更新带有 ``expected_project_version`` 条件。只有该条件命中时，才会在
    同一事务中继续新增清单项和审计记录，从而阻止陈旧页面产生重复创建结果。
    """
    checklist_item = ChecklistItem(
        project_id=project_id,
        name=payload.name,
        document_type=payload.document_type,
        is_required=payload.is_required,
        project_stage=payload.project_stage,
        description=payload.description,
    )
    audit_log = ArchiveAuditLog(
        project_id=project_id,
        actor_id=actor_id,
        operation_type=CHECKLIST_ITEM_CREATED,
        resource_type=CHECKLIST_ITEM_RESOURCE_TYPE,
        resource_id=checklist_item.id,
        # 不写入清单名称或说明，避免把用户维护的业务文本扩散到审计表。
        redacted_summary={
            "document_type": payload.document_type.value,
            "is_required": payload.is_required,
        },
    )

    try:
        version_update = session.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.version == payload.expected_project_version,
            )
            .values(
                version=Project.version + 1,
                updated_at=utc_now(),
            )
        )
        if version_update.rowcount != 1:
            session.rollback()
            raise AppError(409, "VERSION_CONFLICT", "项目已被其他操作修改，请刷新后重试。")

        session.add(checklist_item)
        session.add(audit_log)
        session.commit()
    except AppError:
        raise
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "CHECKLIST_ITEM_CREATE_FAILED", "清单项创建失败。") from exc

    session.refresh(checklist_item)
    return ChecklistItemCreateResponse(
        item=_build_checklist_item_read(item=checklist_item, confirmed_document_count=0),
        project_version=payload.expected_project_version + 1,
    )


def list_checklist_items(*, project_id: UUID, session: Session) -> ChecklistItemListRead:
    """读取一个项目的清单，并实时计算每项的满足状态。

    只有同项目中同时处于 ``CONFIRMED`` 的归档文档和人工确认关联才能计数。
    因此，资料类型、项目阶段、待确认档案或失效关联均不会被误判为满足。
    """
    confirmed_document_counts = (
        select(
            ChecklistLink.checklist_item_id.label("checklist_item_id"),
            func.count(func.distinct(ChecklistLink.document_id)).label("confirmed_document_count"),
        )
        .join(ArchiveDocument, ArchiveDocument.document_id == ChecklistLink.document_id)
        .join(Document, Document.id == ArchiveDocument.document_id)
        .where(
            ChecklistLink.status == ChecklistLinkStatus.CONFIRMED,
            ArchiveDocument.status == ArchiveDocumentStatus.CONFIRMED,
            Document.project_id == project_id,
        )
        .group_by(ChecklistLink.checklist_item_id)
        .subquery()
    )
    statement = (
        select(
            ChecklistItem,
            func.coalesce(confirmed_document_counts.c.confirmed_document_count, 0).label(
                "confirmed_document_count"
            ),
        )
        .outerjoin(
            confirmed_document_counts,
            confirmed_document_counts.c.checklist_item_id == ChecklistItem.id,
        )
        .where(ChecklistItem.project_id == project_id)
        .order_by(ChecklistItem.updated_at.desc(), ChecklistItem.id.desc())
    )
    rows = session.exec(statement).all()
    return ChecklistItemListRead(
        items=[
            _build_checklist_item_read(item=item, confirmed_document_count=int(confirmed_document_count))
            for item, confirmed_document_count in rows
        ]
    )


def update_checklist_item(
    *,
    project_id: UUID,
    actor_id: UUID,
    item_id: UUID,
    payload: ChecklistItemUpdate,
    session: Session,
) -> ChecklistItemRead:
    """以清单项乐观锁更新字段，并在匹配条件变化时使关联失效。"""
    checklist_item = _read_project_checklist_item(
        project_id=project_id,
        item_id=item_id,
        session=session,
    )
    if checklist_item.version != payload.expected_version:
        raise AppError(409, "VERSION_CONFLICT", "清单项已被其他操作修改，请刷新后重试。")

    mutable_fields = payload.model_fields_set
    matching_fields_changed = (
        ("name" in mutable_fields and payload.name != checklist_item.name)
        or ("document_type" in mutable_fields and payload.document_type != checklist_item.document_type)
        or ("project_stage" in mutable_fields and payload.project_stage != checklist_item.project_stage)
    )
    if "name" in mutable_fields:
        assert payload.name is not None
        checklist_item.name = payload.name
    if "document_type" in mutable_fields:
        assert payload.document_type is not None
        checklist_item.document_type = payload.document_type
    if "is_required" in mutable_fields:
        assert payload.is_required is not None
        checklist_item.is_required = payload.is_required
    if "project_stage" in mutable_fields:
        assert payload.project_stage is not None
        checklist_item.project_stage = payload.project_stage
    if "description" in mutable_fields:
        checklist_item.description = payload.description

    now = utc_now()
    checklist_item.version += 1
    checklist_item.updated_at = now
    audit_log = ArchiveAuditLog(
        project_id=project_id,
        actor_id=actor_id,
        operation_type=CHECKLIST_ITEM_UPDATED,
        resource_type=CHECKLIST_ITEM_RESOURCE_TYPE,
        resource_id=checklist_item.id,
        redacted_summary={
            "matching_fields_changed": matching_fields_changed,
            "is_required": checklist_item.is_required,
        },
    )

    try:
        if matching_fields_changed:
            # 保留原确认人和确认时间作为历史信息；仅使该关联不再满足当前清单条件。
            session.execute(
                update(ChecklistLink)
                .where(
                    ChecklistLink.checklist_item_id == checklist_item.id,
                    ChecklistLink.status == ChecklistLinkStatus.CONFIRMED,
                )
                .values(
                    status=ChecklistLinkStatus.INVALIDATED,
                    invalidated_at=now,
                    invalidated_reason=CHECKLIST_LINK_INVALIDATED_BY_ITEM_UPDATE,
                    version=ChecklistLink.version + 1,
                    updated_at=now,
                )
            )
        session.add(checklist_item)
        session.add(audit_log)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "CHECKLIST_ITEM_UPDATE_FAILED", "清单项修改失败。") from exc

    session.refresh(checklist_item)
    return _build_checklist_item_read(
        item=checklist_item,
        confirmed_document_count=_read_confirmed_document_count(
            project_id=project_id,
            item_id=checklist_item.id,
            session=session,
        ),
    )


def delete_checklist_item(
    *,
    project_id: UUID,
    actor_id: UUID,
    item_id: UUID,
    session: Session,
) -> None:
    """删除项目清单项及其关联，并保留不依赖该项存在的删除审计。"""
    checklist_item = _read_project_checklist_item(
        project_id=project_id,
        item_id=item_id,
        session=session,
    )
    audit_log = ArchiveAuditLog(
        project_id=project_id,
        actor_id=actor_id,
        operation_type=CHECKLIST_ITEM_DELETED,
        resource_type=CHECKLIST_ITEM_RESOURCE_TYPE,
        resource_id=checklist_item.id,
        redacted_summary={},
    )

    try:
        # 显式删除使 SQLite 测试和 PostgreSQL 均能证明关联已清理，不仅依赖外键级联。
        session.execute(
            delete(ChecklistLink).where(ChecklistLink.checklist_item_id == checklist_item.id)
        )
        session.delete(checklist_item)
        session.add(audit_log)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "CHECKLIST_ITEM_DELETE_FAILED", "清单项删除失败。") from exc


def _read_project_checklist_item(
    *,
    project_id: UUID,
    item_id: UUID,
    session: Session,
) -> ChecklistItem:
    """读取已验证项目范围内的清单项，避免跨项目资源泄露。"""
    checklist_item = session.get(ChecklistItem, item_id)
    if checklist_item is None or checklist_item.project_id != project_id:
        raise AppError(404, "CHECKLIST_ITEM_NOT_FOUND", "清单项不存在或不属于当前项目。")
    return checklist_item


def _read_confirmed_document_count(
    *,
    project_id: UUID,
    item_id: UUID,
    session: Session,
) -> int:
    """计算一个清单项当前仍有效的已确认档案数量。"""
    statement = (
        select(func.count(func.distinct(ChecklistLink.document_id)))
        .join(ArchiveDocument, ArchiveDocument.document_id == ChecklistLink.document_id)
        .join(Document, Document.id == ArchiveDocument.document_id)
        .where(
            ChecklistLink.checklist_item_id == item_id,
            ChecklistLink.status == ChecklistLinkStatus.CONFIRMED,
            ArchiveDocument.status == ArchiveDocumentStatus.CONFIRMED,
            Document.project_id == project_id,
        )
    )
    return int(session.exec(statement).one())


def _build_checklist_item_read(
    *,
    item: ChecklistItem,
    confirmed_document_count: int,
) -> ChecklistItemRead:
    """将数据库行和实时计数转换为稳定 HTTP 响应。"""
    if confirmed_document_count > 0:
        fulfillment_status = ChecklistFulfillmentStatus.SATISFIED
    elif item.is_required:
        fulfillment_status = ChecklistFulfillmentStatus.MISSING
    else:
        fulfillment_status = ChecklistFulfillmentStatus.NOT_PROVIDED

    return ChecklistItemRead(
        id=item.id,
        name=item.name,
        document_type=item.document_type,
        is_required=item.is_required,
        project_stage=item.project_stage,
        description=item.description,
        fulfillment_status=fulfillment_status,
        confirmed_document_count=confirmed_document_count,
        version=item.version,
    )
