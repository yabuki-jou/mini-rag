"""定义智慧档案正式检索的请求与证据响应契约。"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import EvidenceLocationType


class ArchiveRetrievalRequest(BaseModel):
    """接收项目内正式档案检索问题。

    ``top_k`` 限制服务端最多返回的证据数和后续处理量；授权范围始终由服务端上下文固定。

    Attributes:
        query: 用户提出的正式档案检索问题。
        top_k: 请求返回的最多证据数。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class ArchiveRetrievalItemRead(BaseModel):
    """返回一个正式原文 Chunk 的可追溯证据及最终重排分数。

    Attributes:
        chunk_id: 正式 Chunk 的内部标识。
        document_id: Chunk 所属归档文档的标识。
        filename: Chunk 所属的原文件名。
        location_type: 原文定位类型。
        location_start: 定位起始值，从 1 开始。
        location_end: 定位结束值，从 1 开始。
        excerpt: 可供用户核对的原文摘录。
        score: 向量检索相关性分数。
        reranker_score: 重排模型分数；未进行重排时为空。
    """

    chunk_id: str = Field(min_length=1, max_length=64)
    document_id: UUID
    filename: str = Field(min_length=1)
    location_type: EvidenceLocationType
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1)
    score: float
    reranker_score: float | None = None


class ArchiveRetrievalResponse(BaseModel):
    """返回正式范围内按相关性排序的证据集合。

    Attributes:
        items: 按最终相关性排序的证据列表。
        requested_top_k: 客户端请求的最多证据数。
        returned_count: 实际返回的证据数。
    """

    items: list[ArchiveRetrievalItemRead]
    requested_top_k: int = Field(ge=1, le=10)
    returned_count: int = Field(ge=0)


class ArchiveRetrievalDiagnosticRequest(BaseModel):
    """接收仅供开发环境固定集诊断使用的问题和标准证据。

    ``expected_evidence`` 可以省略以观察候选统计；提供时只用于固定集覆盖率比较，
    不会改变正式检索结果。

    Attributes:
        query: 固定集诊断使用的检索问题。
        expected_evidence: 可选的标准证据描述，仅用于诊断覆盖率比较。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    expected_evidence: dict[str, Any] | None = None


class ArchiveRetrievalDiagnosticCandidateRead(BaseModel):
    """返回候选的双排序数值，不包含文档标识、文件名或原文。

    Attributes:
        candidate_key: 候选内容的去标识化摘要键。
        dense_rank: 向量检索阶段的排名。
        dense_distance: 向量检索距离。
        reranker_rank: 重排阶段的排名。
        reranker_score: 重排模型分数。
        matches_expected_evidence: 是否匹配标准证据。
        public_coverage_match: 是否满足公开覆盖口径。
        candidate_kind: 候选与目标文档的关系类型。
        isolation_violation: 是否发现范围隔离违规。
    """

    candidate_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    dense_rank: int = Field(ge=1)
    dense_distance: float
    reranker_rank: int = Field(ge=1)
    reranker_score: float
    matches_expected_evidence: bool
    public_coverage_match: bool
    candidate_kind: Literal["SAME_DOCUMENT", "OTHER_DOCUMENT", "UNKNOWN"]
    isolation_violation: bool


class ArchiveRetrievalDiagnosticResponse(BaseModel):
    """返回开发环境固定集诊断所需的安全候选统计。

    Attributes:
        chroma_candidate_count: Chroma 返回的候选数量。
        candidate_count: 完成诊断的候选数量。
        reranker_query_mode: 本次诊断使用的重排查询模式。
        candidates: 去标识化候选诊断列表。
    """

    chroma_candidate_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    reranker_query_mode: Literal["c4_a", "c4_b"] = "c4_a"
    candidates: list[ArchiveRetrievalDiagnosticCandidateRead]
