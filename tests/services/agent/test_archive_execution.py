"""验证 FR-042 P06 档案助手执行、历史和脱敏审计。"""

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.archive.graph import (
    ArchiveAgentDependencyError,
    ArchiveAgentModelOutputError,
    ArchiveToolEvent,
    ArchiveTurnResult,
)
from app.agents.archive.runtime import ArchiveRuntimeResult
from app.core.errors import AppError
from app.models import (
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    AgentType,
    EvidenceLocationType,
    KnowledgeBase,
    Project,
    User,
    utc_now,
)
from app.schemas import ArchiveAgentCitationRead, ArchiveAnswerStatus
from app.services.agent.archive_execution import (
    read_archive_agent_messages,
    read_archive_agent_tool_calls,
    send_archive_agent_message,
)


class FakeArchiveRuntime:
    """提供可控的当前轮结果与 Checkpoint 快照。"""

    def __init__(
        self,
        *,
        result: ArchiveRuntimeResult | None = None,
        messages: list[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.messages = messages or []
        self.error = error
        self.invoke_calls: list[tuple[dict[str, object], str]] = []

    def invoke(
        self,
        state: dict[str, object],
        *,
        thread_id: str,
    ) -> ArchiveRuntimeResult:
        """记录服务端范围并返回固定结果。"""
        self.invoke_calls.append((state, thread_id))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    def get_state(self, *, thread_id: str) -> SimpleNamespace:
        """返回当前测试指定的消息快照。"""
        del thread_id
        if self.error is not None:
            raise self.error
        return SimpleNamespace(values={"messages": self.messages})


@pytest.fixture
def db_session() -> Session:
    """创建启用外键的档案助手服务测试库。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        """在 SQLite 中执行真实会话范围外键。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _archive_session(session: Session) -> AgentSession:
    """创建合法项目、知识库与 ARCHIVE 会话。"""
    user = User(name=f"archive-execution-{uuid4()}")
    session.add(user)
    session.commit()
    knowledge_base = KnowledgeBase(owner_id=user.id, name=f"kb-{uuid4()}")
    session.add(knowledge_base)
    session.commit()
    project = Project(owner_id=user.id, kb_id=knowledge_base.id, name=f"project-{uuid4()}")
    session.add(project)
    session.commit()
    agent_session = AgentSession(
        user_id=user.id,
        project_id=project.id,
        kb_id=knowledge_base.id,
        agent_type=AgentType.ARCHIVE,
    )
    session.add(agent_session)
    session.commit()
    session.refresh(agent_session)
    return agent_session


def _citation() -> ArchiveAgentCitationRead:
    """构造不含内部标识和分数的当前引用。"""
    return ArchiveAgentCitationRead(
        filename="施工方案.txt",
        location_type=EvidenceLocationType.TEXT_LINE_RANGE,
        location_start=3,
        location_end=3,
        excerpt="编制单位：示例公司",
    )


def _result(*events: ArchiveToolEvent) -> ArchiveRuntimeResult:
    """构造一轮已可信最终化的运行结果。"""
    answer = "编制单位是示例公司。"
    return ArchiveRuntimeResult(
        state={"messages": [HumanMessage(content="谁编制？"), AIMessage(content=answer)]},
        turn=ArchiveTurnResult(
            answer_status=ArchiveAnswerStatus.ANSWERED,
            answer=answer,
            citations=(_citation(),),
            answer_kind="ANSWERED",
            tool_call_count=len(events),
            tool_events=tuple(events),
        ),
    )


def test_send_archive_message_injects_scope_commits_safe_audit_and_response(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功轮次应注入三重范围、写脱敏日志并更新会话。"""
    agent_session = _archive_session(db_session)
    updated_before = agent_session.updated_at
    request_id = uuid4()
    monkeypatch.setattr(
        "app.services.agent.archive_execution.get_request_id",
        lambda: str(request_id),
    )
    event = ArchiveToolEvent(
        tool_call_id="evidence-call",
        tool_name="search_confirmed_archive_evidence",
        status="COMPLETED",
        error_code=None,
        attempt_count=2,
        duration_ms=12.5,
        arguments_summary={"query_provided": True, "query_length": 5},
        result_summary={"found": True, "result_count": 1},
    )
    runtime = FakeArchiveRuntime(result=_result(event))

    response = send_archive_agent_message(
        agent_session=agent_session,
        message="  谁编制？  ",
        runtime=runtime,
        session=db_session,
    )

    state, thread_id = runtime.invoke_calls[0]
    assert thread_id == agent_session.thread_id
    assert state["user_id"] == str(agent_session.user_id)
    assert state["project_id"] == str(agent_session.project_id)
    assert state["kb_id"] == str(agent_session.kb_id)
    assert state["tool_call_count"] == 0
    assert state["messages"][0].content == "谁编制？"
    assert response.model_dump() == {
        "session_id": agent_session.id,
        "answer_status": ArchiveAnswerStatus.ANSWERED,
        "answer": "编制单位是示例公司。",
        "citations": [_citation().model_dump()],
        "request_id": request_id,
    }
    db_session.refresh(agent_session)
    assert agent_session.updated_at > updated_before
    log = db_session.exec(select(AgentToolCallLog)).one()
    assert log.tool_call_id == "evidence-call"
    assert log.status == AgentToolCallStatus.COMPLETED
    assert json.loads(log.arguments_summary_json or "null") == {
        "query_provided": True,
        "query_length": 5,
    }
    assert json.loads(log.result_summary_json or "null") == {
        "found": True,
        "result_count": 1,
    }
    assert log.duration_ms == 12.5


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ArchiveAgentModelOutputError("内部细节"), "ARCHIVE_AGENT_MODEL_OUTPUT_INVALID"),
        (ArchiveAgentDependencyError("内部细节"), "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"),
        (TimeoutError("内部超时地址"), "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_send_archive_message_maps_failures_and_persists_existing_events(
    db_session: Session,
    error: Exception,
    expected_code: str,
) -> None:
    """越界与真实依赖失败应区分，但都不泄露异常正文。"""
    agent_session = _archive_session(db_session)
    updated_before = agent_session.updated_at
    failed_event = ArchiveToolEvent(
        tool_call_id="failed-call",
        tool_name="list_formal_archives",
        status="FAILED",
        error_code="ARCHIVE_AGENT_TOOL_TIMEOUT",
        attempt_count=2,
        duration_ms=8.0,
        arguments_summary={"filter_names": [], "page": 1, "page_size": 20},
        result_summary=None,
    )
    if isinstance(error, (ArchiveAgentModelOutputError, ArchiveAgentDependencyError)):
        error.tool_events = (failed_event,)
    runtime = FakeArchiveRuntime(error=error)

    with pytest.raises(AppError) as exc_info:
        send_archive_agent_message(
            agent_session=agent_session,
            message="触发失败",
            runtime=runtime,
            session=db_session,
        )

    assert exc_info.value.code == expected_code
    assert "内部" not in exc_info.value.message
    db_session.refresh(agent_session)
    assert agent_session.updated_at == updated_before
    logs = db_session.exec(select(AgentToolCallLog)).all()
    if isinstance(error, (ArchiveAgentModelOutputError, ArchiveAgentDependencyError)):
        assert len(logs) == 1
        assert logs[0].error_code == "ARCHIVE_AGENT_TOOL_TIMEOUT"
    else:
        assert logs == []


def test_archive_history_keeps_only_complete_rounds_and_never_restores_citations(
    db_session: Session,
) -> None:
    """历史与模型投影共用完整轮次规则，历史引用固定为空。"""
    agent_session = _archive_session(db_session)
    runtime = FakeArchiveRuntime(
        messages=[
            HumanMessage(content="孤立问题"),
            ToolMessage(content="{}", tool_call_id="orphan"),
            HumanMessage(content="完整问题"),
            AIMessage(content="", tool_calls=[{"id": "internal", "name": "x", "args": {}}]),
            ToolMessage(content="{}", tool_call_id="internal"),
            AIMessage(content="可信回答"),
            HumanMessage(content="尾部未完成"),
        ]
    )

    messages = read_archive_agent_messages(agent_session, runtime)

    assert [item.model_dump() for item in messages] == [
        {"role": "USER", "content": "完整问题", "citations": []},
        {"role": "ASSISTANT", "content": "可信回答", "citations": []},
    ]


def test_archive_audit_read_is_sorted_and_rejects_non_whitelisted_content(
    db_session: Session,
) -> None:
    """日志读取必须正序且不允许脏数据越过白名单。"""
    agent_session = _archive_session(db_session)
    now = utc_now()
    db_session.add(
        AgentToolCallLog(
            agent_session_id=agent_session.id,
            tool_call_id="later",
            tool_name="search_confirmed_archive_evidence",
            status=AgentToolCallStatus.COMPLETED,
            arguments_summary_json='{"query_provided":true,"query_length":3}',
            result_summary_json='{"found":true,"result_count":1}',
            created_at=now + timedelta(seconds=1),
        )
    )
    db_session.add(
        AgentToolCallLog(
            agent_session_id=agent_session.id,
            tool_call_id="earlier",
            tool_name="list_formal_archives",
            status=AgentToolCallStatus.FAILED,
            arguments_summary_json=None,
            result_summary_json=None,
            error_code="ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID",
            created_at=now,
        )
    )
    db_session.commit()

    records = read_archive_agent_tool_calls(agent_session, db_session)

    assert [record.tool_call_id for record in records] == ["earlier", "later"]
    assert records[1].arguments_summary == {"query_provided": True, "query_length": 3}

    dirty = db_session.exec(
        select(AgentToolCallLog).where(AgentToolCallLog.tool_call_id == "later")
    ).one()
    dirty.arguments_summary_json = '{"query_provided":true,"query":"秘密"}'
    db_session.add(dirty)
    db_session.commit()
    with pytest.raises(AppError) as exc_info:
        read_archive_agent_tool_calls(agent_session, db_session)
    assert exc_info.value.code == "ARCHIVE_AGENT_TOOL_LOG_INVALID"


def test_archive_audit_upsert_keeps_one_row_for_same_tool_call(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一会话和 Tool Call ID 重放时不得重复写日志。"""
    agent_session = _archive_session(db_session)
    monkeypatch.setattr(
        "app.services.agent.archive_execution.get_request_id",
        lambda: str(uuid4()),
    )
    event = ArchiveToolEvent(
        tool_call_id="same-call",
        tool_name="list_formal_archives",
        status="COMPLETED",
        error_code=None,
        attempt_count=2,
        duration_ms=20.0,
        arguments_summary={"filter_names": [], "page": 1, "page_size": 20},
        result_summary={"found": True, "result_count": 1},
    )
    runtime = FakeArchiveRuntime(result=_result(event))

    send_archive_agent_message(
        agent_session=agent_session,
        message="第一次",
        runtime=runtime,
        session=db_session,
    )
    send_archive_agent_message(
        agent_session=agent_session,
        message="客户端重发",
        runtime=runtime,
        session=db_session,
    )

    logs = db_session.exec(select(AgentToolCallLog)).all()
    assert len(logs) == 1
    assert logs[0].duration_ms == 20.0


def test_archive_audit_read_maps_database_failure_to_dependency() -> None:
    """工具日志的真实数据库失败应映射为档案助手稳定 503。"""
    agent_session = AgentSession(
        user_id=uuid4(),
        project_id=uuid4(),
        kb_id=uuid4(),
        agent_type=AgentType.ARCHIVE,
    )

    class FailingSession:
        """模拟在查询阶段失败的 PostgreSQL Session。"""

        def exec(self, _statement):
            raise ConnectionError("secret database address")

    with pytest.raises(AppError) as exc_info:
        read_archive_agent_tool_calls(agent_session, FailingSession())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"
    assert "secret" not in exc_info.value.message


def test_send_archive_message_observes_only_safe_eval_projections(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测观测应覆盖工具路由和最终响应，但排除范围 ID、调用 ID 与原始问题。"""
    agent_session = _archive_session(db_session)
    event = ArchiveToolEvent(
        tool_call_id="private-call-id",
        tool_name="search_confirmed_archive_evidence",
        status="COMPLETED",
        error_code=None,
        attempt_count=1,
        duration_ms=4.0,
        arguments_summary={"query_provided": True, "query_length": 6},
        result_summary={"found": True, "result_count": 1},
    )
    observed: dict[str, tuple[str, object]] = {}

    def fake_wrap(data, *, purpose, name, description):
        assert description
        observed[name] = (purpose, data)
        return data

    monkeypatch.setattr(
        "app.services.agent.archive_execution.eval_wrap",
        fake_wrap,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.agent.archive_execution.get_request_id",
        lambda: str(uuid4()),
    )

    send_archive_agent_message(
        agent_session=agent_session,
        message="原始敏感查询",
        runtime=FakeArchiveRuntime(result=_result(event)),
        session=db_session,
    )

    assert set(observed) == {
        "archive_agent_tool_calls",
        "archive_agent_routing_decision",
        "archive_agent_response",
    }
    assert observed["archive_agent_tool_calls"][0] == "state"
    assert observed["archive_agent_routing_decision"][0] == "state"
    assert observed["archive_agent_response"][0] == "output"
    serialized = json.dumps(observed, ensure_ascii=False, default=str)
    for forbidden in (
        str(agent_session.user_id),
        str(agent_session.project_id),
        str(agent_session.kb_id),
        str(agent_session.id),
        agent_session.thread_id,
        "private-call-id",
        "原始敏感查询",
        "document_id",
        "chunk_id",
        "score",
    ):
        assert forbidden not in serialized
