"""对显式提供的专用 PostgreSQL 测试库执行真实迁移验证。"""

import os

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.migration_service import build_alembic_config, upgrade_database


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


@pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="未设置只用于测试的 POSTGRES_TEST_URL",
)
def test_postgres_upgrade_reaches_head_without_leave_domain() -> None:
    """专用空库升级后应只有当前业务表，并可通过 metadata 检查。"""
    assert POSTGRES_TEST_URL is not None
    engine = create_engine(POSTGRES_TEST_URL)
    existing = set(inspect(engine).get_table_names())
    assert not existing, "POSTGRES_TEST_URL 必须指向空的专用测试数据库"

    upgrade_database(POSTGRES_TEST_URL)

    table_names = set(inspect(engine).get_table_names())
    assert EXPECTED_BUSINESS_TABLES <= table_names
    assert not {"employee_profiles", "leave_balances", "leave_requests"} & table_names
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "0010_account_auth"
    command.check(build_alembic_config(POSTGRES_TEST_URL))
    engine.dispose()
