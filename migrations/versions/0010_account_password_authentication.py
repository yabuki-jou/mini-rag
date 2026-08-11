"""为用户增加账号密码凭据，并创建可撤销认证会话表。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Alembic 默认把 revision 写入 VARCHAR(32)；标识必须控制在 32 字符以内。
revision: str = "0010_account_auth"
down_revision: str | Sequence[str] | None = "0009_chroma_vector_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_COMMENTS = {
    "auth_sessions": "认证会话：保存可撤销会话和 Refresh Token 单向哈希。",
}


COLUMN_COMMENTS = {
    "users": {
        "username": "全局唯一且统一小写的登录标识；历史用户可为空。",
        "password_hash": "Argon2 密码哈希；绝不保存明文密码。",
    },
    "auth_sessions": {
        "id": "认证会话主键，同时写入 JWT sid 声明。",
        "user_id": "认证会话所属用户。",
        "refresh_token_hash": "Refresh Token 的 SHA-256 哈希；不保存原文。",
        "expires_at": "Refresh Token 与认证会话的 UTC 到期时间。",
        "revoked_at": "会话注销 UTC 时间；为空代表未撤销。",
        "created_at": "认证会话创建 UTC 时间。",
        "updated_at": "认证会话最后刷新或撤销 UTC 时间。",
    },
}


def upgrade() -> None:
    """以兼容旧用户数据的方式添加登录凭据和认证会话。"""
    # 历史用户保留 username/password_hash 空值，不能被误视为已完成注册。
    op.add_column("users", sa.Column("username", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.create_index("uq_users_username", "users", ["username"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_sessions_user_revoked_expires",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name, comment in TABLE_COMMENTS.items():
        op.execute(f"COMMENT ON TABLE {table_name} IS '{comment}'")
    for table_name, column_comments in COLUMN_COMMENTS.items():
        for column_name, comment in column_comments.items():
            op.execute(f"COMMENT ON COLUMN {table_name}.{column_name} IS '{comment}'")


def downgrade() -> None:
    """删除认证会话和新增凭据列；仅供尚未写入业务数据的本地开发回退。"""
    if op.get_bind().dialect.name == "postgresql":
        for table_name, column_comments in COLUMN_COMMENTS.items():
            for column_name in column_comments:
                op.execute(f"COMMENT ON COLUMN {table_name}.{column_name} IS NULL")
        for table_name in TABLE_COMMENTS:
            op.execute(f"COMMENT ON TABLE {table_name} IS NULL")

    op.drop_table("auth_sessions")
    op.drop_index("uq_users_username", table_name="users")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch:
            batch.drop_column("password_hash")
            batch.drop_column("username")
    else:
        op.drop_column("users", "password_hash")
        op.drop_column("users", "username")
