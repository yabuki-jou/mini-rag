"""用隔离 SQLite 快速验证 Alembic 迁移逻辑和模型一致性。"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, SQLModel

from app import models as _models  # noqa: F401  注册当前业务表。
from app.migration_service import build_alembic_config, upgrade_database
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    Document,
    KnowledgeBase,
    Project,
    User,
)


EXPECTED_BUSINESS_TABLES = {
    "users",
    "knowledge_bases",
    "documents",
    "chat_sessions",
    "chat_messages",
    "agent_sessions",
    "agent_tool_call_logs",
    "auth_sessions",
    "projects",
    "archive_documents",
    "parsed_snapshots",
    "archive_field_values",
    "field_evidences",
    "checklist_items",
    "checklist_links",
    "archive_operations",
    "archive_audit_logs",
}

LEGACY_TABLES = (
    "users",
    "knowledge_bases",
    "documents",
    "chat_sessions",
    "chat_messages",
)


def sqlite_url(path) -> str:
    """把 pytest 临时路径转换为跨工作目录稳定的 SQLite URL。"""
    return f"sqlite:///{path.as_posix()}"


def test_upgrade_creates_current_schema_in_empty_database(tmp_path) -> None:
    """空数据库升级后应包含业务表和 Alembic 版本表。"""
    database_path = tmp_path / "empty.db"
    target_url = sqlite_url(database_path)

    upgrade_database(target_url)

    engine = create_engine(target_url)
    table_names = set(inspect(engine).get_table_names())
    assert EXPECTED_BUSINESS_TABLES <= table_names
    assert "alembic_version" in table_names

    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "0010_account_auth"
    engine.dispose()


def test_revision_ids_fit_default_alembic_version_column() -> None:
    """所有 revision 必须能写入 Alembic 默认的 VARCHAR(32) 版本列。"""
    revisions = ScriptDirectory.from_config(build_alembic_config()).walk_revisions()

    assert all(len(revision.revision) <= 32 for revision in revisions)


def test_upgrade_preserves_data_in_legacy_schema(tmp_path) -> None:
    """迁移链执行时应保留旧 RAG 数据并移除临时请假领域。"""
    database_path = tmp_path / "legacy.db"
    target_url = sqlite_url(database_path)
    # 先建立真实的 0004 基线，再验证 0005 不会丢失已有 RAG 数据；不能拿演进后的
    # SQLModel metadata 伪造旧库，否则 documents 的 file_hash 会提前出现。
    command.upgrade(build_alembic_config(target_url), "0004_remove_leave_domain")
    engine = create_engine(target_url)

    user_id = uuid4()
    # 该库仍处于 0004，不能通过包含新增字段的当前 User 模型插入数据。
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, name, created_at, updated_at) "
                "VALUES (:id, :name, :created_at, :updated_at)"
            ),
            {
                # SQLAlchemy 的 SQLite UUID 绑定使用无连字符的 32 位十六进制值。
                "id": user_id.hex,
                "name": "迁移测试用户",
                "created_at": "2026-08-10 00:00:00",
                "updated_at": "2026-08-10 00:00:00",
            },
        )
    engine.dispose()

    upgrade_database(target_url)

    migrated_engine = create_engine(target_url)
    with Session(migrated_engine) as session:
        migrated_user = session.get(User, user_id)
    assert migrated_user is not None
    assert migrated_user.name == "迁移测试用户"
    assert migrated_user.username is None
    assert migrated_user.password_hash is None
    assert "alembic_version" in inspect(migrated_engine).get_table_names()
    migrated_engine.dispose()


def test_upgrade_rejects_incompatible_legacy_table(tmp_path) -> None:
    """字段不匹配时必须停止，不能直接 stamp 掩盖 Schema 冲突。"""
    database_path = tmp_path / "incompatible.db"
    target_url = sqlite_url(database_path)
    engine = create_engine(target_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
    engine.dispose()

    with pytest.raises((RuntimeError, SQLAlchemyError)) as exc_info:
        upgrade_database(target_url)

    assert "users" in str(exc_info.value)


def test_migration_head_matches_sqlmodel_metadata(tmp_path) -> None:
    """升级到 head 后，关键智慧档案表和列必须实际存在。"""
    database_path = tmp_path / "metadata-check.db"
    target_url = sqlite_url(database_path)
    upgrade_database(target_url)

    engine = create_engine(target_url)
    inspector = inspect(engine)
    assert {"project_id", "file_hash"} <= {
        column["name"] for column in inspector.get_columns("documents")
    }
    assert {
        "status",
        "current_snapshot_id",
        "final_index_snapshot_hash",
    } <= {column["name"] for column in inspector.get_columns("archive_documents")}
    assert {"username", "password_hash"} <= {
        column["name"] for column in inspector.get_columns("users")
    }
    assert {
        "id",
        "user_id",
        "refresh_token_hash",
        "expires_at",
        "revoked_at",
    } <= {column["name"] for column in inspector.get_columns("auth_sessions")}
    engine.dispose()


def test_archive_constraints_cover_project_hash_confirmation_and_visibility(tmp_path) -> None:
    """关键归档约束必须由数据库兜底，而不是依赖未来 API 或客户端。"""
    database_path = tmp_path / "archive-constraints.db"
    target_url = sqlite_url(database_path)
    upgrade_database(target_url)
    engine = create_engine(target_url)

    with Session(engine) as session:
        user = User(name="archive-owner")
        knowledge_base = KnowledgeBase(owner_id=user.id, name="archive-kb")
        session.add_all([user, knowledge_base])
        session.commit()

        project = Project(owner_id=user.id, kb_id=knowledge_base.id, name="项目 A")
        session.add(project)
        session.commit()

        session.add(Project(owner_id=user.id, kb_id=uuid4(), name="项目 A"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        document = Document(
            kb_id=knowledge_base.id,
            project_id=project.id,
            filename="a.txt",
            storage_path="a.txt",
            file_hash="a" * 64,
        )
        session.add(document)
        session.commit()

        duplicate = Document(
            kb_id=knowledge_base.id,
            project_id=project.id,
            filename="duplicate.txt",
            storage_path="duplicate.txt",
            file_hash="a" * 64,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        archive_document = ArchiveDocument(
            document_id=document.id,
            status=ArchiveDocumentStatus.UPLOADED,
        )
        session.add(archive_document)
        session.commit()

        session.add(
            ArchiveOperation(
                document_id=document.id,
                operation_type=ArchiveOperationType.PARSE,
                operation_status=ArchiveOperationStatus.RUNNING,
                visibility_blocking=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        archive_document.status = ArchiveDocumentStatus.CONFIRMED
        session.add(archive_document)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()
