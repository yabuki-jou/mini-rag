"""验证 FR-042 档案助手会话创建和五要素查找边界。"""

from collections.abc import Generator
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.dependencies.project_context import ProjectContext
from app.models import AgentSession, AgentType, KnowledgeBase, Project, User
from app.services.agent import archive_sessions as archive_session_service


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """创建仅供档案会话服务测试使用的内存数据库。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _project_context(session: Session) -> tuple[ProjectContext, User, Project]:
    """创建用户、知识库和项目，并返回可信项目上下文。"""
    owner = User(name="archive-agent-owner")
    knowledge_base = KnowledgeBase(owner_id=owner.id, name="archive-agent-kb")
    project = Project(
        owner_id=owner.id,
        kb_id=knowledge_base.id,
        name="档案助手项目",
    )
    session.add_all([owner, knowledge_base, project])
    session.commit()
    return (
        ProjectContext(
            user_id=owner.id,
            project_id=project.id,
            kb_id=knowledge_base.id,
        ),
        owner,
        project,
    )


def test_create_archive_session_uses_only_trusted_project_context(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建服务应生成完整 ARCHIVE 范围，且不构造模型或 Checkpoint。"""
    context, _, _ = _project_context(db_session)

    def fail_external_dependency(*_: object, **__: object) -> object:
        raise AssertionError("创建会话不得构造模型或 Checkpoint")

    monkeypatch.setattr(
        archive_session_service,
        "get_chat_model",
        fail_external_dependency,
        raising=False,
    )
    monkeypatch.setattr(
        archive_session_service,
        "build_archive_runtime",
        fail_external_dependency,
        raising=False,
    )

    created = archive_session_service.create_archive_agent_session(
        user_id=context.user_id,
        project_id=context.project_id,
        kb_id=context.kb_id,
        session=db_session,
    )

    assert created.user_id == context.user_id
    assert created.project_id == context.project_id
    assert created.kb_id == context.kb_id
    assert created.agent_type == AgentType.ARCHIVE
    assert created.thread_id
    assert created.updated_at == created.created_at
    assert db_session.get(AgentSession, created.id) is not None


def test_find_archive_session_requires_all_scope_factors(db_session: Session) -> None:
    """会话 ID、用户、项目、知识库或类型任一不匹配都必须统一返回 404。"""
    context, owner, project = _project_context(db_session)
    archive_session = archive_session_service.create_archive_agent_session(
        user_id=context.user_id,
        project_id=context.project_id,
        kb_id=context.kb_id,
        session=db_session,
    )
    policy_session = AgentSession(
        user_id=owner.id,
        kb_id=context.kb_id,
        agent_type=AgentType.POLICY,
    )
    db_session.add(policy_session)
    db_session.commit()

    found = archive_session_service.find_archive_agent_session(
        session_id=archive_session.id,
        user_id=context.user_id,
        project_id=context.project_id,
        kb_id=context.kb_id,
        session=db_session,
    )
    assert found.id == archive_session.id

    mismatches = (
        (uuid4(), context),
        (
            archive_session.id,
            ProjectContext(
                user_id=uuid4(),
                project_id=context.project_id,
                kb_id=context.kb_id,
            ),
        ),
        (
            archive_session.id,
            ProjectContext(
                user_id=context.user_id,
                project_id=uuid4(),
                kb_id=context.kb_id,
            ),
        ),
        (
            archive_session.id,
            ProjectContext(
                user_id=context.user_id,
                project_id=context.project_id,
                kb_id=uuid4(),
            ),
        ),
        (policy_session.id, context),
    )
    for session_id, mismatched_context in mismatches:
        with pytest.raises(AppError) as exc_info:
            archive_session_service.find_archive_agent_session(
                session_id=session_id,
                user_id=mismatched_context.user_id,
                project_id=mismatched_context.project_id,
                kb_id=mismatched_context.kb_id,
                session=db_session,
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "ARCHIVE_AGENT_SESSION_NOT_FOUND"

    assert project.id == context.project_id
