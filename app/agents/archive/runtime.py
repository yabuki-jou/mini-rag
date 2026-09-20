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
    """组合可持久化 Graph State 与当前请求专用可信结果。

    Attributes:
        state: Graph 执行后可交给 Checkpoint 保存的状态。
        turn: 当前请求内通过旁路回调产生的可信轮次结果。
    """

    state: dict[str, Any]
    turn: ArchiveTurnResult


@dataclass
class ArchiveAgentRuntime:
    """持有一个项目范围内可关闭的 Archive Graph 运行环境。

    Attributes:
        graph: 已绑定模型、工具和 Checkpoint 的编译后 Graph。
        checkpoint_store: 管理共享 SQLite Checkpoint 连接的存储对象。
        user_id: 服务端验证后的用户 UUID。
        project_id: 服务端验证后的项目 UUID。
        kb_id: 项目绑定的知识库 UUID。
        evidence_registry: 当前请求内保存原始证据候选的注册表。
        _result_holder: 由 Graph 回调写入当前可信轮次结果的临时容器。
    """

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
        """执行一轮并在返回前清理原始证据注册表。

        Args:
            state: 当前轮次的可序列化 Graph State，通常包含当前用户消息和范围字段。
            thread_id: LangGraph Checkpoint 使用的非空线程标识。

        Returns:
            Graph 更新后的状态，以及通过请求内旁路保存的可信轮次结果。

        Raises:
            RuntimeError: Graph 没有通过结果回调产出当前轮结果。
            Exception: Graph 或其依赖失败时原样向应用服务层传播。
        """
        self._result_holder.clear()
        self.evidence_registry.clear()
        try:
            # 线程状态可持久化，但引用候选和完整工具结果必须留在当前调用的内存边界内。
            graph_state = self.graph.invoke(state, config=build_thread_config(thread_id))
            turn = self._result_holder.get("turn")
            if turn is None:
                raise RuntimeError("Archive Graph 未产生当前轮次结果。")
            return ArchiveRuntimeResult(state=graph_state, turn=turn)
        finally:
            # 无论 Graph 成功、拒答还是异常，都不能把原文摘录留在可复用 Runtime 中。
            self.evidence_registry.clear()

    def get_state(self, *, thread_id: str) -> Any:
        """读取指定线程最近一次状态。

        Args:
            thread_id: 需要读取的 LangGraph Checkpoint 线程标识。

        Returns:
            LangGraph 返回的线程状态快照。
        """
        return self.graph.get_state(build_thread_config(thread_id))

    def delete_thread(self, thread_id: str) -> None:
        """幂等删除指定线程且不构造新模型。

        Args:
            thread_id: 需要从共享 SQLite Checkpoint 中删除的线程标识。
        """
        self.checkpoint_store.delete_thread(thread_id)

    def close(self) -> None:
        """关闭共享 Checkpoint 连接。"""
        self.checkpoint_store.close()

    def __enter__(self) -> Self:
        """返回当前 Runtime。"""
        return self

    def __exit__(self, *_: object) -> None:
        """离开上下文时关闭连接。

        Args:
            *_: 上下文管理器协议提供的异常类型、异常值和回溯对象。
        """
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
    """创建绑定可信项目范围并复用共享 Checkpoint 文件的 Archive Runtime。

    Args:
        user_id: 已认证用户的 UUID，仅由服务端传入工具运行时。
        project_id: 已授权项目的 UUID。
        kb_id: 项目绑定知识库的 UUID。
        session_factory: 为每次工具调用创建业务数据库 Session 的工厂。
        model: 可选主模型；省略时从应用基础设施配置创建。
        judge_model: 可选证据判定模型；省略时复用主模型。
        checkpoint_path: 可选共享 SQLite 路径；省略时使用应用配置路径。

    Returns:
        已绑定服务端授权范围、模型和 Checkpoint 的运行环境。
    """
    store = open_checkpoint_store(checkpoint_path or settings.agent_checkpoint_path)
    try:
        if model is None:
            from app.services.infrastructure.ai_models import get_chat_model

            model = get_chat_model(max_retries=0)
        resolved_judge_model = model if judge_model is None else judge_model
        registry = ArchiveEvidenceRegistry()
        result_holder: dict[str, ArchiveTurnResult] = {}

        def save_result(result: ArchiveTurnResult) -> None:
            """只在当前 Runtime 内保存请求结果，不写入 Graph State。

            Args:
                result: 当前轮次完成后供应用服务层读取的可信结果。

            引用卡片依赖请求内证据候选，不能与可恢复的消息状态混存。
            """
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
        # Graph 构造失败时也要关闭已经打开的 SQLite 连接，避免文件句柄泄漏。
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
