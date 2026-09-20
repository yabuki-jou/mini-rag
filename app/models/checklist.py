"""定义项目资料清单和人工确认关联模型。"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.archive import ArchiveDocumentType, ProjectStage
from app.models.common import utc_now


class ChecklistLinkStatus(str, Enum):
    """档案—清单项关联的状态。"""

    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"


class ChecklistItem(SQLModel, table=True):
    """项目独有的资料完整性清单项。

    Attributes:
        id: 清单项全局唯一标识。
        project_id: 所属项目 ID，删除项目时一并清理。
        name: 用户可见的资料清单名称，如“施工方案”。
        document_type: 清单要求匹配的固定资料类型。
        is_required: 是否为必需项；未满足时分别派生缺失或未提供状态。
        project_stage: 清单要求匹配的固定项目阶段。
        description: 对该清单项满足条件的可选说明。
        version: 清单项乐观锁版本。
        created_at: 清单项创建的 UTC 时间。
        updated_at: 清单项最后修改的 UTC 时间。
    """

    __tablename__ = "checklist_items"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        Index("ix_checklist_items_project_updated_id", "project_id", "updated_at", "id"),
        Index("ix_checklist_items_project_required_stage", "project_id", "is_required", "project_stage"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(index=True)
    name: str = Field(min_length=1, max_length=200)
    document_type: ArchiveDocumentType = Field()
    is_required: bool = Field()
    project_stage: ProjectStage = Field()
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ChecklistLink(SQLModel, table=True):
    """人工确认的档案—清单项关联。

    Attributes:
        id: 关联记录的全局唯一标识。
        document_id: 被关联的归档文档 ID。
        checklist_item_id: 被满足或待重新确认的清单项 ID。
        status: 关联的确认或失效状态；只有已确认关联能够满足清单项。
        confirmed_by: 确认该关联的用户 ID。
        confirmed_at: 用户确认关联的 UTC 时间。
        invalidated_at: 关联失效的 UTC 时间。
        invalidated_reason: 关联失效原因，例如清单关键匹配字段变更。
        version: 关联记录的乐观锁版本。
        created_at: 关联创建的 UTC 时间。
        updated_at: 关联最后修改的 UTC 时间。
    """

    __tablename__ = "checklist_links"
    __table_args__ = (
        UniqueConstraint("document_id", "checklist_item_id", name="uq_checklist_links_document_item"),
        # 两种状态分别要求对应的确认或失效事实，避免只改状态而留下不完整的生命周期记录。
        CheckConstraint("status != 'CONFIRMED' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)", name="ck_checklist_links_confirmed_requirements"),
        CheckConstraint("status != 'INVALIDATED' OR (invalidated_at IS NOT NULL AND invalidated_reason IS NOT NULL)", name="ck_checklist_links_invalidated_requirements"),
        # 文档或清单项删除后，失去业务意义的关联也必须一并清理。
        ForeignKeyConstraint(["document_id"], ["archive_documents.document_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["checklist_item_id"], ["checklist_items.id"], ondelete="CASCADE"),
        Index("ix_checklist_links_item_status", "checklist_item_id", "status"),
        Index("ix_checklist_links_document_status", "document_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(index=True)
    checklist_item_id: UUID = Field(index=True)
    status: ChecklistLinkStatus = Field()
    confirmed_by: UUID | None = Field(default=None, foreign_key="users.id")
    confirmed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    invalidated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    invalidated_reason: str | None = Field(default=None, max_length=200)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
