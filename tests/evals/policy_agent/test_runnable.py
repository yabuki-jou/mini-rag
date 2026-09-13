"""验证制度 Agent 评测 Runnable 的输入、隔离和 HTTP 链路。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from evals.policy_agent.runnable import PolicyAgentArgs, PolicyAgentRunnable
from app.main import app


def test_policy_agent_args_validates_user_message() -> None:
    """评测输入只允许非空且长度受限的用户问题。"""

    assert PolicyAgentArgs(user_message="制度问题").user_message == "制度问题"
    with pytest.raises(ValidationError):
        PolicyAgentArgs(user_message=" ")


class FakeRuntime:
    """以确定性回答隔离真实模型，但保留生产 Runtime 调用形状。"""

    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> "FakeRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def get_state(self, *, thread_id: str) -> SimpleNamespace:
        del thread_id
        return SimpleNamespace(values={"messages": []})

    def invoke(self, state: dict, *, thread_id: str) -> dict:
        del thread_id
        return {
            "messages": [*state["messages"], AIMessage(content="制度回答")],
            "user_id": state["user_id"],
            "kb_id": state["kb_id"],
        }


def test_setup_and_teardown_restore_fastapi_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """临时业务库、Checkpoint 路径和 FastAPI 覆盖应在 teardown 后恢复。"""

    original_overrides = dict(app.dependency_overrides)
    builder_calls: list[dict] = []

    def fake_builder(**kwargs):
        builder_calls.append(kwargs)
        return FakeRuntime()

    monkeypatch.setattr("app.agents.admin.runtime.build_admin_runtime", fake_builder)
    runnable = PolicyAgentRunnable.create()

    async def exercise() -> None:
        await runnable.setup()
        assert runnable._business_engine is not None
        assert runnable._checkpoint_path.suffix == ".db"
        assert set(app.dependency_overrides) != set(original_overrides)
        await runnable.run(PolicyAgentArgs(user_message="测试制度链路"))
        assert builder_calls == [{"checkpoint_path": runnable._checkpoint_path}]
        await runnable.teardown()

    asyncio.run(exercise())
    assert app.dependency_overrides == original_overrides


def test_run_uses_bearer_http_chain_and_independent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runnable 应通过注册、登录、知识库、会话和消息 HTTP 链路完成一题。"""

    monkeypatch.setattr(
        "app.agents.admin.runtime.build_admin_runtime",
        lambda **kwargs: FakeRuntime(),
    )
    runnable = PolicyAgentRunnable.create()

    async def exercise() -> None:
        await runnable.setup()
        try:
            await runnable.run(PolicyAgentArgs(user_message="年假制度是什么？"))
            assert UUID(runnable.last_user_id)
            assert UUID(runnable.last_kb_id)
            assert UUID(runnable.last_session_id)
            assert not hasattr(runnable, "last_access_token")
            assert runnable.last_message_status == 200
        finally:
            await runnable.teardown()

    asyncio.run(exercise())
