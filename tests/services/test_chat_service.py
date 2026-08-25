"""验证聊天会话创建、权限校验和最近消息查询。"""

from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.models import (
    ChatMessage,
    ChatSession,
    KnowledgeBase,
    MessageRole,
    User,
    utc_now,
)
from app.schemas import SourceRead
from app.services.chat_service import (
    ask_question,
    create_chat_session,
    deserialize_sources,
    read_recent_messages,
    save_chat_exchange,
    serialize_sources,
)
from app.services.retrieval_service import RetrievedChunk


@pytest.fixture
def db_session() -> Session:
    """创建仅供当前测试使用的内存 SQLite Session。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


def test_create_chat_session(db_session: Session) -> None:
    """合法用户应能在自己的知识库中创建会话。"""
    user = User(name="owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="kb")
    db_session.add(user)
    db_session.add(knowledge_base)
    db_session.commit()

    chat_session = create_chat_session(
        current_user=user,
        kb_id=knowledge_base.id,
        session=db_session,
    )

    assert chat_session.user_id == user.id
    assert chat_session.kb_id == knowledge_base.id
    assert db_session.get(ChatSession, chat_session.id) is not None


def test_create_chat_session_rejects_missing_kb(
    db_session: Session,
) -> None:
    """知识库不存在时应返回稳定的404业务错误。"""
    user = User(name="owner")
    db_session.add(user)
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        create_chat_session(user, uuid4(), db_session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "KNOWLEDGE_BASE_NOT_FOUND"


def test_create_chat_session_rejects_other_users_kb(
    db_session: Session,
) -> None:
    """用户不能使用其他用户的知识库创建会话。"""
    owner = User(name="owner")
    other_user = User(name="other")
    knowledge_base = KnowledgeBase(owner_id=owner.id, name="kb")
    db_session.add(owner)
    db_session.add(other_user)
    db_session.add(knowledge_base)
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        create_chat_session(other_user, knowledge_base.id, db_session)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "KNOWLEDGE_BASE_FORBIDDEN"


def test_read_recent_messages_returns_latest_twenty_in_order(
    db_session: Session,
) -> None:
    """历史查询应舍弃最早5条，并把最新20条恢复为时间正序。"""
    user = User(name="owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="kb")
    chat_session = ChatSession(user_id=user.id, kb_id=knowledge_base.id)
    db_session.add(user)
    db_session.add(knowledge_base)
    db_session.add(chat_session)

    start_time = utc_now()
    for index in range(25):
        db_session.add(
            ChatMessage(
                session_id=chat_session.id,
                role=MessageRole.USER,
                content=f"message-{index}",
                created_at=start_time + timedelta(seconds=index),
            )
        )
    db_session.commit()

    messages = read_recent_messages(chat_session, db_session)

    assert len(messages) == 20
    assert [message.content for message in messages] == [
        f"message-{index}" for index in range(5, 25)
    ]


def test_read_recent_messages_orders_equal_time_exchange(
    db_session: Session,
) -> None:
    """时间完全相同时，历史消息仍应按照用户问题、助手回答返回。"""
    user = User(name="owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="kb")
    chat_session = ChatSession(
        user_id=user.id,
        kb_id=knowledge_base.id,
    )
    same_created_at = utc_now()

    db_session.add(user)
    db_session.add(knowledge_base)
    db_session.add(chat_session)
    db_session.add(
        ChatMessage(
            session_id=chat_session.id,
            role=MessageRole.USER,
            content="问题",
            created_at=same_created_at,
        )
    )
    db_session.add(
        ChatMessage(
            session_id=chat_session.id,
            role=MessageRole.ASSISTANT,
            content="回答",
            created_at=same_created_at,
        )
    )
    db_session.commit()

    messages = read_recent_messages(chat_session, db_session)

    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert [message.content for message in messages] == ["问题", "回答"]


def make_source() -> SourceRead:
    """创建测试复用的有效引用对象。"""
    return SourceRead(
        source_id="S1",
        chunk_id="a" * 64,
        document_id=uuid4(),
        document_name="员工制度.pdf",
        page=2,
        excerpt="专业培训的年度上限为 3,000 元。",
        score=0.73,
    )


def test_sources_json_round_trip_preserves_data() -> None:
    """引用经过 JSON 存取后应保留中文、UUID 和数值字段。"""
    source = make_source()

    sources_json = serialize_sources([source])
    restored_sources = deserialize_sources(sources_json)

    assert "专业培训" in sources_json
    assert restored_sources == [source]


def test_empty_sources_can_be_serialized_and_restored() -> None:
    """空引用和用户消息的空值都应恢复为空列表。"""
    assert serialize_sources([]) == "[]"
    assert deserialize_sources("[]") == []
    assert deserialize_sources(None) == []


@pytest.mark.parametrize(
    "sources_json",
    [
        "{bad json",
        "{}",
        '[{"source_id": "S1"}]',
    ],
)
def test_deserialize_sources_rejects_invalid_data(
    sources_json: str,
) -> None:
    """损坏的 JSON 或缺少字段的引用不得静默进入历史响应。"""
    with pytest.raises(AppError) as exc_info:
        deserialize_sources(sources_json)

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "CHAT_SOURCE_DATA_INVALID"


def test_save_chat_exchange_commits_complete_pair(
    db_session: Session,
) -> None:
    """一次提交应同时保存用户问题、助手回答及回答引用。"""
    user = User(name="owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="kb")
    chat_session = ChatSession(
        user_id=user.id,
        kb_id=knowledge_base.id,
    )
    db_session.add(user)
    db_session.add(knowledge_base)
    db_session.add(chat_session)
    db_session.commit()
    source = make_source()

    user_message, assistant_message = save_chat_exchange(
        chat_session=chat_session,
        question="专业培训上限是多少？",
        answer="专业培训的年度上限为 3,000 元。[S1]",
        sources=[source],
        session=db_session,
    )

    messages = db_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat_session.id)
        .order_by(ChatMessage.created_at.asc())
    ).all()
    assert messages == [user_message, assistant_message]
    assert user_message.role == MessageRole.USER
    assert user_message.sources_json is None
    assert assistant_message.role == MessageRole.ASSISTANT
    assert user_message.created_at < assistant_message.created_at
    assert chat_session.updated_at == assistant_message.created_at
    assert deserialize_sources(assistant_message.sources_json) == [source]


def test_save_chat_exchange_rolls_back_failed_commit() -> None:
    """提交失败时必须回滚，不能保留不完整的一问一答。"""
    chat_session = ChatSession(user_id=uuid4(), kb_id=uuid4())
    session = Mock(spec=Session)
    session.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(AppError) as exc_info:
        save_chat_exchange(
            chat_session=chat_session,
            question="问题",
            answer="回答",
            sources=[make_source()],
            session=session,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "CHAT_HISTORY_SAVE_FAILED"
    assert session.add.call_count == 3
    session.rollback.assert_called_once_with()


def test_ask_question_rejects_without_calling_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 Chunk 通过阈值时应直接拒答，同时仍保存完整问答。"""
    chat_session = ChatSession(user_id=uuid4(), kb_id=uuid4())
    database_session = Mock(spec=Session)
    model_factory = Mock()
    save_exchange = Mock()

    # 隔离数据库、Chroma 和 DeepSeek，只验证问答编排分支。
    monkeypatch.setattr(
        "app.services.chat_service.read_recent_messages",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.chat_service.retrieve_chunks",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.chat_service.get_chat_model",
        model_factory,
    )
    monkeypatch.setattr(
        "app.services.chat_service.save_chat_exchange",
        save_exchange,
    )

    response = ask_question(
        chat_session=chat_session,
        question="个人健身卡可以报销吗？",
        session=database_session,
    )

    assert response.answer == "知识库中没有足够依据。"
    assert response.rejected is True
    assert response.sources == []
    model_factory.assert_not_called()
    save_exchange.assert_called_once_with(
        chat_session=chat_session,
        question="个人健身卡可以报销吗？",
        answer="知识库中没有足够依据。",
        sources=[],
        session=database_session,
    )


def test_ask_question_generates_answer_with_matching_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常分支应调用模型，并使 S1 与返回 sources[0] 对应。"""
    chat_session = ChatSession(user_id=uuid4(), kb_id=uuid4())
    database_session = Mock(spec=Session)
    retrieved_chunk = RetrievedChunk(
        chunk_id="b" * 64,
        document_id=uuid4(),
        document_name="费用制度.pdf",
        page=1,
        content="专业培训的年度上限为 3,000 元。",
        score=0.82,
    )
    chat_model = Mock()
    chat_model.invoke.return_value = AIMessage(
        content="专业培训的年度上限为 3,000 元。[S1]",
    )
    save_exchange = Mock()

    # 固定历史和检索结果，使测试只关注 Prompt、回答、引用和保存顺序。
    monkeypatch.setattr(
        "app.services.chat_service.read_recent_messages",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.chat_service.retrieve_chunks",
        Mock(return_value=[retrieved_chunk]),
    )
    monkeypatch.setattr(
        "app.services.chat_service.get_chat_model",
        Mock(return_value=chat_model),
    )
    monkeypatch.setattr(
        "app.services.chat_service.save_chat_exchange",
        save_exchange,
    )

    response = ask_question(
        chat_session=chat_session,
        question="专业培训上限是多少？",
        session=database_session,
    )

    assert response.rejected is False
    assert response.answer == "专业培训的年度上限为 3,000 元。[S1]"
    assert len(response.sources) == 1
    assert response.sources[0].source_id == "S1"
    assert response.sources[0].chunk_id == retrieved_chunk.chunk_id
    chat_model.invoke.assert_called_once()
    save_exchange.assert_called_once_with(
        chat_session=chat_session,
        question="专业培训上限是多少？",
        answer=response.answer,
        sources=response.sources,
        session=database_session,
    )


def test_ask_question_does_not_save_when_model_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek 请求失败时不应保存缺少助手回答的聊天记录。"""
    chat_session = ChatSession(user_id=uuid4(), kb_id=uuid4())
    retrieved_chunk = RetrievedChunk(
        chunk_id="c" * 64,
        document_id=uuid4(),
        document_name="制度.pdf",
        page=1,
        content="制度原文。",
        score=0.80,
    )
    chat_model = Mock()
    chat_model.invoke.side_effect = RuntimeError("network error")
    save_exchange = Mock()

    # 模拟模型网络异常，确认异常转换和事务边界是否正确。
    monkeypatch.setattr(
        "app.services.chat_service.read_recent_messages",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.chat_service.retrieve_chunks",
        Mock(return_value=[retrieved_chunk]),
    )
    monkeypatch.setattr(
        "app.services.chat_service.get_chat_model",
        Mock(return_value=chat_model),
    )
    monkeypatch.setattr(
        "app.services.chat_service.save_chat_exchange",
        save_exchange,
    )

    with pytest.raises(AppError) as exc_info:
        ask_question(
            chat_session=chat_session,
            question="制度是什么？",
            session=Mock(spec=Session),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "DEEPSEEK_REQUEST_FAILED"
    save_exchange.assert_not_called()
