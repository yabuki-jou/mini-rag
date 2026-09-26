"""构建仅用于恢复项目档案助手历史状态的轻量 Graph。"""

from typing import Any, NoReturn

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agents.archive.state import ArchiveAgentState


def _reject_history_graph_execution(_state: ArchiveAgentState) -> NoReturn:
    """阻止历史读取 Graph 被误用于执行 Agent 轮次。

    Args:
        _state: LangGraph 提供的输入状态；只为满足节点签名，不读取或修改。
    """
    raise RuntimeError("历史读取 Graph 只允许读取历史状态。")


def build_archive_history_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """构建与正式执行 Graph 同状态、同节点和同拓扑的只读用途 Graph。

    Args:
        checkpointer: 由现有严格序列化配置创建的 Checkpointer。

    Returns:
        可调用公开 ``get_state`` 读取 Checkpoint 的编译 Graph。

    状态恢复仍由 LangGraph 根据共享 State 定义和拓扑完成；占位节点只保留结构，
    如果被调度会立即失败。
    """
    builder = StateGraph(ArchiveAgentState)
    builder.add_node("archive_turn", _reject_history_graph_execution)
    builder.add_edge(START, "archive_turn")
    builder.add_edge("archive_turn", END)
    return builder.compile(checkpointer=checkpointer)
