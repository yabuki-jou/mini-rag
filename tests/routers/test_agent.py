"""验证企业知识库 Agent HTTP 接口、会话隔离和响应转换。"""

import json
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.agents.admin.observability import ToolObservation
from app.db import get_session
from app.dependencies import get_admin_agent_runtime
from app.main import app
from app.models import AgentSession, KnowledgeBase, User
from app.services import agent_service
from tests.support.auth import auth_headers


class FakeAgentRuntime:
    """为 API 测试保存可控 Checkpoint 状态并记录调用参数。"""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"messages": []}
        self.next_state: dict[str, Any] | None = None
        self.invoke_calls: list[tuple[dict[str, Any], str]] = []

    def get_state(self, *, thread_id: str) -> Any:
        del thread_id
        return SimpleNamespace(values=self.state)

    def invoke(self, state: dict[str, Any], *, thread_id: str) -> dict[str, Any]:
        self.invoke_calls.append((state, thread_id))
        if self.next_state is None:
            raise AssertionError("测试没有设置 next_state。")
        self.state = self.next_state
        return self.state


@pytest.fixture
def agent_api() -> Generator[tuple[TestClient, Engine, FakeAgentRuntime], None, None]:
    """创建隔离业务数据库和可控 Agent Runtime。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    runtime = FakeAgentRuntime()

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    def override_runtime() -> Generator[FakeAgentRuntime, None, None]:
        yield runtime

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_admin_agent_runtime] = override_runtime
    client = TestClient(app)
    try:
        yield client, engine, runtime
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_user_and_kb(engine: Engine, name: str = "owner") -> tuple[UUID, UUID]:
    """创建测试用户及其知识库。"""
    user = User(name=name)
    knowledge_base = KnowledgeBase(owner_id=user.id, name=f"{name}-制度库")
    user_id = user.id
    kb_id = knowledge_base.id
    with Session(engine) as session:
        session.add(user)
        session.add(knowledge_base)
        session.commit()
    return user_id, kb_id


def create_agent_session_via_api(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
    kb_id: UUID,
) -> dict:
    response = client.post(
        "/agent-sessions",
        headers=auth_headers(engine, user_id),
        json={"kb_id": str(kb_id)},
    )
    assert response.status_code == 201
    return response.json()


def test_create_agent_session_binds_owned_knowledge_base(
    agent_api: tuple[TestClient, Engine, FakeAgentRuntime],
) -> None:
    """创建接口应保存可信用户、知识库和唯一 Graph thread。"""
    client, engine, _ = agent_api
    user_id, kb_id = create_user_and_kb(engine)
    data = create_agent_session_via_api(client, engine, user_id, kb_id)
    assert data["kb_id"] == str(kb_id)
    assert data["thread_id"]
    assert "user_id" not in data
    with Session(engine) as session:
        record = session.get(AgentSession, UUID(data["id"]))
        assert record is not None
        assert record.user_id == user_id


def test_create_agent_session_rejects_other_users_knowledge_base(
    agent_api: tuple[TestClient, Engine, FakeAgentRuntime],
) -> None:
    """当前用户不能绑定其他用户的知识库。"""
    client, engine, _ = agent_api
    owner_id, kb_id = create_user_and_kb(engine, "owner")
    other_id, _ = create_user_and_kb(engine, "other")
    response = client.post(
        "/agent-sessions",
        headers=auth_headers(engine, other_id),
        json={"kb_id": str(kb_id)},
    )
    assert owner_id != other_id
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_FORBIDDEN"


def test_message_endpoint_injects_trusted_scope_and_returns_answer(
    agent_api: tuple[TestClient, Engine, FakeAgentRuntime],
) -> None:
    """消息接口应从会话注入身份范围，并返回请求 ID。"""
    client, engine, runtime = agent_api
    user_id, kb_id = create_user_and_kb(engine)
    agent_session = create_agent_session_via_api(client, engine, user_id, kb_id)
    runtime.next_state = {
        "messages": [
            HumanMessage(content="你好"),
            AIMessage(content="你好，我可以协助查询企业制度。"),
        ],
        "user_id": str(user_id),
        "kb_id": str(kb_id),
    }
    response = client.post(
        f"/agent-sessions/{agent_session['id']}/messages",
        headers={**auth_headers(engine, user_id), "X-Request-ID": "agent-test-1"},
        json={"message": "你好"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["answer"] == "你好，我可以协助查询企业制度。"
    assert response.json()["request_id"] == "agent-test-1"
    assert "pending_action" not in response.json()
    invoke_state, thread_id = runtime.invoke_calls[0]
    assert invoke_state["user_id"] == str(user_id)
    assert invoke_state["kb_id"] == str(kb_id)
    assert thread_id == agent_session["thread_id"]


def test_policy_answer_returns_sources_and_redacted_tool_log(
    agent_api: tuple[TestClient, Engine, FakeAgentRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """制度回答应返回引用，工具日志不得保存正文或身份字段。"""
    client, engine, runtime = agent_api
    user_id, kb_id = create_user_and_kb(engine)
    agent_session = create_agent_session_via_api(client, engine, user_id, kb_id)
    document_id = uuid4()
    tool_call = {
        "name": "search_company_policy",
        "args": {
            "query": "项目资料如何归档？",
            "user_id": str(user_id),
            "kb_id": str(kb_id),
            "token": "secret-token",
        },
        "id": "policy-http-1",
        "type": "tool_call",
    }
    tool_payload = {
        "query": "项目资料如何归档？",
        "found": True,
        "message": None,
        "results": [{
            "chunk_id": "a" * 64,
            "document_id": str(document_id),
            "document_name": "项目管理制度.pdf",
            "page": 2,
            "content": "项目资料需要归档。",
            "score": 0.88,
        }],
    }
    runtime.next_state = {
        "messages": [
            HumanMessage(content="项目资料如何归档？"),
            AIMessage(content="", tool_calls=[tool_call]),
            ToolMessage(
                content=json.dumps(tool_payload, ensure_ascii=False),
                tool_call_id="policy-http-1",
                name="search_company_policy",
            ),
            AIMessage(content="根据制度，项目资料需要归档。[S1]"),
        ]
    }
    monkeypatch.setattr(
        agent_service,
        "consume_tool_observations",
        lambda: (
            ToolObservation(
                tool_call_id="policy-http-1",
                tool_name="search_company_policy",
                status="COMPLETED",
                duration_ms=12.5,
                error_code=None,
            ),
        ),
    )
    response = client.post(
        f"/agent-sessions/{agent_session['id']}/messages",
        headers=auth_headers(engine, user_id),
        json={"message": "项目资料如何归档？"},
    )
    logs_response = client.get(
        f"/agent-sessions/{agent_session['id']}/tool-calls",
        headers=auth_headers(engine, user_id),
    )
    assert response.json()["sources"][0]["document_id"] == str(document_id)
    log_data = logs_response.json()[0]
    assert log_data["status"] == "COMPLETED"
    assert log_data["arguments_summary"] == {
        "query_provided": True,
        "query_length": 9,
    }
    assert log_data["result_summary"] == {"found": True, "result_count": 1}
    serialized = json.dumps(log_data, ensure_ascii=False)
    assert "项目资料需要归档" not in serialized
    assert "user_id" not in serialized
    assert "secret-token" not in serialized


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError("secret timeout detail"), "AGENT_TIMEOUT"),
        (ConnectionError("secret host detail"), "AGENT_CONNECTION_FAILED"),
        (RuntimeError("secret internal detail"), "AGENT_EXECUTION_FAILED"),
    ],
)
def test_agent_execution_errors_are_safe(
    agent_api: tuple[TestClient, Engine, FakeAgentRuntime],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
) -> None:
    """Runtime 异常应返回稳定分类，不能暴露异常正文。"""
    client, engine, runtime = agent_api
    user_id, kb_id = create_user_and_kb(engine)
    agent_session = create_agent_session_via_api(client, engine, user_id, kb_id)

    def raise_error(*_: Any, **__: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(runtime, "invoke", raise_error)
    response = client.post(
        f"/agent-sessions/{agent_session['id']}/messages",
        headers=auth_headers(engine, user_id),
        json={"message": "查询信息"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == expected_code
    assert "secret" not in json.dumps(response.json(), ensure_ascii=False)


@pytest.mark.parametrize("suffix", ["messages", "tool-calls"])
def test_other_user_cannot_read_agent_session(
    agent_api: tuple[TestClient, Engine, FakeAgentRuntime],
    suffix: str,
) -> None:
    """其他用户不能读取 Agent 历史或工具调用日志。"""
    client, engine, _ = agent_api
    owner_id, kb_id = create_user_and_kb(engine, "owner")
    other_id, _ = create_user_and_kb(engine, "other")
    agent_session = create_agent_session_via_api(client, engine, owner_id, kb_id)
    response = client.get(
        f"/agent-sessions/{agent_session['id']}/{suffix}",
        headers=auth_headers(engine, other_id),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_SESSION_FORBIDDEN"
