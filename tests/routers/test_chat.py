"""验证聊天 HTTP 接口、引用转换和用户会话隔离。"""

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import (
    ChatMessage,
    ChatSession,
    KnowledgeBase,
    MessageRole,
    User,
    utc_now,
)
from app.routers import chat as chat_router
from app.schemas import ChatAnswerResponse, SourceRead
from app.services.chat_service import serialize_sources
from tests.support.auth import auth_headers


@pytest.fixture
def chat_api() -> Generator[tuple[TestClient, Engine], None, None]:
    """创建使用独立内存 SQLite 的聊天 API 测试环境。

    Yields:
        FastAPI 测试客户端及其专用 SQLite Engine。
    """
    # StaticPool 让不同请求 Session 共用同一个内存 SQLite 数据库。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        """为每个测试请求提供连接测试数据库的 Session。"""
        with Session(engine) as session:
            yield session

    # 只替换数据库依赖；用户和资源权限依赖仍执行真实项目逻辑。
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    try:
        yield client, engine
    finally:
        # 避免依赖覆盖泄漏到其他测试，并释放测试数据库资源。
        app.dependency_overrides.clear()
        engine.dispose()


def create_user_and_knowledge_base(
    engine: Engine,
    user_name: str = "owner",
) -> tuple[UUID, UUID]:
    """在测试数据库中创建用户及其知识库。

    Args:
        engine: 当前测试使用的 SQLite Engine。
        user_name: 测试用户名称。

    Returns:
        依次返回用户 UUID 和知识库 UUID。
    """
    user = User(name=user_name)
    knowledge_base = KnowledgeBase(owner_id=user.id, name="测试知识库")
    user_id = user.id
    kb_id = knowledge_base.id

    with Session(engine) as session:
        session.add(user)
        session.add(knowledge_base)
        session.commit()

    return user_id, kb_id


def create_chat_session_record(
    engine: Engine,
    user_id: UUID,
    kb_id: UUID,
) -> UUID:
    """在测试数据库中创建聊天会话并返回其 UUID。"""
    chat_session = ChatSession(user_id=user_id, kb_id=kb_id)
    session_id = chat_session.id

    with Session(engine) as session:
        session.add(chat_session)
        session.commit()

    return session_id


def make_source() -> SourceRead:
    """创建接口测试复用的有效回答引用。"""
    return SourceRead(
        source_id="S1",
        chunk_id="d" * 64,
        document_id=uuid4(),
        document_name="费用制度.pdf",
        page=1,
        excerpt="专业培训的年度上限为 3,000 元。",
        score=0.82,
    )


def test_create_chat_session_endpoint(
    chat_api: tuple[TestClient, Engine],
) -> None:
    """当前用户应能通过 HTTP 接口在自己的知识库中创建会话。"""
    client, engine = chat_api
    user_id, kb_id = create_user_and_knowledge_base(engine)

    response = client.post(
        "/chat-sessions",
        headers=auth_headers(engine, user_id),
        json={"kb_id": str(kb_id)},
    )

    assert response.status_code == 201
    response_data = response.json()
    assert response_data["user_id"] == str(user_id)
    assert response_data["kb_id"] == str(kb_id)

    # HTTP 成功后还要确认会话确实已经写入测试 SQLite。
    with Session(engine) as session:
        saved_session = session.get(ChatSession, UUID(response_data["id"]))
        assert saved_session is not None


def test_ask_question_endpoint(
    chat_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提问接口应传入授权会话，并返回问答服务生成的引用响应。"""
    client, engine = chat_api
    user_id, kb_id = create_user_and_knowledge_base(engine)
    session_id = create_chat_session_record(engine, user_id, kb_id)
    source = make_source()
    expected_response = ChatAnswerResponse(
        answer="专业培训上限为 3,000 元/年。[S1]",
        rejected=False,
        sources=[source],
    )
    ask_question_mock = Mock(return_value=expected_response)

    # 路由已经直接导入 ask_question，因此必须替换路由模块中的函数引用。
    monkeypatch.setattr(chat_router, "ask_question", ask_question_mock)

    response = client.post(
        f"/chat-sessions/{session_id}/messages",
        headers=auth_headers(engine, user_id),
        json={"question": "专业培训上限是多少？"},
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["answer"] == expected_response.answer
    assert response_data["rejected"] is False
    assert response_data["sources"][0]["source_id"] == "S1"

    # 验证路由传给服务层的是依赖校验后的会话，而不是客户端自造数据。
    ask_question_mock.assert_called_once()
    call_arguments = ask_question_mock.call_args.kwargs
    assert call_arguments["chat_session"].id == session_id
    assert call_arguments["question"] == "专业培训上限是多少？"
    assert isinstance(call_arguments["session"], Session)


def test_read_chat_messages_endpoint(
    chat_api: tuple[TestClient, Engine],
) -> None:
    """历史接口应按时间正序返回消息，并恢复助手消息的结构化引用。"""
    client, engine = chat_api
    user_id, kb_id = create_user_and_knowledge_base(engine)
    session_id = create_chat_session_record(engine, user_id, kb_id)
    source = make_source()
    first_created_at = utc_now()

    # 用户消息没有引用；助手消息使用 SQLite 中实际保存的 sources_json。
    with Session(engine) as session:
        session.add(
            ChatMessage(
                session_id=session_id,
                role=MessageRole.USER,
                content="专业培训上限是多少？",
                sources_json=None,
                created_at=first_created_at,
            )
        )
        session.add(
            ChatMessage(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content="专业培训上限为 3,000 元/年。[S1]",
                sources_json=serialize_sources([source]),
                created_at=first_created_at + timedelta(seconds=1),
            )
        )
        session.commit()

    response = client.get(
        f"/chat-sessions/{session_id}/messages",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 200
    response_data = response.json()
    assert [message["role"] for message in response_data] == [
        MessageRole.USER.value,
        MessageRole.ASSISTANT.value,
    ]
    assert response_data[0]["sources"] == []
    assert response_data[1]["sources"][0]["source_id"] == "S1"
    assert response_data[1]["sources"][0]["document_id"] == str(
        source.document_id
    )


@pytest.mark.parametrize("method", ["get", "post"])
def test_other_user_cannot_access_chat_session(
    chat_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """其他用户通过查询或提问接口访问会话时都应得到 403。"""
    client, engine = chat_api
    owner_id, kb_id = create_user_and_knowledge_base(engine, "owner")
    other_user_id, _ = create_user_and_knowledge_base(engine, "other")
    session_id = create_chat_session_record(engine, owner_id, kb_id)
    ask_question_mock = Mock()
    monkeypatch.setattr(chat_router, "ask_question", ask_question_mock)

    # POST 需要合法问题请求体；GET 会忽略传入的 None。
    request_json = {"question": "测试问题"} if method == "post" else None
    response = client.request(
        method=method,
        url=f"/chat-sessions/{session_id}/messages",
        headers=auth_headers(engine, other_user_id),
        json=request_json,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CHAT_SESSION_FORBIDDEN"
    ask_question_mock.assert_not_called()
