"""验证企业知识库 Agent 的状态路由、工具闭环和 SQLite Checkpoint。"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app.agents.admin.graph import AGENT_TOOLS, build_admin_graph
from app.agents.admin.observability import begin_tool_observation, consume_tool_observations
from app.agents.admin.runtime import build_admin_runtime, build_thread_config
from app.agents.tools import policy_tools
from app.agents.tools.context import AgentContextError


class StubChatModel:
    """按预设顺序返回消息，并记录 Graph 交给模型的输入。"""

    def __init__(self, responses: Sequence[AIMessage]):
        self.responses = list(responses)
        self.bound_tools: tuple = ()
        self.invocations: list[list] = []

    def bind_tools(self, tools: Sequence) -> "StubChatModel":
        self.bound_tools = tuple(tools)
        return self

    def invoke(self, messages: Sequence) -> AIMessage:
        self.invocations.append(list(messages))
        if not self.responses:
            raise AssertionError("模拟模型没有剩余回复。")
        return self.responses.pop(0)


def test_graph_registers_policy_tool_and_returns_plain_answer() -> None:
    """普通回答应直接结束，并保留服务端授权上下文。"""
    user_id = uuid4()
    kb_id = uuid4()
    model = StubChatModel([AIMessage(content="你好，我可以查询企业制度。")])
    graph = build_admin_graph(model)

    result = graph.invoke(
        {
            "messages": [HumanMessage(content="你好")],
            "user_id": user_id,
            "kb_id": kb_id,
        }
    )

    assert [item.name for item in model.bound_tools] == [
        item.name for item in AGENT_TOOLS
    ]
    assert result["user_id"] == str(user_id)
    assert result["kb_id"] == str(kb_id)
    assert result["messages"][-1].content == "你好，我可以查询企业制度。"
    assert isinstance(model.invocations[0][0], SystemMessage)
    assert "绝不能要求用户提供、猜测或生成" in model.invocations[0][0].content


def test_graph_rejects_missing_context_before_calling_model() -> None:
    """授权范围缺失时应在调用模型和工具前终止。"""
    model = StubChatModel([AIMessage(content="不应返回")])
    graph = build_admin_graph(model)
    with pytest.raises(AgentContextError):
        graph.invoke(
            {"messages": [HumanMessage(content="查询制度")], "user_id": str(uuid4())}
        )
    assert model.invocations == []


def test_policy_tool_call_returns_to_model_with_injected_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """制度 Tool Call 应执行真实 ToolNode，再携结果回到模型节点。"""
    user_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    retrieve_mock = Mock(
        return_value=[
            SimpleNamespace(
                chunk_id="a" * 64,
                document_id=document_id,
                document_name="项目制度.pdf",
                page=2,
                content="项目资料需要归档。",
                score=0.88,
            )
        ]
    )
    monkeypatch.setattr(policy_tools, "retrieve_chunks", retrieve_mock)
    model = StubChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_company_policy",
                        "args": {"query": "项目资料如何归档？"},
                        "id": "policy-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="根据项目制度第 2 页，项目资料需要归档。"),
        ]
    )
    result = build_admin_graph(model).invoke(
        {
            "messages": [HumanMessage(content="项目资料如何归档？")],
            "user_id": str(user_id),
            "kb_id": str(kb_id),
        }
    )
    retrieve_mock.assert_called_once_with(
        user_id=user_id,
        kb_id=kb_id,
        question="项目资料如何归档？",
    )
    assert any(
        isinstance(message, ToolMessage)
        and message.tool_call_id == "policy-call-1"
        for message in result["messages"]
    )
    assert result["messages"][-1].content.startswith("根据项目制度")


def test_runtime_restores_state_from_same_thread_after_reopen(tmp_path: Path) -> None:
    """关闭并重建 Runtime 后，同一 thread 仍恢复范围和消息历史。"""
    checkpoint_path = tmp_path / "agent-checkpoints.db"
    user_id = uuid4()
    kb_id = uuid4()
    with build_admin_runtime(
        model=StubChatModel([AIMessage(content="第一轮回答")]),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="第一轮问题")],
                "user_id": str(user_id),
                "kb_id": str(kb_id),
            },
            thread_id="session-001",
        )

    second_model = StubChatModel([AIMessage(content="第二轮回答")])
    with build_admin_runtime(model=second_model, checkpoint_path=checkpoint_path) as runtime:
        result = runtime.invoke(
            {"messages": [HumanMessage(content="第二轮问题")]},
            thread_id="session-001",
        )

    assert result["user_id"] == str(user_id)
    assert [message.content for message in result["messages"]] == [
        "第一轮问题",
        "第一轮回答",
        "第二轮问题",
        "第二轮回答",
    ]
    with sqlite3.connect(checkpoint_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"checkpoints", "writes"} <= table_names


def test_runtime_uses_strict_serializer_and_validates_thread_id(tmp_path: Path) -> None:
    """Checkpoint 不得开启 pickle 或允许任意模块反序列化。"""
    with build_admin_runtime(
        model=StubChatModel([AIMessage(content="未调用")]),
        checkpoint_path=tmp_path / "strict.db",
    ) as runtime:
        serializer = runtime.checkpointer.serde
        assert serializer.pickle_fallback is False
        assert serializer._allowed_json_modules is None
        assert serializer._allowed_msgpack_modules is None
    with pytest.raises(ValueError, match="thread_id 不能为空"):
        build_thread_config("   ")


def test_new_thread_cannot_inherit_another_threads_scope(tmp_path: Path) -> None:
    """新 thread 未注入授权范围时不能读取其他 thread 的状态。"""
    model = StubChatModel([AIMessage(content="已有线程回答"), AIMessage(content="不应调用")])
    with build_admin_runtime(model=model, checkpoint_path=tmp_path / "isolated.db") as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="已有线程")],
                "user_id": str(uuid4()),
                "kb_id": str(uuid4()),
            },
            thread_id="owner-thread",
        )
        with pytest.raises(AgentContextError):
            runtime.invoke(
                {"messages": [HumanMessage(content="新线程")]},
                thread_id="other-thread",
            )
    assert len(model.invocations) == 1


def test_graph_retries_only_transient_tool_failure() -> None:
    """连接故障应由节点重试，并记录每次尝试。"""
    attempts = 0

    @tool
    def transient_lookup(query: str) -> str:
        """模拟前两次连接失败、第三次恢复的查询。"""
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("temporary unavailable")
        return f"查询成功：{query}"

    model = StubChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "transient_lookup",
                    "args": {"query": "制度"},
                    "id": "transient-call-1",
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="临时故障恢复后查询成功。"),
        ]
    )
    graph = build_admin_graph(model, tools=[transient_lookup])
    begin_tool_observation()
    graph.invoke(
        {
            "messages": [HumanMessage(content="查询制度")],
            "user_id": str(uuid4()),
            "kb_id": str(uuid4()),
        }
    )
    observations = consume_tool_observations()
    assert attempts == 3
    assert [item.status for item in observations] == ["FAILED", "FAILED", "COMPLETED"]
