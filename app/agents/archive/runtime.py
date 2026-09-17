"""管理项目档案助手 Graph 与共享 SQLite Checkpoint 生命周期。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from uuid import UUID

from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.archive.graph import ArchiveTurnResult, build_archive_graph
from app.agents.checkpoint import AgentCheckpointStore, build_thread_config, open_checkpoint_store
from app.agents.tools.archive_tools import ArchiveEvidenceRegistry, ArchiveToolRuntime
from app.core.config import settings


@dataclass(frozen=True)
class ArchiveRuntimeResult:
    """组合可持久化 Graph State 与当前请求专用可信结果。"""

    state: dict[str, Any]
    turn: ArchiveTurnResult


@dataclass
class ArchiveAgentRuntime:
    """持有一个项目范围内可关闭的 Archive Graph 运行环境。"""

    graph: Any
    checkpoint_store: AgentCheckpointStore
    user_id: UUID
    project_id: UUID
    kb_id: UUID
    evidence_registry: ArchiveEvidenceRegistry
    _result_holder: dict[str, ArchiveTurnResult] = field(default_factory=dict)

    @property
    def checkpointer(self) -> SqliteSaver:
        """返回共享 SQLite Checkpointer。"""
        return self.checkpoint_store.checkpointer

    @property
    def connection(self) -> Any:
        """返回 Checkpoint SQLite 连接。"""
        return self.checkpoint_store.connection

    @property
    def checkpoint_path(self) -> Path:
        """返回共享 Checkpoint 文件绝对路径。"""
        return self.checkpoint_store.checkpoint_path

    def invoke(self, state: dict[str, Any], *, thread_id: str) -> ArchiveRuntimeResult:
        """执行一轮并在返回前清理原始证据注册表。"""
        self._result_holder.clear()
        self.evidence_registry.clear()
        try:
            graph_state = self.graph.invoke(state, config=build_thread_config(thread_id))
            turn = self._result_holder.get("turn")
            if turn is None:
                raise RuntimeError("Archive Graph 未产生当前轮次结果。")
            return ArchiveRuntimeResult(state=graph_state, turn=turn)
        finally:
            self.evidence_registry.clear()

    def get_state(self, *, thread_id: str) -> Any:
        """读取指定线程最近一次状态。"""
        return self.graph.get_state(build_thread_config(thread_id))

    def delete_thread(self, thread_id: str) -> None:
        """幂等删除指定线程且不构造新模型。"""
        self.checkpoint_store.delete_thread(thread_id)

    def close(self) -> None:
        """关闭共享 Checkpoint 连接。"""
        self.checkpoint_store.close()

    def __enter__(self) -> Self:
        """返回当前 Runtime。"""
        return self

    def __exit__(self, *_: object) -> None:
        """离开上下文时关闭连接。"""
        self.close()


def build_archive_runtime(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    session_factory: Callable[[], Any],
    model: Any | None = None,
    judge_model: Any | None = None,
    checkpoint_path: Path | None = None,
) -> ArchiveAgentRuntime:
    """创建绑定可信项目范围并复用共享 Checkpoint 文件的 Archive Runtime。"""
    store = open_checkpoint_store(checkpoint_path or settings.agent_checkpoint_path)
    try:
        if model is None:
            from app.services.infrastructure.ai_models import get_chat_model

            model = get_chat_model(max_retries=0)
        resolved_judge_model = model if judge_model is None else judge_model
        registry = ArchiveEvidenceRegistry()
        result_holder: dict[str, ArchiveTurnResult] = {}

        def save_result(result: ArchiveTurnResult) -> None:
            """只在当前 Runtime 内保存请求结果，不写入 Graph State。"""
            result_holder["turn"] = result

        tool_runtime = ArchiveToolRuntime(
            user_id=user_id,
            project_id=project_id,
            kb_id=kb_id,
            session_factory=session_factory,
            evidence_registry=registry,
        )
        graph = build_archive_graph(
            model,
            judge_model=resolved_judge_model,
            tool_runtime=tool_runtime,
            result_sink=save_result,
            checkpointer=store.checkpointer,
        )
    except Exception:
        store.close()
        raise
    return ArchiveAgentRuntime(
        graph=graph,
        checkpoint_store=store,
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        evidence_registry=registry,
        _result_holder=result_holder,
    )
