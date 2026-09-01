"""定义智慧档案正式检索的请求与证据响应契约。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import EvidenceLocationType


class ArchiveRetrievalRequest(BaseModel):
    """接收项目内正式档案检索问题。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class ArchiveRetrievalItemRead(BaseModel):
    """返回一个正式原文 Chunk 的可追溯证据及最终重排分数。"""

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
    """返回正式范围内按相关性排序的证据集合。"""

    items: list[ArchiveRetrievalItemRead]
    requested_top_k: int = Field(ge=1, le=10)
    returned_count: int = Field(ge=0)


class ArchiveRetrievalDiagnosticRequest(BaseModel):
    """接收仅供开发环境固定集诊断使用的问题和标准证据。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    expected_evidence: dict[str, Any] | None = None


class ArchiveRetrievalDiagnosticCandidateRead(BaseModel):
    """返回候选的双排序数值，不包含文档标识、文件名或原文。"""

    dense_rank: int = Field(ge=1)
    dense_distance: float
    reranker_rank: int = Field(ge=1)
    reranker_score: float
    matches_expected_evidence: bool
    candidate_kind: str | None = None


class ArchiveRetrievalDiagnosticResponse(BaseModel):
    """返回开发环境固定集诊断所需的安全候选统计。"""

    chroma_candidate_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    candidates: list[ArchiveRetrievalDiagnosticCandidateRead]
