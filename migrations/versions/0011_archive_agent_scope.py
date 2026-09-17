"""为 Agent 会话增加制度/档案类型和项目绑定范围。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_archive_agent_scope"
down_revision: str | Sequence[str] | None = "0010_account_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_COMMENTS: dict[str, str] = {}

COLUMN_COMMENTS = {
    "agent_sessions": {
        "agent_type": "Agent 会话类型：POLICY 制度会话或 ARCHIVE 项目档案会话。",
        "project_id": "档案 Agent 绑定的项目；制度 Agent 必须为空。",
    },
}

_SQLITE_FK_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _write_comments() -> None:
    """仅在 PostgreSQL 写入本迁移新增字段的稳定说明。"""
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name, column_comments in COLUMN_COMMENTS.items():
        for column_name, comment in column_comments.items():
            op.execute(f"COMMENT ON COLUMN {table_name}.{column_name} IS '{comment}'")


def _replace_tool_log_foreign_key() -> None:
    """按方言替换工具日志外键，兼容 PostgreSQL 匿名旧约束。"""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        inspector = sa.inspect(bind)
        matching_foreign_keys = [
            foreign_key
            for foreign_key in inspector.get_foreign_keys("agent_tool_call_logs")
            if foreign_key["constrained_columns"] == ["agent_session_id"]
            and foreign_key["referred_table"] == "agent_sessions"
        ]
        if (
            len(matching_foreign_keys) != 1
            or matching_foreign_keys[0]["name"] is None
        ):
            raise RuntimeError(
                "agent_tool_call_logs 必须恰好存在一个具名的 "
                "agent_session_id -> agent_sessions 外键"
            )
        old_foreign_key = matching_foreign_keys[0]
        op.drop_constraint(
            old_foreign_key["name"],
            "agent_tool_call_logs",
            type_="foreignkey",
        )
        op.create_foreign_key(
            "fk_agent_tool_logs_session_cascade",
            "agent_tool_call_logs",
            "agent_sessions",
            ["agent_session_id"],
            ["id"],
            ondelete="CASCADE",
        )
        return

    with op.batch_alter_table(
        "agent_tool_call_logs",
        recreate="always",
        naming_convention=_SQLITE_FK_NAMING,
    ) as batch:
        batch.drop_constraint(
            "fk_agent_tool_call_logs_agent_session_id_agent_sessions",
            type_="foreignkey",
        )
        batch.create_foreign_key(
            "fk_agent_tool_logs_session_cascade",
            "agent_sessions",
            ["agent_session_id"],
            ["id"],
            ondelete="CASCADE",
        )


def _add_agent_session_scope() -> None:
    """先回填类型，再以可执行顺序建立档案会话的数据库约束。"""
    bind = op.get_bind()
    op.add_column(
        "agent_sessions",
        sa.Column("agent_type", sa.String(length=16), nullable=True),
    )
    op.add_column("agent_sessions", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE agent_sessions SET agent_type = 'POLICY' "
            "WHERE agent_type IS NULL"
        )
    )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "agent_sessions",
            recreate="always",
            naming_convention=_SQLITE_FK_NAMING,
        ) as batch:
            batch.alter_column(
                "agent_type",
                existing_type=sa.String(length=16),
                nullable=False,
                server_default=sa.text("'POLICY'"),
            )
            batch.create_check_constraint(
                "ck_agent_sessions_agent_type",
                "agent_type IN ('POLICY', 'ARCHIVE')",
            )
            batch.create_check_constraint(
                "ck_agent_sessions_type_project",
                "(agent_type = 'POLICY' AND project_id IS NULL) "
                "OR (agent_type = 'ARCHIVE' AND project_id IS NOT NULL)",
            )
            batch.create_foreign_key(
                "fk_agent_sessions_project_kb",
                "projects",
                ["project_id", "kb_id"],
                ["id", "kb_id"],
                ondelete="CASCADE",
                match="SIMPLE",
            )
        op.create_index("ix_agent_sessions_project_id", "agent_sessions", ["project_id"])
        return

    op.alter_column(
        "agent_sessions",
        "agent_type",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default=sa.text("'POLICY'"),
    )
    op.create_check_constraint(
        "ck_agent_sessions_agent_type",
        "agent_sessions",
        "agent_type IN ('POLICY', 'ARCHIVE')",
    )
    op.create_check_constraint(
        "ck_agent_sessions_type_project",
        "agent_sessions",
        "(agent_type = 'POLICY' AND project_id IS NULL) "
        "OR (agent_type = 'ARCHIVE' AND project_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_agent_sessions_project_kb",
        "agent_sessions",
        "projects",
        ["project_id", "kb_id"],
        ["id", "kb_id"],
        ondelete="CASCADE",
        match="SIMPLE",
    )
    op.create_index("ix_agent_sessions_project_id", "agent_sessions", ["project_id"])


def upgrade() -> None:
    """兼容存量制度会话，并建立档案会话的数据库级绑定边界。"""
    _add_agent_session_scope()
    _replace_tool_log_foreign_key()
    _write_comments()


def _restore_tool_log_foreign_key() -> None:
    """回退工具日志外键的级联语义，恢复为历史普通外键。"""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "agent_tool_call_logs",
            recreate="always",
            naming_convention=_SQLITE_FK_NAMING,
        ) as batch:
            batch.drop_constraint(
                "fk_agent_tool_logs_session_cascade",
                type_="foreignkey",
            )
            batch.create_foreign_key(
                "fk_agent_tool_call_logs_agent_session_id_agent_sessions",
                "agent_sessions",
                ["agent_session_id"],
                ["id"],
            )
        return

    op.drop_constraint(
        "fk_agent_tool_logs_session_cascade",
        "agent_tool_call_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        None,
        "agent_tool_call_logs",
        "agent_sessions",
        ["agent_session_id"],
        ["id"],
    )


def _drop_agent_session_scope() -> None:
    """按依赖逆序删除档案范围列和约束。"""
    bind = op.get_bind()
    op.drop_index("ix_agent_sessions_project_id", table_name="agent_sessions")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "agent_sessions",
            recreate="always",
            naming_convention=_SQLITE_FK_NAMING,
        ) as batch:
            batch.drop_constraint("fk_agent_sessions_project_kb", type_="foreignkey")
            batch.drop_constraint("ck_agent_sessions_type_project", type_="check")
            batch.drop_constraint("ck_agent_sessions_agent_type", type_="check")
            batch.drop_column("project_id")
            batch.drop_column("agent_type")
        return

    op.drop_constraint(
        "fk_agent_sessions_project_kb",
        "agent_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_agent_sessions_type_project",
        "agent_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_sessions_agent_type",
        "agent_sessions",
        type_="check",
    )
    op.drop_column("project_id", table_name="agent_sessions")
    op.drop_column("agent_type", table_name="agent_sessions")


def downgrade() -> None:
    """存在档案会话时拒绝回退，避免静默丢失项目绑定语义。"""
    existing_archive = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM agent_sessions "
            "WHERE agent_type = 'ARCHIVE' LIMIT 1"
        )
    ).first()
    if existing_archive is not None:
        raise RuntimeError("存在 ARCHIVE Agent 会话，禁止回退 0011_archive_agent_scope")

    _restore_tool_log_foreign_key()
    _drop_agent_session_scope()
