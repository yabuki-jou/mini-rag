"""验证 ``app.services.checklist_service`` 的事务边界。"""

from collections.abc import Generator

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocumentType,
    ChecklistItem,
    KnowledgeBase,
    Project,
    ProjectStage,
    User,
)
from app.schemas import ChecklistItemCreate
from app.services.checklist_service import create_checklist_item


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """提供带外键检查的独立 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        """让测试数据库执行与 PostgreSQL 相同的外键约束。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_create_rejects_stale_project_version_without_partial_records(
    db_session: Session,
) -> None:
    """服务层必须先命中项目版本，失败时不能留下清单项或审计记录。"""
    owner = User(name="owner")
    db_session.add(owner)
    db_session.commit()

    knowledge_base = KnowledgeBase(owner_id=owner.id, name="archive-kb")
    db_session.add(knowledge_base)
    db_session.commit()

    project = Project(owner_id=owner.id, kb_id=knowledge_base.id, name="测试项目")
    # 这些外键字段没有 ORM relationship；分步提交可让 SQLite 明确验证实际依赖顺序。
    db_session.add(project)
    db_session.commit()

    payload = ChecklistItemCreate(
        name="施工方案",
        document_type=ArchiveDocumentType.CONSTRUCTION,
        is_required=True,
        project_stage=ProjectStage.CONSTRUCTION,
        expected_project_version=1,
    )
    create_checklist_item(
        project_id=project.id,
        actor_id=owner.id,
        payload=payload,
        session=db_session,
    )

    with pytest.raises(AppError) as exc_info:
        create_checklist_item(
            project_id=project.id,
            actor_id=owner.id,
            payload=payload,
            session=db_session,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "VERSION_CONFLICT"
    assert db_session.get(Project, project.id).version == 2
    assert len(
        db_session.exec(
            select(ChecklistItem).where(ChecklistItem.project_id == project.id)
        ).all()
    ) == 1
    assert len(
        db_session.exec(
            select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project.id)
        ).all()
    ) == 1
