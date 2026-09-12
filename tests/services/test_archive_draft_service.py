"""验证 AV1-P07 人工草稿服务的确定性事务规则。"""

from collections.abc import Generator
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    Document,
    DocumentStatus,
    KnowledgeBase,
    ParsedSnapshot,
    Project,
    User,
)
from app.schemas import ArchiveFieldUpdate
from app.services.archive_draft_service import create_manual_draft, update_field


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """提供启用外键约束的隔离 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _create_parsed_document(session: Session) -> tuple[Document, UUID, UUID]:
    """创建服务测试所需的用户、知识库、原文档和当前快照。"""
    user = User(name="service-owner")
    session.add(user)
    session.commit()
    knowledge_base = KnowledgeBase(owner_id=user.id, name="archive-kb")
    session.add(knowledge_base)
    session.commit()
    project = Project(owner_id=user.id, kb_id=knowledge_base.id, name="archive-project")
    session.add(project)
    session.commit()
    document = Document(
        kb_id=knowledge_base.id,
        project_id=project.id,
        filename="资料.txt",
        storage_path="C:/tmp/资料.txt",
        file_hash="b" * 64,
        status=DocumentStatus.UPLOADED,
    )
    session.add(document)
    session.flush()
    archive_document = ArchiveDocument(
        document_id=document.id,
        status=ArchiveDocumentStatus.PARSED,
    )
    session.add(archive_document)
    session.flush()
    snapshot = ParsedSnapshot(
        document_id=document.id,
        snapshot_storage_path="C:/tmp/parsed_snapshot.json",
        snapshot_hash="c" * 64,
        parser_name="test-parser",
        parser_version="test-v1",
        normalization_version="test-v1",
        text_character_count=20,
        fragment_count=1,
    )
    session.add(snapshot)
    session.flush()
    archive_document.current_snapshot_id = snapshot.id
    session.add(archive_document)
    session.commit()
    return document, user.id, snapshot.id


def test_update_rejects_wrong_value_column_without_mutating_field(
    db_session: Session,
) -> None:
    """日期字段不能写入文本列，拒绝后数据库值和版本保持不变。"""
    document, user_id, _ = _create_parsed_document(db_session)
    draft = create_manual_draft(document=document, actor_id=user_id, session=db_session)
    with pytest.raises(AppError) as exc_info:
        update_field(
            document=document,
            actor_id=user_id,
            field_name=ArchiveFieldName.DOCUMENT_DATE,
            payload=ArchiveFieldUpdate(
                text_value="2026-08-26",
                review_status="VALUE_CONFIRMED",
                no_source_evidence=True,
                expected_version=draft.document.version,
            ),
            session=db_session,
        )

    assert exc_info.value.code == "FIELD_VALUE_SHAPE_INVALID"
    archive_document = db_session.get(ArchiveDocument, document.id)
    assert archive_document is not None
    assert archive_document.version == draft.document.version
    field_values = db_session.exec(select(ArchiveFieldValue)).all()
    assert len(field_values) == 7
    assert all(value.date_value is None for value in field_values)


def test_update_rejects_stale_version_after_successful_save(
    db_session: Session,
) -> None:
    """同一草稿的第二次旧版本写入必须被乐观锁拒绝。"""
    document, user_id, _ = _create_parsed_document(db_session)
    draft = create_manual_draft(document=document, actor_id=user_id, session=db_session)
    payload = ArchiveFieldUpdate(
        text_value="施工方案",
        review_status="VALUE_CONFIRMED",
        no_source_evidence=True,
        expected_version=draft.document.version,
    )
    update_field(
        document=document,
        actor_id=user_id,
        field_name=ArchiveFieldName.TITLE,
        payload=payload,
        session=db_session,
    )

    with pytest.raises(AppError) as exc_info:
        update_field(
            document=document,
            actor_id=user_id,
            field_name=ArchiveFieldName.TITLE,
            payload=payload,
            session=db_session,
        )
    assert exc_info.value.code == "VERSION_CONFLICT"
    archive_document = db_session.get(ArchiveDocument, document.id)
    assert archive_document is not None
    assert archive_document.version == draft.document.version + 1
    field = db_session.exec(
        select(ArchiveFieldValue).where(
            ArchiveFieldValue.document_id == document.id,
            ArchiveFieldValue.field_name == ArchiveFieldName.TITLE,
        )
    ).one()
    assert field.text_value == "施工方案"
