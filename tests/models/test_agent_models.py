"""验证 FR-042 Agent 会话类型和项目绑定的 SQLModel 契约。"""

from uuid import uuid4

from sqlalchemy import String

from app.models import AgentSession, AgentToolCallLog, AgentType


def test_agent_type_is_exported_with_two_frozen_values() -> None:
    """会话类型只能在制度和档案两个业务边界之间选择。"""
    assert AgentType.POLICY.value == "POLICY"
    assert AgentType.ARCHIVE.value == "ARCHIVE"
    session = AgentSession(user_id=uuid4(), kb_id=uuid4())
    assert session.agent_type is AgentType.POLICY
    assert session.project_id is None


def test_agent_session_declares_archive_scope_constraints_and_index() -> None:
    """模型元数据必须承载数据库层的类型、项目和复合绑定约束。"""
    table = AgentSession.__table__
    assert isinstance(table.c.agent_type.type, String)
    assert table.c.agent_type.type.length == 16
    assert table.c.agent_type.nullable is False
    assert table.c.agent_type.server_default is not None
    assert table.c.project_id.nullable is True

    constraints = {constraint.name: constraint for constraint in table.constraints}
    assert {
        "ck_agent_sessions_agent_type",
        "ck_agent_sessions_type_project",
        "fk_agent_sessions_project_kb",
    } <= constraints.keys()
    project_fk = constraints["fk_agent_sessions_project_kb"]
    assert [column.name for column in project_fk.columns] == ["project_id", "kb_id"]
    assert project_fk.elements[0].target_fullname.endswith("projects.id")
    assert project_fk.elements[1].target_fullname.endswith("projects.kb_id")
    assert project_fk.elements[0].ondelete == "CASCADE"
    assert project_fk.elements[1].ondelete == "CASCADE"
    assert project_fk.elements[0].match == "SIMPLE"
    assert project_fk.elements[1].match == "SIMPLE"
    assert any(
        index.name == "ix_agent_sessions_project_id"
        and [column.name for column in index.columns] == ["project_id"]
        for index in table.indexes
    )


def test_agent_tool_log_foreign_key_is_cascading_and_named() -> None:
    """工具日志模型必须与 0011 的具名级联外键保持一致。"""
    foreign_keys = [
        constraint
        for constraint in AgentToolCallLog.__table__.constraints
        if constraint.name == "fk_agent_tool_logs_session_cascade"
    ]
    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    assert foreign_key.ondelete == "CASCADE"
