"""对显式提供的专用 PostgreSQL 测试库执行真实迁移和约束验证。"""

import os
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.agents.archive.runtime import build_archive_runtime
from app.agents.checkpoint import build_thread_config, open_checkpoint_store
from app.migration_service import upgrade_database
from app.services.project.management import delete_empty_project


POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")
EXPECTED_BUSINESS_TABLES = {
    "users",
    "knowledge_bases",
    "documents",
    "chat_sessions",
    "chat_messages",
    "agent_sessions",
    "agent_tool_call_logs",
    "auth_sessions",
    "projects",
    "archive_documents",
    "parsed_snapshots",
    "archive_field_values",
    "field_evidences",
    "checklist_items",
    "checklist_links",
    "archive_operations",
    "archive_audit_logs",
}


def _assert_postgres_insert_rejected(connection, statement, parameters) -> None:
    """在保存点内验证一个非法插入，避免中止外层测试事务。"""
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(statement, parameters)


class _ZeroToolModel:
    """为 PostgreSQL 集成门提供不访问外部模型的固定响应。"""

    def bind_tools(self, _tools):
        """模拟工具绑定并返回自身。"""
        return self

    def invoke(self, _messages):
        """返回不含工具调用的固定消息。"""
        return AIMessage(content="固定模型响应")


@pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="未设置只用于测试的 POSTGRES_TEST_URL",
)
def test_postgres_upgrade_and_agent_scope_behavior_are_self_contained(tmp_path) -> None:
    """同一测试完成迁移、约束、级联和项目删除服务联动。"""
    assert POSTGRES_TEST_URL is not None
    engine = create_engine(POSTGRES_TEST_URL)
    existing = set(inspect(engine).get_table_names())
    assert not existing, "POSTGRES_TEST_URL 必须指向空的专用测试数据库"

    upgrade_database(POSTGRES_TEST_URL)

    inspector = inspect(engine)
    assert EXPECTED_BUSINESS_TABLES <= set(inspector.get_table_names())
    assert not {"employee_profiles", "leave_balances", "leave_requests"} & set(
        inspector.get_table_names()
    )
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "0011_archive_agent_scope"
    # P01 只验收本次迁移边界，不用全库历史 autogenerate 差异作为门禁。

    assert {"agent_type", "project_id"} <= {
        column["name"] for column in inspector.get_columns("agent_sessions")
    }
    assert {
        "ck_agent_sessions_agent_type",
        "ck_agent_sessions_type_project",
    } <= {
        constraint["name"]
        for constraint in inspector.get_check_constraints("agent_sessions")
    }
    assert "ix_agent_sessions_project_id" in {
        index["name"] for index in inspector.get_indexes("agent_sessions")
    }

    session_fks = inspector.get_foreign_keys("agent_sessions")
    project_fk = next(
        foreign_key
        for foreign_key in session_fks
        if foreign_key["name"] == "fk_agent_sessions_project_kb"
    )
    assert project_fk["constrained_columns"] == ["project_id", "kb_id"]
    assert project_fk["referred_table"] == "projects"
    assert project_fk["referred_columns"] == ["id", "kb_id"]
    assert project_fk["options"]["ondelete"] == "CASCADE"

    log_fk = next(
        foreign_key
        for foreign_key in inspector.get_foreign_keys("agent_tool_call_logs")
        if foreign_key["name"] == "fk_agent_tool_logs_session_cascade"
    )
    assert log_fk["options"]["ondelete"] == "CASCADE"

    project_constraint = text(
        "SELECT pg_get_constraintdef(constraint_row.oid) AS constraint_definition, "
        "constraint_row.confmatchtype AS match_type "
        "FROM pg_constraint AS constraint_row "
        "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
        "JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace "
        "WHERE constraint_row.conname = 'fk_agent_sessions_project_kb' "
        "AND namespace_row.nspname = current_schema()"
    )
    with engine.begin() as connection:
        project_constraint_row = connection.execute(project_constraint).one()
        assert project_constraint_row.match_type == "s"
        assert "ON DELETE CASCADE" in project_constraint_row.constraint_definition

        user_id = uuid4()
        kb_id = uuid4()
        other_kb_id = uuid4()
        project_id = uuid4()
        policy_session_id = uuid4()
        archive_session_id = uuid4()
        archive_log_id = uuid4()
        now = datetime(2026, 9, 15)
        connection.execute(
            text(
                "INSERT INTO users (id, name, created_at, updated_at) "
                "VALUES (:id, :name, :created_at, :updated_at)"
            ),
            {"id": user_id, "name": "pg-agent-owner", "created_at": now, "updated_at": now},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, owner_id, name, created_at, updated_at) "
                "VALUES (:id, :owner_id, :name, :created_at, :updated_at)"
            ),
            [
                {
                    "id": kb_id,
                    "owner_id": user_id,
                    "name": "pg-agent-kb",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": other_kb_id,
                    "owner_id": user_id,
                    "name": "pg-other-kb",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, owner_id, kb_id, name, uses_demo_checklist, "
                "active_document_count, version, created_at, updated_at) "
                "VALUES (:id, :owner_id, :kb_id, :name, false, 0, 1, :created_at, :updated_at)"
            ),
            {
                "id": project_id,
                "owner_id": user_id,
                "kb_id": kb_id,
                "name": "PG Agent 项目",
                "created_at": now,
                "updated_at": now,
            },
        )

        session_insert = text(
            "INSERT INTO agent_sessions "
            "(id, user_id, kb_id, agent_type, project_id, thread_id, created_at, updated_at) "
            "VALUES (:id, :user_id, :kb_id, :agent_type, :project_id, :thread_id, :created_at, :updated_at)"
        )
        connection.execute(
            session_insert,
            {
                "id": policy_session_id,
                "user_id": user_id,
                "kb_id": kb_id,
                "agent_type": "POLICY",
                "project_id": None,
                "thread_id": "pg-policy-thread",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            session_insert,
            {
                "id": archive_session_id,
                "user_id": user_id,
                "kb_id": kb_id,
                "agent_type": "ARCHIVE",
                "project_id": project_id,
                "thread_id": "pg-archive-thread",
                "created_at": now,
                "updated_at": now,
            },
        )
        _assert_postgres_insert_rejected(
            connection,
            session_insert,
            {
                "id": uuid4(),
                "user_id": user_id,
                "kb_id": kb_id,
                "agent_type": "INVALID",
                "project_id": None,
                "thread_id": "pg-invalid-type",
                "created_at": now,
                "updated_at": now,
            },
        )
        _assert_postgres_insert_rejected(
            connection,
            session_insert,
            {
                "id": uuid4(),
                "user_id": user_id,
                "kb_id": kb_id,
                "agent_type": "ARCHIVE",
                "project_id": None,
                "thread_id": "pg-invalid-null-project",
                "created_at": now,
                "updated_at": now,
            },
        )
        _assert_postgres_insert_rejected(
            connection,
            session_insert,
            {
                "id": uuid4(),
                "user_id": user_id,
                "kb_id": kb_id,
                "agent_type": "POLICY",
                "project_id": project_id,
                "thread_id": "pg-invalid-policy-project",
                "created_at": now,
                "updated_at": now,
            },
        )
        _assert_postgres_insert_rejected(
            connection,
            session_insert,
            {
                "id": uuid4(),
                "user_id": user_id,
                "kb_id": other_kb_id,
                "agent_type": "ARCHIVE",
                "project_id": project_id,
                "thread_id": "pg-invalid-cross-kb",
                "created_at": now,
                "updated_at": now,
            },
        )

        connection.execute(
            text(
                "INSERT INTO agent_tool_call_logs "
                "(id, agent_session_id, tool_call_id, tool_name, status, created_at, updated_at) "
                "VALUES (:id, :session_id, :tool_call_id, :tool_name, 'COMPLETED', :created_at, :updated_at)"
            ),
            {
                "id": archive_log_id,
                "session_id": archive_session_id,
                "tool_call_id": "pg-cascade-call",
                "tool_name": "search_archive_catalog",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text("DELETE FROM projects WHERE id = :id"),
            {"id": project_id},
        )
        assert connection.execute(
            text("SELECT COUNT(*) FROM agent_sessions WHERE id = :id"),
            {"id": archive_session_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM agent_tool_call_logs WHERE id = :id"),
            {"id": archive_log_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM agent_sessions WHERE id = :id"),
            {"id": policy_session_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM knowledge_bases WHERE id = :id"),
            {"id": kb_id},
        ).scalar_one() == 1

    service_project_id = uuid4()
    service_session_id = uuid4()
    service_log_id = uuid4()
    service_thread_id = "pg-service-archive-thread"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id, owner_id, kb_id, name, uses_demo_checklist, "
                "active_document_count, version, created_at, updated_at) "
                "VALUES (:id, :owner_id, :kb_id, :name, false, 0, 1, :created_at, :updated_at)"
            ),
            {
                "id": service_project_id,
                "owner_id": user_id,
                "kb_id": kb_id,
                "name": "PG 删除服务项目",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            session_insert,
            {
                "id": service_session_id,
                "user_id": user_id,
                "kb_id": kb_id,
                "agent_type": "ARCHIVE",
                "project_id": service_project_id,
                "thread_id": service_thread_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO agent_tool_call_logs "
                "(id, agent_session_id, tool_call_id, tool_name, status, created_at, updated_at) "
                "VALUES (:id, :session_id, :tool_call_id, :tool_name, 'COMPLETED', :created_at, :updated_at)"
            ),
            {
                "id": service_log_id,
                "session_id": service_session_id,
                "tool_call_id": "pg-service-delete-call",
                "tool_name": "list_formal_archives",
                "created_at": now,
                "updated_at": now,
            },
        )

    checkpoint_path = tmp_path / "postgres-project-delete.db"
    with build_archive_runtime(
        user_id=user_id,
        project_id=service_project_id,
        kb_id=kb_id,
        session_factory=lambda: nullcontext(SimpleNamespace()),
        model=_ZeroToolModel(),
        judge_model=_ZeroToolModel(),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="写入待删除线程")],
                "user_id": str(user_id),
                "project_id": str(service_project_id),
                "kb_id": str(kb_id),
                "tool_call_count": 0,
            },
            thread_id=service_thread_id,
        )
    with open_checkpoint_store(checkpoint_path) as store:
        assert store.checkpointer.get_tuple(build_thread_config(service_thread_id)) is not None

    with Session(engine) as session:
        delete_empty_project(
            project_id=service_project_id,
            session=session,
            checkpoint_path=checkpoint_path,
        )

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM projects WHERE id = :id"),
            {"id": service_project_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM agent_sessions WHERE id = :id"),
            {"id": service_session_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM agent_tool_call_logs WHERE id = :id"),
            {"id": service_log_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM agent_sessions WHERE id = :id"),
            {"id": policy_session_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM knowledge_bases WHERE id = :id"),
            {"id": kb_id},
        ).scalar_one() == 1
    with open_checkpoint_store(checkpoint_path) as store:
        assert store.checkpointer.get_tuple(build_thread_config(service_thread_id)) is None
    engine.dispose()
