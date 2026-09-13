"""管理企业知识库 Agent 的模型、Graph 和 SQLite Checkpoint 生命周期。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from app.agents.admin.graph import build_admin_graph
from app.core.config import settings


def build_thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    """构造 LangGraph Checkpointer 要求的线程配置。

    Args:
        thread_id: Agent 会话对应的稳定线程标识。

    Returns:
        可直接传给 Graph ``invoke`` 和 ``get_state`` 的配置字典。

    Raises:
        ValueError: 线程标识为空或只包含空白字符时抛出。
    """
    normalized_thread_id = thread_id.strip()
    if not normalized_thread_id:
        raise ValueError("thread_id 不能为空。")
    return {"configurable": {"thread_id": normalized_thread_id}}


@dataclass
class AdminAgentRuntime:
    """持有一个可关闭的企业行政 Agent 运行环境。

    Attributes:
        graph: 使用 SQLite Checkpointer 编译后的 LangGraph。
        checkpointer: 保存跨请求消息和状态的 SQLite Checkpointer。
        connection: Checkpointer 独占的 SQLite 连接。
        checkpoint_path: Checkpoint 数据库文件的绝对路径。
    """

    graph: Any
    checkpointer: SqliteSaver
    connection: sqlite3.Connection
    checkpoint_path: Path

    def invoke(
        self,
        state: dict[str, Any],
        *,
        thread_id: str,
    ) -> dict[str, Any]:
        """在指定线程中执行一次 Agent，并持久化最新状态。

        Args:
            state: 本轮新增消息，以及首次调用时的用户和知识库范围。
            thread_id: 用于恢复同一会话状态的稳定线程标识。

        Returns:
            执行结束时包含完整消息历史和授权范围的 Graph State。
        """
        return self.graph.invoke(state, config=build_thread_config(thread_id))

    def get_state(self, *, thread_id: str) -> Any:
        """读取一个线程最近一次持久化的状态快照。

        Args:
            thread_id: 需要读取的 Agent 线程标识。

        Returns:
            LangGraph 的 StateSnapshot；线程不存在时其 values 为空。
        """
        return self.graph.get_state(build_thread_config(thread_id))

    def close(self) -> None:
        """关闭 Checkpoint SQLite 连接并释放文件句柄。"""
        self.connection.close()

    def __enter__(self) -> Self:
        """返回当前运行时，支持使用 ``with`` 管理连接。"""
        return self

    def __exit__(self, *_: object) -> None:
        """离开 ``with`` 代码块时关闭 Checkpoint 连接。"""
        self.close()


def build_admin_runtime(
    *,
    model: Any | None = None,
    checkpoint_path: Path | None = None,
) -> AdminAgentRuntime:
    """创建带独立 SQLite Checkpointer 的企业知识库 Agent 运行时。

    Args:
        model: 可选聊天模型；未提供时延迟创建配置的 DeepSeek 客户端。
        checkpoint_path: 可选 Checkpoint 文件；未提供时使用应用配置。

    Returns:
        可执行、读取状态并显式关闭的 AdminAgentRuntime。

    严格 JsonPlus 序列化器不启用 pickle，也不额外放行任意模块，
    从而限制 Checkpoint 反序列化可创建的对象类型。
    """
    resolved_path = (checkpoint_path or settings.agent_checkpoint_path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved_path, check_same_thread=False)
    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    checkpointer = SqliteSaver(connection, serde=serializer)
    checkpointer.setup()

    try:
        # 只有生产运行时未显式传入模型时才加载模型模块，避免普通导入
        # Agent 类型或测试构图时提前初始化重量级 AI 依赖。
        if model is None:
            from app.services.infrastructure.ai_models import get_chat_model

            model = get_chat_model()
        graph = build_admin_graph(
            model,
            checkpointer=checkpointer,
        )
    except Exception:
        connection.close()
        raise

    return AdminAgentRuntime(
        graph=graph,
        checkpointer=checkpointer,
        connection=connection,
        checkpoint_path=resolved_path,
    )
