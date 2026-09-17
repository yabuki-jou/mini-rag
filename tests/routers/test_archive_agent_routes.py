"""验证 FR-042 项目档案助手四个公开端点。"""

from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import (
    AgentSession,
    AgentType,
    EvidenceLocationType,
    KnowledgeBase,
    Project,
    User,
)
from app.agents.archive.runtime import build_archive_runtime as real_build_archive_runtime
from app.core.errors import AppError
from app.routers import archive_agent as archive_agent_router
from app.schemas import ArchiveRetrievalItemRead, ArchiveRetrievalResponse
from app.services.archive import catalog as catalog_service
from app.services.archive import retrieval as retrieval_service
from tests.support.auth import auth_headers


@pytest.fixture
def archive_agent_api() -> Generator[tuple[TestClient, Engine], None, None]:
    """提供启用外键的隔离数据库和真实 FastAPI 路由。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        """让会话范围约束在 SQLite 路由测试中真实生效。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        """把 HTTP 请求绑定到隔离测试数据库。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    try:
        yield client, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _create_user_and_project(
    engine: Engine,
    *,
    name: str,
) -> tuple[UUID, Project]:
    """直接创建用户、项目私有知识库和项目记录。"""
    user = User(name=name)
    user_id = user.id
    with Session(engine) as session:
        session.add(user)
        session.commit()

        knowledge_base = KnowledgeBase(owner_id=user.id, name=f"{name}-kb")
        session.add(knowledge_base)
        session.commit()

        project = Project(
            owner_id=user.id,
            kb_id=knowledge_base.id,
            name=f"{name}-project",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
    return user_id, project


def test_create_archive_agent_session_uses_empty_body_and_hides_scope(
    archive_agent_api: tuple[TestClient, Engine],
) -> None:
    """创建端点只接收空对象，并返回不含内部范围的最小响应。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="owner")

    response = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    )

    assert response.status_code == 201
    payload = response.json()
    assert set(payload) == {"id", "project_id", "created_at", "updated_at"}
    assert payload["project_id"] == str(project.id)
    assert payload["created_at"] == payload["updated_at"]
    with Session(engine) as session:
        record = session.get(AgentSession, UUID(payload["id"]))
        assert record is not None
        assert record.user_id == user_id
        assert record.project_id == project.id
        assert record.kb_id == project.kb_id
        assert record.agent_type == AgentType.ARCHIVE
        assert record.thread_id


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"user_id": str(uuid4())},
        {"project_id": str(uuid4())},
        {"kb_id": str(uuid4())},
        {"thread_id": "client-thread"},
        {"agent_type": "POLICY"},
    ],
)
def test_create_archive_agent_session_rejects_nonempty_or_invalid_body(
    archive_agent_api: tuple[TestClient, Engine],
    body: object,
) -> None:
    """缺少对象请求体、非对象或任何范围字段都必须在写库前返回 422。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="validation-owner")
    request_kwargs = (
        {"content": b"", "headers": auth_headers(engine, user_id)}
        if body is None
        else {"json": body, "headers": auth_headers(engine, user_id)}
    )

    response = client.post(
        f"/projects/{project.id}/agent-sessions",
        **request_kwargs,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with Session(engine) as session:
        assert session.exec(select(AgentSession)).all() == []


def test_create_archive_agent_session_preserves_project_404_and_403(
    archive_agent_api: tuple[TestClient, Engine],
) -> None:
    """项目不存在与项目越权必须沿用既有稳定错误边界。"""
    client, engine = archive_agent_api
    owner_id, project = _create_user_and_project(engine, name="project-owner")
    other_id, _ = _create_user_and_project(engine, name="other-user")

    missing = client.post(
        f"/projects/{uuid4()}/agent-sessions",
        headers=auth_headers(engine, owner_id),
        json={},
    )
    forbidden = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, other_id),
        json={},
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PROJECT_FORBIDDEN"


def test_p03_openapi_contains_only_archive_session_creation() -> None:
    """P06 应将四端点及其请求响应 Schema 全部写入 OpenAPI。"""
    openapi = app.openapi()
    create_path = "/projects/{project_id}/agent-sessions"
    assert set(openapi["paths"][create_path]) == {"post"}
    operation = openapi["paths"][create_path]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ArchiveAgentSessionCreate"
    }
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ArchiveAgentSessionRead"
    }
    assert "422" in operation["responses"]
    messages_path = f"{create_path}/{{session_id}}/messages"
    tool_calls_path = f"{create_path}/{{session_id}}/tool-calls"
    assert set(openapi["paths"][messages_path]) == {"post", "get"}
    assert set(openapi["paths"][tool_calls_path]) == {"get"}
    message_post = openapi["paths"][messages_path]["post"]
    assert message_post["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ArchiveAgentMessageCreate"
    }
    assert message_post["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ArchiveAgentResponse"
    }
    assert "422" in message_post["responses"]


class RecordingChatModel:
    """记录每轮模型输入并返回预设消息。"""

    def __init__(self, responses: Sequence[AIMessage]) -> None:
        self.responses = list(responses)
        self.invocations: list[list[Any]] = []

    def bind_tools(self, _tools: Sequence[Any]) -> "RecordingChatModel":
        """模拟绑定工具后仍使用当前模型。"""
        return self

    def invoke(self, messages: Any) -> AIMessage:
        """保存输入并消费一个固定响应。"""
        self.invocations.append(list(messages) if not isinstance(messages, str) else [messages])
        if not self.responses:
            raise AssertionError("模拟模型没有剩余响应。")
        return self.responses.pop(0)


def _install_real_archive_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    checkpoint_path: Path,
    model: RecordingChatModel,
    judge_model: RecordingChatModel | None = None,
) -> None:
    """让路由使用真实 Graph/Checkpoint 和确定性模型。"""

    def build_runtime(**kwargs: Any):
        return real_build_archive_runtime(
            **kwargs,
            model=model,
            judge_model=judge_model or RecordingChatModel([]),
            checkpoint_path=checkpoint_path,
        )

    monkeypatch.setattr(
        archive_agent_router,
        "build_archive_runtime",
        build_runtime,
        raising=False,
    )


def test_message_history_and_tool_routes_form_two_round_checkpoint_loop(
    archive_agent_api: tuple[TestClient, Engine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续两轮必须复用 thread_id，第二轮看到第一轮完整历史。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="two-round-owner")
    model = RecordingChatModel(
        [AIMessage(content="第一轮自由文本"), AIMessage(content="第二轮自由文本")]
    )
    _install_real_archive_runtime(
        monkeypatch,
        checkpoint_path=tmp_path / "archive-api.db",
        model=model,
    )
    created = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    ).json()
    base_path = f"/projects/{project.id}/agent-sessions/{created['id']}"

    first = client.post(
        f"{base_path}/messages",
        headers=auth_headers(engine, user_id),
        json={"message": "  第一个问题  "},
    )
    second = client.post(
        f"{base_path}/messages",
        headers=auth_headers(engine, user_id),
        json={"message": "第二个问题"},
    )
    history = client.get(f"{base_path}/messages", headers=auth_headers(engine, user_id))
    tool_calls = client.get(
        f"{base_path}/tool-calls",
        headers=auth_headers(engine, user_id),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    for response in (first, second):
        payload = response.json()
        assert set(payload) == {
            "session_id",
            "answer_status",
            "answer",
            "citations",
            "request_id",
        }
        assert payload["answer_status"] == "REFUSED_NO_EVIDENCE"
        assert payload["answer"] == "正式档案中没有足够依据。"
        assert payload["citations"] == []
        UUID(payload["request_id"])
    second_input = [message.content for message in model.invocations[1][1:]]
    assert second_input == [
        "第一个问题",
        "正式档案中没有足够依据。",
        "第二个问题",
    ]
    assert history.status_code == 200
    assert history.json() == [
        {"role": "USER", "content": "第一个问题", "citations": []},
        {"role": "ASSISTANT", "content": "正式档案中没有足够依据。", "citations": []},
        {"role": "USER", "content": "第二个问题", "citations": []},
        {"role": "ASSISTANT", "content": "正式档案中没有足够依据。", "citations": []},
    ]
    assert tool_calls.status_code == 200
    assert tool_calls.json() == []


def test_evidence_message_returns_safe_citation_and_persists_safe_tool_log(
    archive_agent_api: tuple[TestClient, Engine],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据链路的 HTTP 引用和工具日志均不得泄露内部标识或查询原文。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="evidence-owner")
    candidate = ArchiveRetrievalItemRead(
        chunk_id="a" * 64,
        document_id=uuid4(),
        filename="施工方案.txt",
        location_type=EvidenceLocationType.TEXT_LINE_RANGE,
        location_start=3,
        location_end=3,
        excerpt="编制单位：示例公司",
        score=0.91,
        reranker_score=0.87,
    )
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: ArchiveRetrievalResponse(
            items=[candidate],
            requested_top_k=8,
            returned_count=1,
        ),
    )
    monkeypatch.setattr(
        catalog_service,
        "list_agent_confirmed_document_titles",
        lambda **_: {candidate.document_id: "施工方案"},
    )
    model = RecordingChatModel(
        [
            AIMessage(
                content="不可见自由文本",
                tool_calls=[
                    {
                        "id": "evidence-http-call",
                        "name": "search_confirmed_archive_evidence",
                        "args": {"query": "编制单位是谁？"},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="不可见最终自由文本"),
        ]
    )
    judge = RecordingChatModel(
        [
            AIMessage(
                content='{"decision":"ANSWERED","answer":"编制单位是示例公司。","citation_numbers":[1]}'
            )
        ]
    )
    _install_real_archive_runtime(
        monkeypatch,
        checkpoint_path=tmp_path / "archive-evidence-api.db",
        model=model,
        judge_model=judge,
    )
    created = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    ).json()
    base_path = f"/projects/{project.id}/agent-sessions/{created['id']}"

    response = client.post(
        f"{base_path}/messages",
        headers=auth_headers(engine, user_id),
        json={"message": "施工方案的编制单位是谁？"},
    )
    logs = client.get(f"{base_path}/tool-calls", headers=auth_headers(engine, user_id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "编制单位是示例公司。"
    assert payload["citations"] == [
        {
            "filename": "施工方案.txt",
            "location_type": "TEXT_LINE_RANGE",
            "location_start": 3,
            "location_end": 3,
            "excerpt": "编制单位：示例公司",
        }
    ]
    assert logs.status_code == 200
    assert len(logs.json()) == 1
    log = logs.json()[0]
    assert log["tool_call_id"] == "evidence-http-call"
    assert log["arguments_summary"] == {
        "query_provided": True,
        "query_length": 7,
    }
    assert log["result_summary"] == {"found": True, "result_count": 1}
    serialized = response.text + logs.text
    for forbidden in (
        "document_id",
        "chunk_id",
        "reranker_score",
        str(candidate.document_id),
        candidate.chunk_id,
        "编制单位是谁？",
    ):
        assert forbidden not in serialized


def test_invalid_message_stops_before_session_lookup_and_runtime(
    archive_agent_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空白消息的 422 不得查询会话或打开 Checkpoint Runtime。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="invalid-message-owner")

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("422 前不得查询会话或构造 Runtime")

    monkeypatch.setattr(archive_agent_router, "find_archive_agent_session", fail, raising=False)
    monkeypatch.setattr(archive_agent_router, "build_archive_runtime", fail, raising=False)

    response = client.post(
        f"/projects/{project.id}/agent-sessions/{uuid4()}/messages",
        headers=auth_headers(engine, user_id),
        json={"message": " \r\n\t "},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_runtime_construction_failure_maps_to_archive_dependency_error(
    archive_agent_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint 或模型 Runtime 构造失败不得落入通用 500。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="runtime-failure-owner")
    created = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    ).json()

    def fail_runtime(**_kwargs: Any) -> None:
        raise ConnectionError("secret checkpoint path")

    monkeypatch.setattr(archive_agent_router, "build_archive_runtime", fail_runtime)
    response = client.get(
        f"/projects/{project.id}/agent-sessions/{created['id']}/messages",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"
    assert "secret" not in response.text


def test_runtime_construction_app_error_maps_to_archive_dependency_error(
    archive_agent_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型配置类 AppError 也必须投影为档案助手冻结错误码。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="runtime-app-error-owner")
    created = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    ).json()

    def fail_runtime(**_kwargs: Any) -> None:
        raise AppError(503, "DEEPSEEK_NOT_CONFIGURED", "内部模型配置细节")

    monkeypatch.setattr(archive_agent_router, "build_archive_runtime", fail_runtime)
    response = client.get(
        f"/projects/{project.id}/agent-sessions/{created['id']}/messages",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"
    assert "内部模型配置细节" not in response.text


def test_runtime_scope_preserves_execution_business_error(
    archive_agent_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime 已打开后，执行服务的冻结业务错误必须保持原样。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="runtime-body-error-owner")
    created = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    ).json()

    monkeypatch.setattr(
        archive_agent_router,
        "build_archive_runtime",
        lambda **_kwargs: nullcontext(object()),
    )

    def fail_read(*_args: Any, **_kwargs: Any) -> None:
        raise AppError(503, "ARCHIVE_AGENT_MODEL_OUTPUT_INVALID", "模型输出无效。")

    monkeypatch.setattr(archive_agent_router, "read_archive_agent_messages", fail_read)
    response = client.get(
        f"/projects/{project.id}/agent-sessions/{created['id']}/messages",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ARCHIVE_AGENT_MODEL_OUTPUT_INVALID"


def test_runtime_close_app_error_maps_to_archive_dependency_error(
    archive_agent_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime 关闭阶段的 AppError 不得泄露内部依赖错误码。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name="runtime-close-error-owner")
    created = client.post(
        f"/projects/{project.id}/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={},
    ).json()

    class FailingCloseRuntime:
        """提供成功打开、关闭失败的最小 Runtime。"""

        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: Any) -> None:
            raise AppError(503, "CHECKPOINT_CLOSE_FAILED", "内部关闭细节")

    monkeypatch.setattr(
        archive_agent_router,
        "build_archive_runtime",
        lambda **_kwargs: FailingCloseRuntime(),
    )
    monkeypatch.setattr(
        archive_agent_router,
        "read_archive_agent_messages",
        lambda *_args, **_kwargs: [],
    )
    response = client.get(
        f"/projects/{project.id}/agent-sessions/{created['id']}/messages",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE"
    assert "内部关闭细节" not in response.text


@pytest.mark.parametrize("suffix", ["messages:post", "messages:get", "tool-calls:get"])
def test_archive_routes_reject_policy_session_before_runtime_or_log_read(
    archive_agent_api: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    """三个 ARCHIVE 入口都必须把 POLICY 会话按不存在处理。"""
    client, engine = archive_agent_api
    user_id, project = _create_user_and_project(engine, name=f"policy-reject-{suffix}")
    with Session(engine) as session:
        policy_session = AgentSession(
            user_id=user_id,
            kb_id=project.kb_id,
            agent_type=AgentType.POLICY,
            project_id=None,
        )
        session.add(policy_session)
        session.commit()
        policy_id = policy_session.id

    def fail_runtime(**_kwargs: Any) -> None:
        raise AssertionError("POLICY ID 不得打开 Archive Runtime")

    monkeypatch.setattr(
        archive_agent_router,
        "build_archive_runtime",
        fail_runtime,
        raising=False,
    )
    name, method = suffix.split(":")
    url = f"/projects/{project.id}/agent-sessions/{policy_id}/{name}"
    response = (
        client.post(url, headers=auth_headers(engine, user_id), json={"message": "问题"})
        if method == "post"
        else client.get(url, headers=auth_headers(engine, user_id))
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ARCHIVE_AGENT_SESSION_NOT_FOUND"
