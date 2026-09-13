"""验证 Agent Checkpoint 消息读取和来源绑定。"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.models import KnowledgeBase, User
from app.services.agent.messages import read_agent_messages
from app.services.agent.sessions import create_agent_session


class FakeRuntime:
    """提供可控的 Checkpoint 状态读取。"""

    def __init__(
        self,
        messages: list[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.messages = messages or []
        self.error = error

    def get_state(self, *, thread_id: str) -> SimpleNamespace:
        """返回模拟线程状态或抛出指定异常。"""
        del thread_id
        if self.error is not None:
            raise self.error
        return SimpleNamespace(values={"messages": self.messages})


@pytest.fixture
def db_session():
    """创建只供 Agent 消息服务单测使用的内存 SQLite 会话。"""
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
    user = User(name="message_owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="message-kb")
    session.add(user)
    session.add(knowledge_base)
    session.commit()
    return user, knowledge_base


def test_read_agent_messages_filters_tools_and_binds_sources_to_next_answer(
    db_session: Session,
) -> None:
    """工具消息和空工具调用消息不可见，来源应绑定后续回答。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)
    document_id = uuid4()
    tool_payload = {
        "found": True,
        "results": [
            {
                "chunk_id": "a" * 64,
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
                    {
                        "id": "call-1",
                        "name": "search_company_policy",
                        "args": {},
                    }
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
