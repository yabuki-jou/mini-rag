"""验证 Agent Graph 执行的可信范围、审计提交和稳定错误。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.admin.observability import ToolObservation
from app.core.errors import AppError
from app.models import (
    AgentToolCallLog,
    KnowledgeBase,
    User,
)
from app.schemas import AgentExecutionStatus
from app.services.agent import execution
from app.services.agent.sessions import create_agent_session


class FakeRuntime:
    """提供可控的 Graph 状态和执行异常。"""

    def __init__(
        self,
        messages: list[object] | None = None,
        invoke_error: Exception | None = None,
    ) -> None:
        self.state = {"messages": messages or []}
        self.invoke_error = invoke_error
        self.invoke_calls: list[tuple[dict[str, object], str]] = []

    def get_state(self, *, thread_id: str) -> SimpleNamespace:
        """返回当前线程的 Checkpoint 状态。"""
        del thread_id
        return SimpleNamespace(values={"messages": []})

    def invoke(
        self,
        state: dict[str, object],
        *,
        thread_id: str,
    ) -> dict[str, object]:
        """记录可信上下文并返回固定 Graph 状态。"""
        self.invoke_calls.append((state, thread_id))
        if self.invoke_error is not None:
            raise self.invoke_error
        return self.state


@pytest.fixture
def db_session():
    """创建只供 Agent 执行服务单测使用的内存 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def user_and_kb(session: Session) -> tuple[User, object]:
    """创建测试用户及其知识库。"""
    user = User(name="execution_owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="execution-kb")
    session.add(user)
    session.add(knowledge_base)
    session.commit()
    return user, knowledge_base


def test_send_agent_message_injects_scope_and_commits_response(
    db_session: Session,
) -> None:
    """执行应注入可信用户范围、生成响应并提交会话更新时间。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)
    updated_before = agent_session.updated_at
    runtime = FakeRuntime([AIMessage(content="固定回答")])

    response = execution.send_agent_message(
        agent_session,
        "当前问题",
        runtime,
        db_session,
    )

    assert runtime.invoke_calls[0][0]["user_id"] == str(owner.id)
    assert runtime.invoke_calls[0][0]["kb_id"] == str(knowledge_base.id)
    assert response.status == AgentExecutionStatus.COMPLETED
    assert response.answer == "固定回答"
    db_session.refresh(agent_session)
    assert agent_session.updated_at > updated_before


def test_send_agent_message_failure_records_before_commit_and_maps_timeout(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行失败应按审计、提交、稳定错误的顺序处理。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)
    runtime = FakeRuntime(invoke_error=TimeoutError("模拟超时"))
    events: list[str] = []
    observation = ToolObservation(
        tool_call_id="call-failed",
        tool_name="search_company_policy",
        status="FAILED",
        duration_ms=1.0,
        error_code="AGENT_TOOL_TIMEOUT",
    )
    monkeypatch.setattr(
        execution,
        "consume_tool_observations",
        lambda: (observation,),
    )
    original_record = execution.record_failed_observations
    original_commit = execution.commit_execution
    original_raise = execution.raise_agent_execution_error

    def record(*args, **kwargs):
        events.append("record")
        return original_record(*args, **kwargs)

    def commit(*args, **kwargs):
        events.append("commit")
        return original_commit(*args, **kwargs)

    def raise_error(*args, **kwargs):
        events.append("raise")
        return original_raise(*args, **kwargs)

    monkeypatch.setattr(execution, "record_failed_observations", record)
    monkeypatch.setattr(execution, "commit_execution", commit)
    monkeypatch.setattr(execution, "raise_agent_execution_error", raise_error)

    with pytest.raises(AppError) as exc_info:
        execution.send_agent_message(
            agent_session,
            "会触发超时",
            runtime,
            db_session,
        )

    assert exc_info.value.code == "AGENT_TIMEOUT"
    assert events == ["record", "commit", "raise"]
    log = db_session.exec(
        select(AgentToolCallLog).where(
            AgentToolCallLog.agent_session_id == agent_session.id,
        )
    ).one()
    assert log.error_code == "AGENT_TOOL_TIMEOUT"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConnectionError("模拟连接失败"), "AGENT_CONNECTION_FAILED"),
        (RuntimeError("模拟未知失败"), "AGENT_EXECUTION_FAILED"),
    ],
)
def test_send_agent_message_maps_runtime_errors(
    db_session: Session,
    error: Exception,
    code: str,
) -> None:
    """连接和未知执行异常应映射为稳定业务码。"""
    owner, knowledge_base = user_and_kb(db_session)
    agent_session = create_agent_session(owner, knowledge_base.id, db_session)

    with pytest.raises(AppError) as exc_info:
        execution.send_agent_message(
            agent_session,
            "会触发失败",
            FakeRuntime(invoke_error=error),
            db_session,
        )

    assert exc_info.value.code == code
    assert str(error) not in exc_info.value.message
