"""定义智慧档案正式检索的请求与证据响应契约。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import EvidenceLocationType


class ArchiveRetrievalRequest(BaseModel):
    """接收项目内正式档案检索问题。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class ArchiveRetrievalItemRead(BaseModel):
    """返回一个正式原文 Chunk 的可追溯证据。"""

    chunk_id: str = Field(min_length=1, max_length=64)
    document_id: UUID
    filename: str = Field(min_length=1)
    location_type: EvidenceLocationType
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1)
    score: float


class ArchiveRetrievalResponse(BaseModel):
    """返回正式范围内按相关性排序的证据集合。"""

    items: list[ArchiveRetrievalItemRead]
    requested_top_k: int = Field(ge=1, le=10)
    returned_count: int = Field(ge=0)
