"""定义可恢复归档操作和脱敏业务审计模型。"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.archive import ARCHIVE_JSON
from app.models.common import utc_now


class ArchiveOperationType(str, Enum):
    """可恢复的内部操作类型。"""

    PARSE = "PARSE"
    SUGGEST = "SUGGEST"
    INDEX = "INDEX"
    DELETE = "DELETE"


class ArchiveOperationStatus(str, Enum):
    """内部操作的执行状态。"""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ArchiveOperation(SQLModel, table=True):
    """可恢复的内部操作和删除期间的正式可见性阻断。

    Attributes:
        id: 内部操作的全局唯一标识。
        document_id: 操作作用的归档文档 ID。
        operation_type: 解析、建议、索引或删除等内部操作类型。
        operation_status: 操作当前的运行、成功或失败状态。
        visibility_blocking: 删除未完成时阻断该文档进入正式范围的标记。
        attempt_no: 当前操作类型的执行尝试次数，从 1 开始。
        last_completed_step: 跨 PostgreSQL、文件系统和 Chroma 恢复时的最后完成步骤。
        failure_code: 可安全记录和返回的最近一次受控失败代码。
        failure_summary: 不含内部异常细节的最近一次失败摘要。
        started_at: 操作开始执行的 UTC 时间。
        finished_at: 操作成功或失败结束的 UTC 时间。
        created_at: 操作记录创建的 UTC 时间。
        updated_at: 操作记录最后更新的 UTC 时间。
    """

    __tablename__ = "archive_operations"
    __table_args__ = (
        CheckConstraint("attempt_no >= 1", name="ck_archive_operations_attempt_no"),
        CheckConstraint("visibility_blocking = FALSE OR operation_type = 'DELETE'", name="ck_archive_operations_visibility_blocking"),
        ForeignKeyConstraint(["document_id"], ["archive_documents.document_id"], ondelete="CASCADE"),
        Index("ix_archive_operations_status_updated", "operation_status", "updated_at"),
        Index("ix_archive_operations_document_type_created", "document_id", "operation_type", "created_at"),
        # 部分唯一索引允许同一文档保留历史记录，但同一时刻只能有一个运行中操作。
        Index("uq_archive_operations_document_running", "document_id", unique=True, postgresql_where=text("operation_status = 'RUNNING'"), sqlite_where=text("operation_status = 'RUNNING'")),
        # 每份文档只保留一个未完成删除操作，供服务层执行可见性阻断和失败重试。
        Index("uq_archive_operations_delete_unfinished", "document_id", unique=True, postgresql_where=text("operation_type = 'DELETE' AND operation_status IN ('RUNNING', 'FAILED')"), sqlite_where=text("operation_type = 'DELETE' AND operation_status IN ('RUNNING', 'FAILED')")),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(index=True)
    operation_type: ArchiveOperationType = Field()
    operation_status: ArchiveOperationStatus = Field()
    visibility_blocking: bool = Field(default=False)
    attempt_no: int = Field(default=1, ge=1)
    last_completed_step: str | None = Field(default=None, max_length=64)
    failure_code: str | None = Field(default=None, max_length=64)
    failure_summary: str | None = Field(default=None, max_length=500)
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ArchiveAuditLog(SQLModel, table=True):
    """可查询、脱敏且不依赖被删除资源存在的业务审计。

    Attributes:
        id: 审计记录全局唯一标识。
        project_id: 审计所属项目 ID，用于所有权过滤和时间分页。
        actor_id: 执行该业务操作的用户 ID。
        operation_type: 面向审计查询的固定操作名称，不等同于内部操作类型。
        resource_type: 被操作资源的类型，例如项目、清单项或归档文档。
        resource_id: 被操作资源的 ID；资源删除后仍保留此标识。
        operation_id: 可选内部操作 ID，用于避免同一成功操作重复写审计。
        redacted_summary: 不含原文、提示词和敏感字段的 JSON 操作摘要。
        created_at: 审计记录创建的 UTC 时间。
    """

    __tablename__ = "archive_audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        UniqueConstraint("operation_id", name="uq_archive_audit_logs_operation_id"),
        Index("ix_archive_audit_logs_project_created_id", "project_id", "created_at", "id"),
        Index("ix_archive_audit_logs_actor_created", "actor_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(index=True)
    actor_id: UUID = Field(foreign_key="users.id", index=True)
    operation_type: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: UUID = Field(index=True)
    operation_id: UUID | None = Field(default=None, unique=True)
    redacted_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(ARCHIVE_JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
