"""定义用户和知识库数据库实体。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import utc_now


class User(SQLModel, table=True):
    """表示可以创建和访问知识库的基础用户。

    Attributes:
        id: 用户的全局唯一标识。
        username: 全局唯一且规范化为小写的登录标识；历史用户允许为空。
        name: 用户显示名称。
        password_hash: Argon2 计算出的密码哈希；绝不保存明文密码。
        created_at: 用户记录的 UTC 创建时间。
        updated_at: 用户记录最后一次更新的 UTC 时间。
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str | None = Field(default=None, sa_column=Column(String(50), nullable=True))
    name: str = Field(min_length=1, max_length=100, index=True)
    password_hash: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AuthSession(SQLModel, table=True):
    """表示可撤销的账号登录会话。

    只保存 Refresh Token 的单向哈希。Access Token 本身不落库，但请求会使用其
    ``sid`` 查询本表，因此注销会立即阻断该会话继续访问受保护资源。

    Attributes:
        id: 会话全局唯一标识，同时写入 JWT 的 ``sid`` 声明。
        user_id: 创建并拥有该会话的用户 ID。
        refresh_token_hash: Refresh Token 的 SHA-256 哈希，数据库内唯一。
        expires_at: Refresh Token 与会话的 UTC 到期时间。
        revoked_at: 注销时间；为空代表会话仍可使用。
        created_at: 会话创建 UTC 时间。
        updated_at: 会话最近一次刷新或撤销 UTC 时间。
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        Index("ix_auth_sessions_user_revoked_expires", "user_id", "revoked_at", "expires_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(index=True)
    refresh_token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class KnowledgeBase(SQLModel, table=True):
    """表示由一个用户拥有的独立知识库。

    Attributes:
        id: 知识库的全局唯一标识。
        owner_id: 知识库所有者的用户 ID。
        name: 知识库显示名称。
        created_at: 知识库记录的 UTC 创建时间。
        updated_at: 知识库记录最后一次更新的 UTC 时间。
    """

    __tablename__ = "knowledge_bases"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="users.id", index=True)
    name: str = Field(min_length=1, max_length=100, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
