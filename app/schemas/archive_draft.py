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
    """返回当前解析快照的可引用元数据。

    Attributes:
        id: 解析快照的全局唯一标识。
        snapshot_hash: 快照内容的摘要值。
        parser_name: 生成快照的解析器名称。
        parser_version: 生成快照的解析器版本。
        normalization_version: 文本规范化规则版本。
        fragment_count: 快照包含的可定位片段数量。
        created_at: 快照创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    snapshot_hash: str
    parser_name: str
    parser_version: str
    normalization_version: str
    fragment_count: int
    created_at: datetime


class FieldEvidenceInput(BaseModel):
    """字段更新请求中的一条位置感知原文证据。

    ``location_start`` 和 ``location_end`` 从 1 开始；页码与段落只能使用点定位，
    文本行范围才允许两个位置不同，具体业务校验由服务层执行。

    Attributes:
        excerpt: 支持字段值的原文摘录。
        location_type: 原文定位类型。
        location_start: 定位起始值，从 1 开始。
        location_end: 定位结束值，从 1 开始。
        normalized_anchor: 用于辅助核对的规范化文本锚点；没有时为空。
    """

    excerpt: str = Field(min_length=1)
    location_type: EvidenceLocationType
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    normalized_anchor: str | None = Field(default=None, max_length=200)


class FieldEvidenceRead(FieldEvidenceInput):
    """返回已保存的字段证据。

    Attributes:
        id: 字段证据的全局唯一标识。
        snapshot_id: 证据所属的解析快照标识。
        created_at: 证据创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    snapshot_id: UUID
    created_at: datetime


class FieldDraftRead(BaseModel):
    """返回七个固定字段之一的草稿值和证据。

    Attributes:
        id: 字段值记录的全局唯一标识。
        field_name: 固定字段名称。
        text_value: 文本类型字段值；非文本字段时为空。
        date_value: 日期类型字段值；非日期字段时为空。
        json_value: 关键词等列表类型字段值；非列表字段时为空。
        review_status: 字段当前的人工检查状态。
        source: 字段值来源；未标记时为空。
        no_source_evidence: 是否已明确确认没有原文证据。
        updated_by: 最近修改字段的用户标识；没有时为空。
        updated_at: 字段最近更新时间。
        evidences: 支持该字段值的原文证据列表。
    """

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
    """返回归档文档、字段草稿和下一步动作。

    Attributes:
        document: 归档文档的受控处理状态。
        fields: 七个固定字段的草稿值和证据。
        snapshot: 当前解析快照元数据；尚未解析时为空。
        next_actions: 服务端建议的下一步动作标识列表。
    """

    document: ProcessDocumentRead
    fields: list[FieldDraftRead]
    snapshot: ParsedSnapshotRead | None
    next_actions: list[str]


class ArchiveFieldUpdate(BaseModel):
    """更新单个归档字段并携带乐观锁版本。

    字段名由路由路径单独提供，因此服务层会根据字段名只保存对应的值列；人工值
    没有原文证据时必须显式使用 ``no_source_evidence``，避免把缺证据误判为漏传。
    ``expected_version`` 用于保护模型建议或人工编辑期间的并发修改。

    Attributes:
        text_value: 待保存的文本值；非文本字段时应为空。
        date_value: 待保存的日期值；非日期字段时应为空。
        json_value: 待保存的列表值；非列表字段时应为空。
        review_status: 更新后的字段检查状态。
        source: 更新后的字段值来源；未指定时为空。
        no_source_evidence: 是否明确确认该字段没有原文证据。
        evidences: 本次更新携带的原文证据列表。
        reason: 字段修改或无证据确认的说明；没有时为空。
        expected_version: 客户端读取到的文档版本，用于乐观锁校验。
    """

    text_value: str | None = None
    date_value: date | None = None
    json_value: list[str] | None = None
    review_status: FieldReviewStatus = FieldReviewStatus.PENDING_CHECK
    source: FieldSource | None = None
    no_source_evidence: bool = False
    evidences: list[FieldEvidenceInput] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=500)
    expected_version: int = Field(ge=1)
