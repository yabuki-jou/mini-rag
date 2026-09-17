"""用隔离 SQLite 快速验证 Alembic 迁移逻辑和模型一致性。"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
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
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    AgentType,
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
    assert revision == "0011_archive_agent_scope"
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
    assert {"agent_type", "project_id"} <= {
        column["name"] for column in inspector.get_columns("agent_sessions")
    }
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


def _sqlite_engine_with_foreign_keys(target_url: str):
    """为迁移行为测试打开 SQLite 外键，否则级联与复合外键不会生效。"""
    engine = create_engine(target_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        """让每个新 SQLite 连接都执行外键开关。"""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def test_agent_session_scope_constraints_and_project_cascade(tmp_path) -> None:
    """SQLite 开启外键后应验证会话范围、错绑、级联和存量类型。"""
    database_path = tmp_path / "agent-scope.db"
    target_url = sqlite_url(database_path)
    upgrade_database(target_url)
    engine = _sqlite_engine_with_foreign_keys(target_url)

    with Session(engine) as session:
        user = User(name="agent-owner")
        knowledge_base = KnowledgeBase(owner_id=user.id, name="agent-kb")
        other_kb = KnowledgeBase(owner_id=user.id, name="other-kb")
        session.add(user)
        session.commit()
        session.add_all([knowledge_base, other_kb])
        session.commit()
        project = Project(owner_id=user.id, kb_id=knowledge_base.id, name="Agent 项目")
        session.add(project)
        session.commit()

        policy_session = AgentSession(
            user_id=user.id,
            kb_id=knowledge_base.id,
            agent_type=AgentType.POLICY,
        )
        archive_session = AgentSession(
            user_id=user.id,
            kb_id=knowledge_base.id,
            project_id=project.id,
            agent_type=AgentType.ARCHIVE,
        )
        second_archive_session = AgentSession(
            user_id=user.id,
            kb_id=knowledge_base.id,
            project_id=project.id,
            agent_type=AgentType.ARCHIVE,
        )
        session.add_all([policy_session, archive_session, second_archive_session])
        session.commit()

        session.add(
            AgentSession(
                user_id=user.id,
                kb_id=knowledge_base.id,
                agent_type=AgentType.ARCHIVE,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        archive_log = AgentToolCallLog(
            agent_session_id=archive_session.id,
            tool_call_id="cascade-call",
            tool_name="list_formal_archives",
            status=AgentToolCallStatus.COMPLETED,
        )
        session.add(archive_log)
        session.commit()
        archive_session_id = archive_session.id
        second_archive_session_id = second_archive_session.id
        policy_session_id = policy_session.id
        archive_log_id = archive_log.id

        session.add(
            AgentSession(
                user_id=user.id,
                kb_id=knowledge_base.id,
                project_id=project.id,
                agent_type=AgentType.POLICY,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            AgentSession(
                user_id=user.id,
                kb_id=knowledge_base.id,
                agent_type="INVALID",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            AgentSession(
                user_id=user.id,
                kb_id=other_kb.id,
                project_id=project.id,
                agent_type=AgentType.ARCHIVE,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.delete(project)
        session.commit()
        assert session.get(AgentSession, archive_session_id) is None
        assert session.get(AgentSession, second_archive_session_id) is None
        assert session.get(AgentToolCallLog, archive_log_id) is None
        assert session.get(AgentSession, policy_session_id) is not None
        assert session.get(KnowledgeBase, knowledge_base.id) is not None
    engine.dispose()


def test_existing_agent_sessions_are_backfilled_to_policy(tmp_path) -> None:
    """从 0010 升级时，存量制度会话必须获得 POLICY 类型且项目为空。"""
    database_path = tmp_path / "agent-backfill.db"
    target_url = sqlite_url(database_path)
    command.upgrade(build_alembic_config(target_url), "0010_account_auth")
    engine = _sqlite_engine_with_foreign_keys(target_url)
    user_id = uuid4()
    kb_id = uuid4()
    session_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, name, created_at, updated_at) "
                "VALUES (:id, :name, :created_at, :updated_at)"
            ),
            {
                "id": user_id.hex,
                "name": "存量 Agent 用户",
                "created_at": "2026-08-10 00:00:00",
                "updated_at": "2026-08-10 00:00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, owner_id, name, created_at, updated_at) "
                "VALUES (:id, :owner_id, :name, :created_at, :updated_at)"
            ),
            {
                "id": kb_id.hex,
                "owner_id": user_id.hex,
                "name": "存量 Agent 知识库",
                "created_at": "2026-08-10 00:00:00",
                "updated_at": "2026-08-10 00:00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO agent_sessions "
                "(id, user_id, kb_id, thread_id, created_at, updated_at) "
                "VALUES (:id, :user_id, :kb_id, :thread_id, :created_at, :updated_at)"
            ),
            {
                "id": session_id.hex,
                "user_id": user_id.hex,
                "kb_id": kb_id.hex,
                "thread_id": "legacy-agent-thread",
                "created_at": "2026-08-10 00:00:00",
                "updated_at": "2026-08-10 00:00:00",
            },
        )
    engine.dispose()

    upgrade_database(target_url)
    migrated_engine = _sqlite_engine_with_foreign_keys(target_url)
    with migrated_engine.connect() as connection:
        row = connection.execute(
            text("SELECT agent_type, project_id FROM agent_sessions WHERE id = :id"),
            {"id": session_id.hex},
        ).one()
    assert row.agent_type == "POLICY"
    assert row.project_id is None
    migrated_engine.dispose()


def test_downgrade_rejects_existing_archive_sessions(tmp_path) -> None:
    """存在档案会话时回退必须明确失败并保留当前迁移版本。"""
    database_path = tmp_path / "agent-downgrade-blocked.db"
    target_url = sqlite_url(database_path)
    upgrade_database(target_url)
    engine = _sqlite_engine_with_foreign_keys(target_url)
    with Session(engine) as session:
        user = User(name="downgrade-owner")
        knowledge_base = KnowledgeBase(owner_id=user.id, name="downgrade-kb")
        session.add(user)
        session.commit()
        session.add(knowledge_base)
        session.commit()
        project = Project(owner_id=user.id, kb_id=knowledge_base.id, name="降级项目")
        session.add(project)
        session.commit()
        session.add(
            AgentSession(
                user_id=user.id,
                kb_id=knowledge_base.id,
                project_id=project.id,
                agent_type=AgentType.ARCHIVE,
            )
        )
        session.commit()
    engine.dispose()

    with pytest.raises(RuntimeError, match="ARCHIVE"):
        command.downgrade(build_alembic_config(target_url), "0010_account_auth")

    check_engine = _sqlite_engine_with_foreign_keys(target_url)
    with check_engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "0011_archive_agent_scope"
    check_engine.dispose()


def test_downgrade_removes_agent_scope_without_archive_sessions(tmp_path) -> None:
    """没有档案会话时回退应删除新增列和约束。"""
    database_path = tmp_path / "agent-downgrade-ok.db"
    target_url = sqlite_url(database_path)
    upgrade_database(target_url)
    command.downgrade(build_alembic_config(target_url), "0010_account_auth")

    engine = _sqlite_engine_with_foreign_keys(target_url)
    inspector = inspect(engine)
    assert {"agent_type", "project_id"}.isdisjoint(
        {column["name"] for column in inspector.get_columns("agent_sessions")}
    )
    assert not {
        "fk_agent_sessions_project_kb",
        "ck_agent_sessions_agent_type",
        "ck_agent_sessions_type_project",
    } & {
        constraint["name"]
        for constraint in inspector.get_check_constraints("agent_sessions")
    }
    assert not any(
        foreign_key["name"] == "fk_agent_tool_logs_session_cascade"
        for foreign_key in inspector.get_foreign_keys("agent_tool_call_logs")
    )
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "0010_account_auth"
    engine.dispose()
