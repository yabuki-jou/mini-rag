"""定义项目隔离边界和项目级容量计数模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Index, SmallInteger, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import utc_now


class Project(SQLModel, table=True):
    """归档隔离边界及其唯一绑定的知识库。

    Attributes:
        id: 项目的全局唯一标识，也是 V1 全部项目资源的隔离边界。
        owner_id: 创建并拥有项目的用户 ID；客户端不能指定或修改。
        kb_id: 服务端创建的内部知识库 ID，与项目一对一绑定。
        name: 已去除首尾空格的项目名称，在同一用户范围内唯一。
        description: 用户可选的项目说明，不扩展为行业专用字段。
        uses_demo_checklist: 创建时是否复制五项虚构演示清单。
        active_document_count: 当前未删除项目文档数，用于 100 份容量上限校验。
        version: 乐观锁版本；项目名称或说明更新后递增。
        created_at: 项目记录的 UTC 创建时间。
        updated_at: 项目记录最后一次更新的 UTC 时间。
    """

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
        # 复合唯一键是 documents、agent_sessions 等跨表复合外键的稳定目标。
        UniqueConstraint("id", "kb_id", name="uq_projects_id_kb_id"),
        # 一个知识库只能属于一个项目，保证项目是档案资源的唯一隔离边界。
        UniqueConstraint("kb_id", name="uq_projects_kb_id"),
        # 名称和容量约束放在数据库层，避免绕过 API 的写入破坏项目计数与唯一性语义。
        CheckConstraint("name = trim(name) AND length(name) > 0", name="ck_projects_name_trimmed"),
        CheckConstraint("active_document_count BETWEEN 0 AND 100", name="ck_projects_active_document_count"),
        Index("ix_projects_owner_updated_id", "owner_id", "updated_at", "id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="users.id", index=True)
    kb_id: UUID = Field(foreign_key="knowledge_bases.id", index=True)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    uses_demo_checklist: bool = Field(default=False)
    active_document_count: int = Field(
        default=0,
        ge=0,
        le=100,
        sa_column=Column(SmallInteger, nullable=False, server_default="0"),
    )
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
