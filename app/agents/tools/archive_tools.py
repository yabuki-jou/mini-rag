"""实现智慧档案 Agent 的两个只读工具和请求内安全边界。"""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Iterator
from uuid import UUID

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ConfigDict, Field

from app.core.evaluation import eval_wrap
from app.core.errors import AppError
from app.models import ArchiveDocumentType, EvidenceLocationType, ProjectStage
from app.services.archive import catalog as catalog_service
from app.services.archive import retrieval as retrieval_service


class ArchiveAgentEvidenceCandidate(BaseModel):
    """保存只含请求内文档引用和原文定位的 Agent 证据候选。

    Attributes:
        document_ref: 当前请求内用于模型和引用判定的文档编号。
        filename: 证据所在原文件名。
        document_title: 已确认的文档标题；缺失时为 ``None``。
        location_type: 摘录所在的定位类型。
        location_start: 定位范围起点。
        location_end: 定位范围终点。
        excerpt: 支持回答的原文摘录。
    """

    model_config = ConfigDict(extra="forbid")

    document_ref: str = Field(pattern=r"^D[1-8]$")
    filename: str = Field(min_length=1)
    document_title: str | None = Field(default=None, min_length=1)
    location_type: EvidenceLocationType
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1)


def _retrieve_archive_agent_evidence(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    query: str,
    gate_question: str = "",
    session: Any,
) -> list[dict[str, object]]:
    """执行正式检索并在评测边界前移除持久化标识和分数。

    Args:
        user_id: 服务端验证后的用户 UUID。
        project_id: 服务端验证后的项目 UUID。
        kb_id: 项目绑定知识库的 UUID。
        query: 已规范化的档案检索问题。
        gate_question: 当前轮用户原始问题，仅用于补充召回门控。
        session: 当前工具调用使用的业务数据库会话。

    参数中的三个 UUID 均来自已验证的服务端上下文，不能由模型输入覆盖。
    """
    response = retrieval_service.retrieve_archive_answer_candidates(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        query=query,
        gate_question=gate_question,
        session=session,
        observe_result=False,
    )
    titles = catalog_service.list_agent_confirmed_document_titles(
        project_id=project_id,
        document_ids={item.document_id for item in response.items},
        session=session,
    )
    document_refs: dict[UUID, str] = {}
    return [
        {
            "document_ref": document_refs.setdefault(
                item.document_id,
                f"D{len(document_refs) + 1}",
            ),
            "filename": item.filename,
            "document_title": titles[item.document_id],
            "location_type": item.location_type.value,
            "location_start": item.location_start,
            "location_end": item.location_end,
            "excerpt": item.excerpt,
        }
        for item in response.items
        if item.document_id in titles
    ]


def _current_gate_question(state: dict[str, Any]) -> str:
    """仅从 State 末尾的文本用户消息取得当前轮门控意图。

    Args:
        state: LangGraph 注入的当前轮状态。

    Returns:
        当前轮用户问题；支持原始 HumanMessage 或 type 标记为 ``human`` 且 content
        为字符串的 Pydantic 序列化字典。不符合条件时返回空串，不回退到旧消息。
    """
    messages = state.get("messages")
    if not isinstance(messages, (list, tuple)) or not messages:
        return ""
    current = messages[-1]
    if isinstance(current, BaseMessage):
        if current.type != "human" or not isinstance(current.content, str):
            return ""
        return current.content
    if isinstance(current, dict):
        content = current.get("content")
        if current.get("type") == "human" and isinstance(content, str):
            return content
    return ""


@dataclass
class ArchiveEvidenceRegistry:
    """按 Tool Call ID 保存当前请求内的原始证据候选。

    Attributes:
        _items: 从工具调用标识到原始证据候选元组的内存映射。
    """

    _items: dict[str, tuple[ArchiveAgentEvidenceCandidate, ...]] = field(default_factory=dict)

    def register(
        self,
        tool_call_id: str,
        candidates: list[ArchiveAgentEvidenceCandidate],
    ) -> None:
        """登记本次成功证据调用的原始候选。

        Args:
            tool_call_id: 当前工具调用的关联标识。
            candidates: 需要保留给服务端判定的原始候选列表。

        将列表转换为元组，避免调用方后续修改列表而改变服务端判定依据。
        """
        self._items[tool_call_id] = tuple(candidates)

    def get(self, tool_call_id: str) -> tuple[ArchiveAgentEvidenceCandidate, ...] | None:
        """读取指定调用的候选，不复制到安全消息。

        Args:
            tool_call_id: 需要读取的工具调用标识。

        Returns:
            已登记的候选元组；没有对应调用时返回 ``None``。
        """
        return self._items.get(tool_call_id)

    def discard(self, tool_call_id: str) -> None:
        """清理一个失败调用的候选。

        Args:
            tool_call_id: 需要从当前请求注册表移除的工具调用标识。
        """
        self._items.pop(tool_call_id, None)

    def clear(self) -> None:
        """清理请求结束后的所有原始候选。"""
        self._items.clear()

    def __getitem__(self, tool_call_id: str) -> tuple[ArchiveAgentEvidenceCandidate, ...]:
        """提供测试和请求内判定使用的只读索引入口。

        Args:
            tool_call_id: 需要索引的工具调用标识。

        Returns:
            对应工具调用的候选元组。
        """
        return self._items[tool_call_id]

    def __contains__(self, tool_call_id: object) -> bool:
        """判断调用是否仍在当前请求注册表中。

        Args:
            tool_call_id: 需要检查的工具调用标识。

        Returns:
            标识仍存在于注册表时返回 ``True``。
        """
        return tool_call_id in self._items

    def __len__(self) -> int:
        """返回当前请求中仍保留的调用数。"""
        return len(self._items)


@contextmanager
def archive_evidence_registry_scope() -> Iterator[ArchiveEvidenceRegistry]:
    """创建请求级注册表，并保证成功或异常退出都清理原始候选。"""
    registry = ArchiveEvidenceRegistry()
    try:
        yield registry
    finally:
        # 原始候选可能包含摘录和定位信息，只在当前请求内供服务端判定，不能跨请求残留。
        registry.clear()


@dataclass(frozen=True)
class ArchiveToolRuntime:
    """保存工具运行时注入的服务端范围和 Session 工厂。

    Attributes:
        user_id: 已认证用户的 UUID。
        project_id: 已授权项目的 UUID。
        kb_id: 项目绑定知识库的 UUID。
        session_factory: 为工具调用创建业务数据库会话的工厂。
        evidence_registry: 当前请求的原始证据注册表或兼容字典。
    """

    user_id: UUID
    project_id: UUID
    kb_id: UUID
    session_factory: Callable[[], Any]
    evidence_registry: ArchiveEvidenceRegistry | dict[str, tuple[ArchiveAgentEvidenceCandidate, ...]] | None = None

    def registry(self) -> ArchiveEvidenceRegistry | dict[str, tuple[ArchiveAgentEvidenceCandidate, ...]]:
        """取得当前请求注册表；未提供时创建临时注册表。"""
        if self.evidence_registry is None:
            # 没有请求级容器时只返回一次性注册表，避免隐式创建可跨请求共享的全局状态。
            return ArchiveEvidenceRegistry()
        return self.evidence_registry


class _ArchiveCatalogToolInput(BaseModel):
    """目录工具的模型可控参数和运行时注入字段。

    Attributes:
        document_type: 可选的档案文档类型筛选。
        project_stage: 可选的项目阶段筛选。
        document_date_from: 可选的文档日期起点字符串。
        document_date_to: 可选的文档日期终点字符串。
        document_date_is_null: 是否只筛选没有文档日期的记录。
        authoring_organization: 可选的编制组织筛选。
        page: 分页页码。
        page_size: 每页返回的目录数量。
        state: 由 LangGraph 注入的当前 Graph State。
        tool_call_id: 由 LangGraph 注入的工具调用标识。
    """

    model_config = ConfigDict(extra="forbid")

    document_type: ArchiveDocumentType | None = None
    project_stage: ProjectStage | None = None
    document_date_from: str | None = None
    document_date_to: str | None = None
    document_date_is_null: bool = False
    authoring_organization: str | None = Field(default=None, max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=20)
    state: Annotated[dict[str, Any], InjectedState]
    tool_call_id: Annotated[str, InjectedToolCallId]


class _ArchiveEvidenceToolInput(BaseModel):
    """证据工具的模型可控参数和运行时注入字段。

    Attributes:
        query: 模型生成的档案检索问题。
        state: 由 LangGraph 注入的当前 Graph State。
        tool_call_id: 由 LangGraph 注入的工具调用标识。
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    state: Annotated[dict[str, Any], InjectedState]
    tool_call_id: Annotated[str, InjectedToolCallId]


def _normalize_query(query: str) -> str:
    """按消息契约统一换行并去除首尾空白。

    Args:
        query: 模型或服务层传入的原始检索问题。

    Returns:
        换行统一、首尾无空白且长度合法的检索问题。

    Raises:
        AppError: 问题为空或超过 2000 个码点。
    """
    normalized = query.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > 2000:
        raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "检索问题长度必须为 1 到 2000 个码点。")
    return normalized


def _safe_evidence_result(
    candidates: list[ArchiveAgentEvidenceCandidate],
) -> dict[str, object]:
    """将原始候选投影为不含标识和分数的 S 编号结果。

    Args:
        candidates: 当前请求内的原始证据候选。

    Returns:
        供模型使用的安全证据结果字典。
    """
    # 模型只接触此投影；document_id、Chunk 标识和检索分数留在可信服务端边界内。
    return {
        "results": [
            {
                "citation": f"S{index}",
                "filename": item.filename,
                "document_title": item.document_title,
                "location_type": item.location_type.value,
                "location_start": item.location_start,
                "location_end": item.location_end,
                "excerpt": item.excerpt,
            }
            for index, item in enumerate(candidates, start=1)
        ],
        "found": bool(candidates),
    }


def serialize_tool_message(
    tool_call_id: str,
    payload: dict[str, object],
    *,
    tool_name: str | None = None,
) -> ToolMessage:
    """把已安全投影结果编码为可序列化 ToolMessage。

    Args:
        tool_call_id: LangGraph 当前工具调用的关联标识。
        payload: 已移除内部标识和敏感字段的 JSON 兼容结果。
        tool_name: 可选工具名称，用于保留消息来源信息。

    Returns:
        内容为 JSON 字符串的 ``ToolMessage``，可由 Checkpoint 严格序列化。
    """
    # ToolMessage 内容统一使用 JSON 文本，避免把任意 Python 对象交给 Checkpoint 序列化器。
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        tool_call_id=tool_call_id,
        name=tool_name,
    )


def build_archive_tools(*, runtime: ArchiveToolRuntime) -> tuple[Any, Any]:
    """根据请求运行时构造两个不携带客户端范围参数的工具。

    Args:
        runtime: 服务端注入的范围、数据库 Session 工厂和请求级证据注册表。

    Returns:
        目录查询工具与正式证据检索工具，二者的范围参数均不暴露给模型。
    """
    registry = runtime.registry()

    @tool(args_schema=_ArchiveCatalogToolInput)
    def list_formal_archives(
        document_type: Annotated[ArchiveDocumentType | None, Field(default=None)],
        project_stage: Annotated[ProjectStage | None, Field(default=None)],
        document_date_from: Annotated[str | None, Field(default=None)],
        document_date_to: Annotated[str | None, Field(default=None)],
        document_date_is_null: Annotated[bool, Field(default=False)],
        authoring_organization: Annotated[str | None, Field(default=None, max_length=200)],
        page: Annotated[int, Field(default=1, ge=1)],
        page_size: Annotated[int, Field(default=20, ge=1, le=20)],
        state: Annotated[dict[str, Any], InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, object]:
        """查询当前项目正式目录并返回安全投影。

        Args:
            document_type: 可选的文档类型筛选。
            project_stage: 可选的项目阶段筛选。
            document_date_from: 可选的文档日期起点。
            document_date_to: 可选的文档日期终点。
            document_date_is_null: 是否筛选文档日期为空的记录。
            authoring_organization: 可选的编制组织筛选。
            page: 目录分页页码。
            page_size: 目录分页大小。
            state: LangGraph 注入的当前 Graph State。
            tool_call_id: LangGraph 注入的工具调用标识。

        Returns:
            当前授权项目正式目录的安全分页结果。
        """
        del state, tool_call_id
        from datetime import date

        try:
            start = date.fromisoformat(document_date_from) if document_date_from else None
            end = date.fromisoformat(document_date_to) if document_date_to else None
        except ValueError as exc:
            raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "日期筛选格式无效。") from exc
        catalog_call = eval_wrap(
            catalog_service.list_agent_formal_archives,
            purpose="input",
            name="archive_agent_catalog_result",
            description="当前项目正式档案目录服务返回的安全分页结果。",
        )
        # 项目范围来自闭包中的可信 Runtime，模型只能控制目录筛选和分页字段。
        with runtime.session_factory() as session:
            page_result = catalog_call(
                project_id=runtime.project_id,
                page=page,
                page_size=page_size,
                document_type=document_type,
                project_stage=project_stage,
                document_date_from=start,
                document_date_to=end,
                document_date_is_null=document_date_is_null,
                authoring_organization=authoring_organization,
                session=session,
            )
        payload = page_result.as_dict()
        eval_wrap(
            payload,
            purpose="state",
            name="archive_agent_safe_tool_result",
            description="目录或证据工具返回给模型的不含持久化标识与分数的安全投影。",
        )
        return payload

    @tool(args_schema=_ArchiveEvidenceToolInput)
    def search_confirmed_archive_evidence(
        query: Annotated[str, Field(min_length=1, max_length=2000)],
        state: Annotated[dict[str, Any], InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, object]:
        """检索当前用户项目正式档案并返回安全 S 编号结果。

        Args:
            query: 模型生成的档案检索问题。
            state: LangGraph 注入的当前 Graph State。
            tool_call_id: LangGraph 注入的工具调用标识。

        Returns:
            当前授权范围内不含持久化标识和分数的安全证据结果。
        """
        normalized_query = _normalize_query(query)
        gate_question = _current_gate_question(state)
        try:
            def retrieve_for_current_turn(
                *,
                user_id: UUID,
                project_id: UUID,
                kb_id: UUID,
                query: str,
                session: Any,
            ) -> list[dict[str, object]]:
                """在注入边界之外绑定当前轮原话，避免新增原话采集字段。

                Args:
                    user_id: 已验证的用户范围。
                    project_id: 已验证的项目范围。
                    kb_id: 项目绑定的知识库范围。
                    query: 模型生成的检索词。
                    session: 当前业务数据库会话。
                """
                return _retrieve_archive_agent_evidence(
                    user_id=user_id,
                    project_id=project_id,
                    kb_id=kb_id,
                    query=query,
                    gate_question=gate_question,
                    session=session,
                )

            retrieval_call = eval_wrap(
                retrieve_for_current_turn,
                purpose="input",
                name="archive_agent_evidence_retrieval",
                description="当前授权范围返回给档案助手的请求内 Top-8 安全证据候选。",
            )
            # 检索使用服务端固定的 user/project/kb 范围，防止模型跨项目读取向量或文档。
            with runtime.session_factory() as session:
                candidate_data = retrieval_call(
                    user_id=runtime.user_id,
                    project_id=runtime.project_id,
                    kb_id=runtime.kb_id,
                    query=normalized_query,
                    session=session,
                )
            if not isinstance(candidate_data, list) or len(candidate_data) > 8:
                raise ValueError("archive agent evidence input is invalid")
            candidates = [
                ArchiveAgentEvidenceCandidate.model_validate(item)
                for item in candidate_data
            ]
            # 原始候选仅注册给服务端判定；返回给模型的 payload 仍是安全投影。
            if isinstance(registry, ArchiveEvidenceRegistry):
                registry.register(tool_call_id, candidates)
            else:
                registry[tool_call_id] = tuple(candidates)
            payload = _safe_evidence_result(candidates)
            eval_wrap(
                payload,
                purpose="state",
                name="archive_agent_safe_tool_result",
                description="目录或证据工具返回给模型的不含持久化标识与分数的安全投影。",
            )
            return payload
        except Exception as exc:
            eval_wrap(
                {
                    "tool_name": "search_confirmed_archive_evidence",
                    "exception_type": type(exc).__name__,
                },
                purpose="state",
                name="archive_agent_tool_failure",
                description="证据工具失败时不含异常正文、参数值和业务数据的类型观测。",
            )
            # 失败调用不能留下可被后续轮次误用的候选，清理后再保留原异常类型。
            if isinstance(registry, ArchiveEvidenceRegistry):
                registry.discard(tool_call_id)
            else:
                registry.pop(tool_call_id, None)
            raise

    return list_formal_archives, search_confirmed_archive_evidence
