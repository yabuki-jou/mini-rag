"""验证 Agent 工具调用审计的脱敏和排序行为。"""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.models import (
    AgentToolCallLog,
    AgentToolCallStatus,
    KnowledgeBase,
    User,
    utc_now,
)
from app.services.agent.audit import read_agent_tool_calls
from app.services.agent.sessions import create_agent_session


@pytest.fixture
def db_session():
    """创建只供 Agent 审计服务单测使用的内存 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def user_and_kb(session: Session) -> tuple[User, KnowledgeBase]:
    """创建测试用户及其知识库。"""
    user = User(name="audit_owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="audit-kb")
    session.add(user)
    session.add(knowledge_base)
    session.commit()
    return user, knowledge_base


def test_read_agent_tool_calls_orders_records_and_decodes_safe_summaries(
    db_session: Session,
) -> None:
    """工具日志应按时间排序并反序列化脱敏摘要。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)
    base_time = utc_now()
    newer = AgentToolCallLog(
        agent_session_id=agent_session.id,
        tool_call_id="call-new",
        tool_name="search_company_policy",
        status=AgentToolCallStatus.COMPLETED,
        arguments_summary_json='{"query_provided":true,"query_length":4}',
        result_summary_json='{"found":true,"result_count":1}',
        created_at=base_time + timedelta(seconds=2),
    )
    older = AgentToolCallLog(
        agent_session_id=agent_session.id,
        tool_call_id="call-old",
        tool_name="search_company_policy",
        status=AgentToolCallStatus.FAILED,
        arguments_summary_json=None,
        result_summary_json=None,
        error_code="AGENT_TOOL_FAILED",
        created_at=base_time,
    )
    db_session.add(newer)
    db_session.add(older)
    db_session.commit()

    records = read_agent_tool_calls(agent_session, db_session)

    assert [record.tool_call_id for record in records] == ["call-old", "call-new"]
    assert records[1].arguments_summary == {
        "query_provided": True,
        "query_length": 4,
    }
    assert records[1].result_summary == {"found": True, "result_count": 1}


def test_read_agent_tool_calls_rejects_corrupt_json(db_session: Session) -> None:
    """损坏的工具摘要 JSON 应返回稳定错误码。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)
    db_session.add(
        AgentToolCallLog(
            agent_session_id=agent_session.id,
            tool_call_id="call-bad",
            tool_name="search_company_policy",
            status=AgentToolCallStatus.COMPLETED,
            arguments_summary_json="{bad json",
        )
    )
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        read_agent_tool_calls(agent_session, db_session)

    assert exc_info.value.code == "AGENT_TOOL_LOG_INVALID"
