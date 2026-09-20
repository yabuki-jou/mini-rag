"""提供 POLICY 与 ARCHIVE Agent 共用的严格 SQLite Checkpoint 存储。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


def build_thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    """构造 LangGraph Checkpointer 要求的线程配置。

    Args:
        thread_id: 业务会话对应的线程标识；首尾空白会被移除。

    Returns:
        供 LangGraph ``configurable`` 配置使用的线程字典。

    Raises:
        ValueError: ``thread_id`` 去除首尾空白后为空。
    """
    normalized_thread_id = thread_id.strip()
    if not normalized_thread_id:
        raise ValueError("thread_id 不能为空。")
    return {"configurable": {"thread_id": normalized_thread_id}}


@dataclass
class AgentCheckpointStore:
    """管理一个禁用 pickle fallback 的 SQLite Checkpoint 连接。

    Attributes:
        checkpointer: LangGraph 使用的 SQLite Checkpoint 适配器。
        connection: 由当前存储对象持有、需要显式关闭的 SQLite 连接。
        checkpoint_path: 实际打开的 Checkpoint SQLite 文件绝对路径。
    """

    checkpointer: SqliteSaver
    connection: sqlite3.Connection
    checkpoint_path: Path

    def delete_thread(self, thread_id: str) -> None:
        """幂等删除指定线程的全部 Checkpoint 数据。

        Args:
            thread_id: 需要删除的 LangGraph 线程标识。
        """
        config = build_thread_config(thread_id)
        self.checkpointer.delete_thread(config["configurable"]["thread_id"])

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        self.connection.close()

    def __enter__(self) -> Self:
        """返回当前存储对象。"""
        return self

    def __exit__(self, *_: Any) -> None:
        """离开上下文时关闭连接。

        Args:
            *_: 上下文管理器协议提供的异常类型、异常值和回溯对象。
        """
        self.close()


def open_checkpoint_store(checkpoint_path: Path) -> AgentCheckpointStore:
    """打开共享 Checkpoint 文件并安装严格序列化器。

    Args:
        checkpoint_path: 共享 SQLite 文件路径；父目录不存在时自动创建。

    Returns:
        已完成表结构初始化、可用于 Graph 调用的 Checkpoint 存储对象。
    """
    resolved_path = checkpoint_path.resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    # Runtime 可能在异步请求与同步工具边界间使用连接，关闭 SQLite 的线程归属检查。
    connection = sqlite3.connect(resolved_path, check_same_thread=False)
    # 禁止 pickle fallback，确保 Graph State 只能保存受支持且可审计的序列化值。
    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    checkpointer = SqliteSaver(connection, serde=serializer)
    # setup 只负责幂等创建 Checkpoint 表，不替代业务数据库迁移。
    checkpointer.setup()
    return AgentCheckpointStore(
        checkpointer=checkpointer,
        connection=connection,
        checkpoint_path=resolved_path,
    )


def delete_checkpoint_thread(*, checkpoint_path: Path, thread_id: str) -> None:
    """不构造模型地打开共享存储并幂等删除一个线程。

    Args:
        checkpoint_path: 共享 Checkpoint SQLite 文件路径。
        thread_id: 需要删除的 LangGraph 线程标识。
    """
    with open_checkpoint_store(checkpoint_path) as store:
        store.delete_thread(thread_id)
