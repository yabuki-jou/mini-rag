"""验证档案助手入口拒绝历史 POLICY 会话。"""

from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.dependencies.archive_agent import get_archive_agent_session
from app.dependencies.project_context import ProjectContext
from app.models import AgentSession, AgentType, KnowledgeBase, Project, User


def test_archive_dependency_accepts_only_matching_archive_session() -> None:
    """档案依赖必须使用完整项目范围，并拒绝 POLICY 会话 ID。"""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(name="archive-owner")
            knowledge_base = KnowledgeBase(owner_id=owner.id, name="archive-kb")
            project = Project(owner_id=owner.id, kb_id=knowledge_base.id, name="项目 A")
            session.add_all([owner, knowledge_base, project])
            session.commit()
            context = ProjectContext(
                user_id=owner.id,
                project_id=project.id,
                kb_id=knowledge_base.id,
            )
            archive_session = AgentSession(
                user_id=owner.id,
                project_id=project.id,
                kb_id=knowledge_base.id,
                agent_type=AgentType.ARCHIVE,
            )
            policy_session = AgentSession(
                user_id=owner.id,
                kb_id=knowledge_base.id,
                agent_type=AgentType.POLICY,
            )
            session.add_all([archive_session, policy_session])
            session.commit()

            assert (
                get_archive_agent_session(archive_session.id, context, session).id
                == archive_session.id
            )
            try:
                get_archive_agent_session(policy_session.id, context, session)
            except AppError as exc:
                assert exc.status_code == 404
                assert exc.code == "ARCHIVE_AGENT_SESSION_NOT_FOUND"
            else:
                raise AssertionError("档案依赖不得接受 POLICY 会话")
    finally:
        engine.dispose()
