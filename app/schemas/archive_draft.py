"""定义 AV1-P07 人工草稿、字段值和原文证据的 HTTP 契约。"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    ArchiveFieldName,
    EvidenceLocationType,
    FieldReviewStatus,
    FieldSource,
)
from app.schemas.document import ProcessDocumentRead


class ParsedSnapshotRead(BaseModel):
    """返回当前解析快照的可引用元数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    snapshot_hash: str
    parser_name: str
    parser_version: str
    normalization_version: str
    fragment_count: int
    created_at: datetime


class FieldEvidenceInput(BaseModel):
    """字段更新请求中的一条位置感知原文证据。"""

    excerpt: str = Field(min_length=1)
    location_type: EvidenceLocationType
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    normalized_anchor: str | None = Field(default=None, max_length=200)


class FieldEvidenceRead(FieldEvidenceInput):
    """返回已保存的字段证据。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    snapshot_id: UUID
    created_at: datetime


class FieldDraftRead(BaseModel):
    """返回七个固定字段之一的草稿值和证据。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field_name: ArchiveFieldName
    text_value: str | None
    date_value: date | None
    json_value: list[str] | None
    review_status: FieldReviewStatus
    source: FieldSource | None
    no_source_evidence: bool
    updated_by: UUID | None
    updated_at: datetime
    evidences: list[FieldEvidenceRead]


class ArchiveDraftRead(BaseModel):
    """返回归档文档、字段草稿和下一步动作。"""

    document: ProcessDocumentRead
    fields: list[FieldDraftRead]
    snapshot: ParsedSnapshotRead | None
    next_actions: list[str]


class ArchiveFieldUpdate(BaseModel):
    """更新单个归档字段并携带乐观锁版本。"""

    text_value: str | None = None
    date_value: date | None = None
    json_value: list[str] | None = None
    review_status: FieldReviewStatus = FieldReviewStatus.PENDING_CHECK
    source: FieldSource | None = None
    no_source_evidence: bool = False
    evidences: list[FieldEvidenceInput] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=500)
    expected_version: int = Field(ge=1)
