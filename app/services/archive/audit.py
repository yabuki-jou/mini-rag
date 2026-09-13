"""提供智慧档案脱敏审计日志查询服务。"""

from uuid import UUID

from sqlmodel import Session, select

from app.models import ArchiveAuditLog
from app.schemas.archive_catalog import (
    ArchiveAuditOperationType,
    AuditLogPageRead,
    AuditLogRead,
)


def list_audit_logs(
    *,
    project_id: UUID,
    page: int,
    page_size: int,
    operation_type: ArchiveAuditOperationType | None,
    session: Session,
) -> AuditLogPageRead:
    """分页返回当前项目的脱敏业务审计。

    参数:
        project_id: 项目身份。
        page: 从一开始的页码。
        page_size: 单页数量。
        operation_type: 可选的审计操作筛选。
        session: 当前数据库会话。
    """
    statement = select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project_id)
    if operation_type is not None:
        statement = statement.where(ArchiveAuditLog.operation_type == operation_type)
    rows = list(
        session.exec(
            statement.order_by(
                ArchiveAuditLog.created_at.desc(), ArchiveAuditLog.id.desc()
            )
        ).all()
    )
    total = len(rows)
    start = (page - 1) * page_size
    return AuditLogPageRead(
        items=[AuditLogRead.model_validate(row) for row in rows[start : start + page_size]],
        page=page,
        page_size=page_size,
        total=total,
    )
