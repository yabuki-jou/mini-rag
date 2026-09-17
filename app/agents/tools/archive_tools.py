"""实现智慧档案 Agent 的两个只读工具和请求内安全边界。"""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Iterator
from uuid import UUID

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, ConfigDict, Field

from app.core.evaluation import eval_wrap
from app.core.errors import AppError
from app.models import ArchiveDocumentType, EvidenceLocationType, ProjectStage
from app.services.archive import catalog as catalog_service
from app.services.archive import retrieval as retrieval_service


class ArchiveAgentEvidenceCandidate(BaseModel):
    """保存只含请求内文档引用和原文定位的 Agent 证据候选。"""

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
    session: Any,
) -> list[dict[str, object]]:
    """执行正式检索并在评测边界前移除持久化标识和分数。"""
    response = retrieval_service.retrieve_archive_answer_candidates(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        query=query,
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


@dataclass
class ArchiveEvidenceRegistry:
    """按 Tool Call ID 保存当前请求内的原始证据候选。"""

    _items: dict[str, tuple[ArchiveAgentEvidenceCandidate, ...]] = field(default_factory=dict)

    def register(
        self,
        tool_call_id: str,
        candidates: list[ArchiveAgentEvidenceCandidate],
    ) -> None:
        """登记本次成功证据调用的原始候选。"""
        self._items[tool_call_id] = tuple(candidates)

    def get(self, tool_call_id: str) -> tuple[ArchiveAgentEvidenceCandidate, ...] | None:
        """读取指定调用的候选，不复制到安全消息。"""
        return self._items.get(tool_call_id)

    def discard(self, tool_call_id: str) -> None:
        """清理一个失败调用的候选。"""
        self._items.pop(tool_call_id, None)

    def clear(self) -> None:
        """清理请求结束后的所有原始候选。"""
        self._items.clear()

    def __getitem__(self, tool_call_id: str) -> tuple[ArchiveAgentEvidenceCandidate, ...]:
        """提供测试和请求内判定使用的只读索引入口。"""
        return self._items[tool_call_id]

    def __contains__(self, tool_call_id: object) -> bool:
        """判断调用是否仍在当前请求注册表中。"""
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
        registry.clear()


@dataclass(frozen=True)
class ArchiveToolRuntime:
    """保存工具运行时注入的服务端范围和 Session 工厂。"""

    user_id: UUID
    project_id: UUID
    kb_id: UUID
    session_factory: Callable[[], Any]
    evidence_registry: ArchiveEvidenceRegistry | dict[str, tuple[ArchiveAgentEvidenceCandidate, ...]] | None = None

    def registry(self) -> ArchiveEvidenceRegistry | dict[str, tuple[ArchiveAgentEvidenceCandidate, ...]]:
        """取得当前请求注册表；未提供时创建临时注册表。"""
        if self.evidence_registry is None:
            return ArchiveEvidenceRegistry()
        return self.evidence_registry


class _ArchiveCatalogToolInput(BaseModel):
    """目录工具的模型可控参数和运行时注入字段。"""

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
    """证据工具的模型可控参数和运行时注入字段。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    state: Annotated[dict[str, Any], InjectedState]
    tool_call_id: Annotated[str, InjectedToolCallId]


def _normalize_query(query: str) -> str:
    """按消息契约统一换行并去除首尾空白。"""
    normalized = query.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > 2000:
        raise AppError(422, "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID", "检索问题长度必须为 1 到 2000 个码点。")
    return normalized


def _safe_evidence_result(
    candidates: list[ArchiveAgentEvidenceCandidate],
) -> dict[str, object]:
    """将原始候选投影为不含标识和分数的 S 编号结果。"""
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
    """把已安全投影结果编码为可序列化 ToolMessage。"""
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        tool_call_id=tool_call_id,
        name=tool_name,
    )


def build_archive_tools(*, runtime: ArchiveToolRuntime) -> tuple[Any, Any]:
    """根据请求运行时构造两个不携带客户端范围参数的工具。"""
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
        """查询当前项目正式目录并返回安全投影。"""
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
        """检索当前用户项目正式档案并返回安全 S 编号结果。"""
        del state
        normalized_query = _normalize_query(query)
        try:
            retrieval_call = eval_wrap(
                _retrieve_archive_agent_evidence,
                purpose="input",
                name="archive_agent_evidence_retrieval",
                description="当前授权范围返回给档案助手的请求内 Top-8 安全证据候选。",
            )
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
            if isinstance(registry, ArchiveEvidenceRegistry):
                registry.discard(tool_call_id)
            else:
                registry.pop(tool_call_id, None)
            raise

    return list_formal_archives, search_confirmed_archive_evidence
