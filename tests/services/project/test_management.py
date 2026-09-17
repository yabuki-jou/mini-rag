"""验证 FR-042 P07 空项目删除与 Agent Checkpoint 联动。"""

from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.admin.runtime import build_admin_runtime
from app.agents.archive.runtime import build_archive_runtime
from app.agents.checkpoint import build_thread_config, open_checkpoint_store
from app.core.errors import AppError
from app.models import (
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    AgentType,
    Document,
    KnowledgeBase,
    Project,
    User,
)
from app.services.project import management


class ZeroToolModel:
    """以固定文本结束 Graph，不调用外部模型。"""

    def __init__(self, content: str) -> None:
        self.content = content

    def bind_tools(self, _tools):
        """模拟工具绑定并返回自身。"""
        return self

    def invoke(self, _messages):
        """返回不带 Tool Call 的固定 AIMessage。"""
        return AIMessage(content=self.content)


@pytest.fixture
def engine():
    """创建启用外键的隔离 SQLite 业务库。"""
    database_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(database_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        """让 SQLite 执行与 PostgreSQL 一致的级联约束。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


def _seed_scope(engine):
    """创建目标/其他项目、POLICY/ARCHIVE 会话和工具日志。"""
    with Session(engine) as session:
        user = User(name=f"delete-owner-{uuid4()}")
        session.add(user)
        session.commit()
        target_kb = KnowledgeBase(owner_id=user.id, name=f"target-kb-{uuid4()}")
        other_kb = KnowledgeBase(owner_id=user.id, name=f"other-kb-{uuid4()}")
        session.add(target_kb)
        session.add(other_kb)
        session.commit()
        target_project = Project(
            owner_id=user.id,
            kb_id=target_kb.id,
            name=f"target-project-{uuid4()}",
        )
        other_project = Project(
            owner_id=user.id,
            kb_id=other_kb.id,
            name=f"other-project-{uuid4()}",
        )
        session.add(target_project)
        session.add(other_project)
        session.commit()
        target_archive = AgentSession(
            user_id=user.id,
            kb_id=target_kb.id,
            project_id=target_project.id,
            agent_type=AgentType.ARCHIVE,
        )
        other_archive = AgentSession(
            user_id=user.id,
            kb_id=other_kb.id,
            project_id=other_project.id,
            agent_type=AgentType.ARCHIVE,
        )
        policy = AgentSession(
            user_id=user.id,
            kb_id=target_kb.id,
            project_id=None,
            agent_type=AgentType.POLICY,
        )
        session.add(target_archive)
        session.add(other_archive)
        session.add(policy)
        session.commit()
        log = AgentToolCallLog(
            agent_session_id=target_archive.id,
            tool_call_id="target-call",
            tool_name="list_formal_archives",
            status=AgentToolCallStatus.COMPLETED,
        )
        session.add(log)
        session.commit()
        return SimpleNamespace(
            user_id=user.id,
            target_kb_id=target_kb.id,
            other_kb_id=other_kb.id,
            target_project_id=target_project.id,
            other_project_id=other_project.id,
            target_archive_id=target_archive.id,
            target_thread=target_archive.thread_id,
            other_archive_id=other_archive.id,
            other_thread=other_archive.thread_id,
            policy_id=policy.id,
            policy_thread=policy.thread_id,
            target_log_id=log.id,
        )


def _seed_checkpoints(checkpoint_path, scope) -> None:
    """在共享文件中写入目标、其他项目和 POLICY 三个线程。"""
    with build_archive_runtime(
        user_id=scope.user_id,
        project_id=scope.target_project_id,
        kb_id=scope.target_kb_id,
        session_factory=lambda: nullcontext(SimpleNamespace()),
        model=ZeroToolModel("不可见自由文本"),
        judge_model=ZeroToolModel("未调用"),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="目标问题")],
                "user_id": str(scope.user_id),
                "project_id": str(scope.target_project_id),
                "kb_id": str(scope.target_kb_id),
                "tool_call_count": 0,
            },
            thread_id=scope.target_thread,
        )
    with build_archive_runtime(
        user_id=scope.user_id,
        project_id=scope.other_project_id,
        kb_id=scope.other_kb_id,
        session_factory=lambda: nullcontext(SimpleNamespace()),
        model=ZeroToolModel("不可见自由文本"),
        judge_model=ZeroToolModel("未调用"),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="其他问题")],
                "user_id": str(scope.user_id),
                "project_id": str(scope.other_project_id),
                "kb_id": str(scope.other_kb_id),
                "tool_call_count": 0,
            },
            thread_id=scope.other_thread,
        )
    with build_admin_runtime(
        model=ZeroToolModel("制度回答"),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="制度问题")],
                "user_id": str(scope.user_id),
                "kb_id": str(scope.target_kb_id),
            },
            thread_id=scope.policy_thread,
        )


def _checkpoint_exists(checkpoint_path, thread_id: str) -> bool:
    """不构造模型地检查指定线程是否存在。"""
    with open_checkpoint_store(checkpoint_path) as store:
        return store.checkpointer.get_tuple(build_thread_config(thread_id)) is not None


def test_document_gate_stops_before_checkpoint_cleanup(
    engine,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有文档的项目仍返回 409，且不得开始删除线程。"""
    scope = _seed_scope(engine)
    with Session(engine) as session:
        session.add(
            Document(
                kb_id=scope.target_kb_id,
                project_id=scope.target_project_id,
                filename="现存档案.txt",
                storage_path="tests/pytest_docs/existing.txt",
                file_hash="a" * 64,
            )
        )
        session.commit()

    def fail_cleanup(**_kwargs) -> None:
        raise AssertionError("文档门禁前不得清理 Checkpoint")

    monkeypatch.setattr(management, "delete_checkpoint_thread", fail_cleanup, raising=False)
    with Session(engine) as session:
        with pytest.raises(AppError) as exc_info:
            management.delete_empty_project(
                project_id=scope.target_project_id,
                session=session,
                checkpoint_path=tmp_path / "blocked.db",
            )

    assert exc_info.value.code == "PROJECT_HAS_DOCUMENTS"
    with Session(engine) as session:
        assert session.get(Project, scope.target_project_id) is not None
        assert session.get(AgentSession, scope.target_archive_id) is not None


def test_delete_empty_project_removes_only_target_archive_scope(
    engine,
    tmp_path,
) -> None:
    """删除成功必须清理目标 ARCHIVE，保留知识库、POLICY 和其他项目。"""
    scope = _seed_scope(engine)
    checkpoint_path = tmp_path / "shared-delete.db"
    _seed_checkpoints(checkpoint_path, scope)

    with Session(engine) as session:
        management.delete_empty_project(
            project_id=scope.target_project_id,
            session=session,
            checkpoint_path=checkpoint_path,
        )

    with Session(engine) as session:
        assert session.get(Project, scope.target_project_id) is None
        assert session.get(AgentSession, scope.target_archive_id) is None
        assert session.get(AgentToolCallLog, scope.target_log_id) is None
        assert session.get(KnowledgeBase, scope.target_kb_id) is not None
        assert session.get(Project, scope.other_project_id) is not None
        assert session.get(AgentSession, scope.other_archive_id) is not None
        assert session.get(AgentSession, scope.policy_id) is not None
    assert not _checkpoint_exists(checkpoint_path, scope.target_thread)
    assert _checkpoint_exists(checkpoint_path, scope.other_thread)
    assert _checkpoint_exists(checkpoint_path, scope.policy_thread)


def test_checkpoint_failure_rolls_back_business_deletion(
    engine,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 失败时项目、会话和日志必须保留。"""
    scope = _seed_scope(engine)

    def fail_cleanup(**_kwargs) -> None:
        raise OSError("secret checkpoint path")

    monkeypatch.setattr(management, "delete_checkpoint_thread", fail_cleanup, raising=False)
    with Session(engine) as session:
        with pytest.raises(AppError) as exc_info:
            management.delete_empty_project(
                project_id=scope.target_project_id,
                session=session,
                checkpoint_path=tmp_path / "failure.db",
            )

    assert exc_info.value.code == "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"
    assert "secret" not in exc_info.value.message
    with Session(engine) as session:
        assert session.get(Project, scope.target_project_id) is not None
        assert session.get(AgentSession, scope.target_archive_id) is not None
        assert session.get(AgentToolCallLog, scope.target_log_id) is not None


def test_commit_failure_after_checkpoint_delete_is_safe_to_retry(
    engine,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """线程已删但数据库提交失败时，重试仍能完成同一删除。"""
    scope = _seed_scope(engine)
    checkpoint_path = tmp_path / "retry-delete.db"
    _seed_checkpoints(checkpoint_path, scope)

    with Session(engine) as session:
        def fail_commit() -> None:
            raise SQLAlchemyError("secret database failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(AppError) as exc_info:
            management.delete_empty_project(
                project_id=scope.target_project_id,
                session=session,
                checkpoint_path=checkpoint_path,
            )
    assert exc_info.value.code == "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"
    assert not _checkpoint_exists(checkpoint_path, scope.target_thread)
    with Session(engine) as session:
        assert session.get(Project, scope.target_project_id) is not None
        assert session.get(AgentSession, scope.target_archive_id) is not None

    with Session(engine) as session:
        management.delete_empty_project(
            project_id=scope.target_project_id,
            session=session,
            checkpoint_path=checkpoint_path,
        )
    with Session(engine) as session:
        assert session.get(Project, scope.target_project_id) is None
        assert session.exec(
            select(AgentSession).where(AgentSession.project_id == scope.target_project_id)
        ).all() == []
