"""验证 Agent 会话创建的授权边界。"""

from collections.abc import Generator
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.models import AgentSession, KnowledgeBase, User
from app.services.agent.sessions import create_agent_session


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """创建只供 Agent 会话服务单测使用的内存 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def user_and_kb(
    session: Session,
    name: str = "agent_owner",
) -> tuple[User, KnowledgeBase]:
    """创建测试用户及其知识库。"""
    user = User(name=name)
    knowledge_base = KnowledgeBase(owner_id=user.id, name=f"{name}-kb")
    session.add(user)
    session.add(knowledge_base)
    session.commit()
    session.refresh(user)
    session.refresh(knowledge_base)
    return user, knowledge_base


def test_create_agent_session_checks_knowledge_base_scope(
    db_session: Session,
) -> None:
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
