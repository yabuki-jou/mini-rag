"""验证轻量历史 Graph 与正式执行 Graph 的状态恢复契约。"""

from pathlib import Path
from typing import Any, get_args, get_type_hints
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import add_messages

from app.agents.archive.graph import build_archive_graph
from app.agents.archive.history_reader import build_archive_history_graph
from app.agents.archive.state import ArchiveAgentState
from app.agents.checkpoint import build_thread_config, open_checkpoint_store
from app.agents.tools.archive_tools import ArchiveEvidenceRegistry, ArchiveToolRuntime


class _NoToolModel:
    """提供不发起工具调用的确定性模型替身。"""

    def bind_tools(self, _tools: list[Any]) -> "_NoToolModel":
        """返回自身，满足正式 Graph 的构造契约。"""
        return self

    def invoke(self, _messages: list[Any]) -> AIMessage:
        """返回固定自由文本，由正式 Graph 按当前拒答规则投影。"""
        return AIMessage(content="模型替身文本")


def _build_execution_graph(
    checkpointer: Any | None = None,
    *,
    state: dict[str, Any] | None = None,
) -> Any:
    """使用正式构造器创建执行 Graph，但不触发外部服务。"""
    state = state or _initial_state()
    model = _NoToolModel()
    tool_runtime = ArchiveToolRuntime(
        user_id=UUID(state["user_id"]),
        project_id=UUID(state["project_id"]),
        kb_id=UUID(state["kb_id"]),
        session_factory=lambda: None,
        evidence_registry=ArchiveEvidenceRegistry(),
    )
    return build_archive_graph(
        model,
        judge_model=model,
        tool_runtime=tool_runtime,
        result_sink=lambda _result: None,
        checkpointer=checkpointer,
    )


def _initial_state() -> dict[str, Any]:
    """构造通过正式执行节点范围校验的最小输入。"""
    return {
        "user_id": str(uuid4()),
        "project_id": str(uuid4()),
        "kb_id": str(uuid4()),
        "messages": [HumanMessage(content="历史状态问题")],
    }


def test_history_graph_structure_matches_execution_graph() -> None:
    """状态字段、消息 reducer、节点名称与拓扑必须和正式 Graph 一致。"""
    execution_graph = _build_execution_graph()
    history_graph = build_archive_history_graph()
    execution_builder = execution_graph.builder
    history_builder = history_graph.builder

    assert execution_builder.state_schema is ArchiveAgentState
    assert history_builder.state_schema is ArchiveAgentState
    assert set(execution_builder.channels) == set(history_builder.channels)
    assert {
        name: channel.UpdateType for name, channel in execution_builder.channels.items()
    } == {name: channel.UpdateType for name, channel in history_builder.channels.items()}

    state_hints = get_type_hints(ArchiveAgentState, include_extras=True)
    messages_reducer = get_args(state_hints["messages"])[-1]
    assert messages_reducer is add_messages
    assert execution_builder.channels["messages"].operator is messages_reducer
    assert history_builder.channels["messages"].operator is messages_reducer

    expected_nodes = {"__start__", "archive_turn", "__end__"}
    assert set(execution_graph.get_graph().nodes) == expected_nodes
    assert set(history_graph.get_graph().nodes) == expected_nodes
    expected_edges = {("__start__", "archive_turn"), ("archive_turn", "__end__")}
    assert {
        (edge.source, edge.target) for edge in execution_graph.get_graph().edges
    } == expected_edges
    assert {
        (edge.source, edge.target) for edge in history_graph.get_graph().edges
    } == expected_edges


def test_history_graph_placeholder_fails_if_invoked() -> None:
    """历史 Graph 的占位节点不能意外执行任何 Agent 业务。"""
    graph = build_archive_history_graph()

    with pytest.raises(RuntimeError, match="只允许读取历史状态"):
        graph.invoke(_initial_state())


def test_history_reader_matches_completed_execution_state(tmp_path: Path) -> None:
    """正常完成的历史由两种 Graph 恢复为相同状态。"""
    store = open_checkpoint_store(tmp_path / "completed.db")
    try:
        state = _initial_state()
        execution_graph = _build_execution_graph(store.checkpointer, state=state)
        history_graph = build_archive_history_graph(store.checkpointer)
        config = build_thread_config("completed-thread")
        execution_graph.invoke(state, config=config)

        expected = execution_graph.get_state(config)
        actual = history_graph.get_state(config)

        assert actual.values == expected.values
        assert [message.content for message in actual.values["messages"]] == [
            "历史状态问题",
            "正式档案中没有足够依据。",
        ]
    finally:
        store.close()


def test_history_reader_matches_empty_execution_state(tmp_path: Path) -> None:
    """不存在 Checkpoint 的线程在两种 Graph 中都恢复为空历史。"""
    store = open_checkpoint_store(tmp_path / "empty.db")
    try:
        execution_graph = _build_execution_graph(store.checkpointer)
        history_graph = build_archive_history_graph(store.checkpointer)
        config = build_thread_config("empty-thread")

        expected = execution_graph.get_state(config)
        actual = history_graph.get_state(config)

        assert actual.values == expected.values
        assert actual.values.get("messages", []) == []
    finally:
        store.close()


def test_history_reader_matches_pending_write_recovery(tmp_path: Path) -> None:
    """待执行节点的 pending messages 写入仍由 LangGraph reducer 恢复。"""
    store = open_checkpoint_store(tmp_path / "pending.db")
    try:
        state = _initial_state()
        execution_graph = _build_execution_graph(state=state)
        paused_graph = execution_graph.builder.compile(
            checkpointer=store.checkpointer,
            interrupt_before=["archive_turn"],
        )
        resumed_execution_graph = execution_graph.builder.compile(
            checkpointer=store.checkpointer,
        )
        history_graph = build_archive_history_graph(store.checkpointer)
        config = build_thread_config("pending-thread")
        paused_graph.invoke(state, config=config)
        paused_state = paused_graph.get_state(config)
        assert paused_state.tasks
        task = paused_state.tasks[0]

        store.checkpointer.put_writes(
            paused_state.config,
            [
                (
                    "messages",
                    [AIMessage(content="pending write 回答", id="pending-write-assistant")],
                )
            ],
            task_id=task.id,
        )

        expected = resumed_execution_graph.get_state(config)
        actual = history_graph.get_state(config)

        assert actual.values == expected.values
        assert actual.next == expected.next
        assert [(task.id, task.name) for task in actual.tasks] == [
            (task.id, task.name) for task in expected.tasks
        ]
        assert [message.content for message in actual.values["messages"]] == [
            "历史状态问题",
            "pending write 回答",
        ]
    finally:
        store.close()
