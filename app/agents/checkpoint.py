"""提供 POLICY 与 ARCHIVE Agent 共用的严格 SQLite Checkpoint 存储。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


def build_thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    """构造 LangGraph Checkpointer 要求的线程配置。"""
    normalized_thread_id = thread_id.strip()
    if not normalized_thread_id:
        raise ValueError("thread_id 不能为空。")
    return {"configurable": {"thread_id": normalized_thread_id}}


@dataclass
class AgentCheckpointStore:
    """管理一个禁用 pickle fallback 的 SQLite Checkpoint 连接。"""

    checkpointer: SqliteSaver
    connection: sqlite3.Connection
    checkpoint_path: Path

    def delete_thread(self, thread_id: str) -> None:
        """幂等删除指定线程的全部 Checkpoint 数据。"""
        config = build_thread_config(thread_id)
        self.checkpointer.delete_thread(config["configurable"]["thread_id"])

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        self.connection.close()

    def __enter__(self) -> Self:
        """返回当前存储对象。"""
        return self

    def __exit__(self, *_: Any) -> None:
        """离开上下文时关闭连接。"""
        self.close()


def open_checkpoint_store(checkpoint_path: Path) -> AgentCheckpointStore:
    """打开共享 Checkpoint 文件并安装严格序列化器。"""
    resolved_path = checkpoint_path.resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved_path, check_same_thread=False)
    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    checkpointer = SqliteSaver(connection, serde=serializer)
    checkpointer.setup()
    return AgentCheckpointStore(
        checkpointer=checkpointer,
        connection=connection,
        checkpoint_path=resolved_path,
    )


def delete_checkpoint_thread(*, checkpoint_path: Path, thread_id: str) -> None:
    """不构造模型地打开共享存储并幂等删除一个线程。"""
    with open_checkpoint_store(checkpoint_path) as store:
        store.delete_thread(thread_id)
