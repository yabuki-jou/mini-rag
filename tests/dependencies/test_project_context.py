"""验证智慧档案项目范围只能由服务端授权依赖建立。"""

from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.dependencies.project_context import get_project_context
from app.models import KnowledgeBase, Project, User


def test_project_context_uses_owner_project_and_bound_knowledge_base() -> None:
    """项目上下文必须来自已验证用户与项目映射，而不是客户端提供的知识库 ID。"""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(name="owner")
            knowledge_base = KnowledgeBase(owner_id=owner.id, name="archive-kb")
            project = Project(owner_id=owner.id, kb_id=knowledge_base.id, name="项目 A")
            session.add_all([owner, knowledge_base, project])
            session.commit()

            context = get_project_context(project.id, session, owner)

            assert context.user_id == owner.id
            assert context.project_id == project.id
            assert context.kb_id == knowledge_base.id
    finally:
        engine.dispose()


def test_project_context_rejects_missing_or_other_users_project() -> None:
    """不存在项目与越权项目必须使用稳定业务错误拒绝。"""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            owner = User(name="owner")
            other_user = User(name="other")
            knowledge_base = KnowledgeBase(owner_id=owner.id, name="archive-kb")
            project = Project(owner_id=owner.id, kb_id=knowledge_base.id, name="项目 A")
            session.add_all([owner, other_user, knowledge_base, project])
            session.commit()

            with pytest.raises(AppError, match="项目不存在或无权访问") as missing:
                get_project_context(uuid4(), session, owner)
            assert missing.value.code == "PROJECT_NOT_FOUND"

            with pytest.raises(AppError, match="无权访问该项目") as forbidden:
                get_project_context(project.id, session, other_user)
            assert forbidden.value.code == "PROJECT_FORBIDDEN"
    finally:
        engine.dispose()
