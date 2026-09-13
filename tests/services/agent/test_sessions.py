"""验证 Agent 会话、Checkpoint 消息和工具调用审计的服务行为。"""

import json
from collections.abc import Generator
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.models import (
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    KnowledgeBase,
    User,
    utc_now,
)
from app.services.agent.sessions import (
    create_agent_session,
    read_agent_messages,
    read_agent_tool_calls,
)


class FakeRuntime:
    """提供可控的 Checkpoint 状态读取。"""

    def __init__(self, messages: list[object] | None = None, error: Exception | None = None):
        self.messages = messages or []
        self.error = error

    def get_state(self, *, thread_id: str) -> SimpleNamespace:
        """返回模拟线程状态或抛出指定异常。"""
        del thread_id
        if self.error is not None:
            raise self.error
        return SimpleNamespace(values={"messages": self.messages})


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """创建只供 Agent 服务单测使用的内存 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def user_and_kb(session: Session, name: str = "agent_owner") -> tuple[User, KnowledgeBase]:
    """创建测试用户及其知识库。"""
    user = User(name=name)
    knowledge_base = KnowledgeBase(owner_id=user.id, name=f"{name}-kb")
    session.add(user)
    session.add(knowledge_base)
    session.commit()
    session.refresh(user)
    session.refresh(knowledge_base)
    return user, knowledge_base


def test_create_agent_session_checks_knowledge_base_scope(db_session: Session) -> None:
    """会话创建应覆盖成功、知识库缺失和越权。"""
    owner, knowledge_base = user_and_kb(db_session)
    other, _ = user_and_kb(db_session, "other_owner")

    created = create_agent_session(owner, knowledge_base.id, db_session)
    assert created.user_id == owner.id
    assert created.kb_id == knowledge_base.id
    assert db_session.get(AgentSession, created.id) is not None

    with pytest.raises(AppError) as missing:
        create_agent_session(owner, uuid4(), db_session)
    with pytest.raises(AppError) as forbidden:
        create_agent_session(other, knowledge_base.id, db_session)
    assert missing.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert forbidden.value.code == "KNOWLEDGE_BASE_FORBIDDEN"


def test_read_agent_messages_filters_tools_and_binds_sources_to_next_answer(
    db_session: Session,
) -> None:
    """工具消息和空工具调用消息不可见，来源应绑定后续回答。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)
    document_id = uuid4()
    chunk_id = "a" * 64
    tool_payload = {
        "found": True,
        "results": [
            {
                "chunk_id": chunk_id,
                "document_id": str(document_id),
                "document_name": "制度.pdf",
                "page": 4,
                "content": "报销上限为三千元。",
                "score": 0.9,
            }
        ],
    }
    runtime = FakeRuntime(
        [
            HumanMessage(content="第一个问题"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call-1", "name": "search_company_policy", "args": {}}
                ],
            ),
            ToolMessage(
                content=json.dumps(tool_payload, ensure_ascii=False),
                name="search_company_policy",
                tool_call_id="call-1",
            ),
            AIMessage(content="第一个回答"),
            HumanMessage(content="第二个问题"),
            AIMessage(content="第二个回答"),
        ]
    )

    messages = read_agent_messages(agent_session, runtime)

    assert [(item.role.value, item.content) for item in messages] == [
        ("USER", "第一个问题"),
        ("ASSISTANT", "第一个回答"),
        ("USER", "第二个问题"),
        ("ASSISTANT", "第二个回答"),
    ]
    assert messages[1].sources[0].document_id == document_id
    assert messages[1].sources[0].source_id == "S1"
    assert messages[3].sources == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError("slow"), "AGENT_CHECKPOINT_TIMEOUT"),
        (ConnectionError("offline"), "AGENT_CHECKPOINT_UNAVAILABLE"),
        (RuntimeError("broken"), "AGENT_CHECKPOINT_FAILED"),
    ],
)
def test_read_agent_messages_maps_checkpoint_errors(
    db_session: Session,
    error: Exception,
    code: str,
) -> None:
    """Checkpoint 超时、连接和未知错误均应转换为稳定业务码。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)

    with pytest.raises(AppError) as exc_info:
        read_agent_messages(agent_session, FakeRuntime(error=error))

    assert exc_info.value.code == code
    assert str(error) not in exc_info.value.message


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
    assert records[1].arguments_summary == {"query_provided": True, "query_length": 4}
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
