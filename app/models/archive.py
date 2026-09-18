"""定义智慧档案文档、字段和原文证据模型。"""

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.common import utc_now


# PostgreSQL 业务库使用 JSONB；SQLite 迁移测试仍可使用通用 JSON。
# `None` 必须存为 SQL NULL，而不是 JSON 字面量 `null`，这样字段名检查约束才能
# 区分未使用的值列。
ARCHIVE_JSON = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True), "postgresql"
)


class ArchiveDocumentStatus(str, Enum):
    """归档文档的七种用户可见状态。"""

    UPLOADED = "UPLOADED"
    PARSE_FAILED = "PARSE_FAILED"
    PARSED = "PARSED"
    SUGGESTION_FAILED = "SUGGESTION_FAILED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    PENDING_RECONFIRMATION = "PENDING_RECONFIRMATION"


class ArchiveFieldName(str, Enum):
    """每份档案固定维护的七个字段。"""

    TITLE = "TITLE"
    DOCUMENT_TYPE = "DOCUMENT_TYPE"
    DOCUMENT_DATE = "DOCUMENT_DATE"
    AUTHORING_ORGANIZATION = "AUTHORING_ORGANIZATION"
    VERSION_NUMBER = "VERSION_NUMBER"
    PROJECT_STAGE = "PROJECT_STAGE"
    KEYWORDS = "KEYWORDS"


class FieldReviewStatus(str, Enum):
    """字段的人工检查状态。"""

    PENDING_CHECK = "PENDING_CHECK"
    VALUE_CONFIRMED = "VALUE_CONFIRMED"
    EMPTY_ACCEPTED = "EMPTY_ACCEPTED"


class FieldSource(str, Enum):
    """字段值的来源。"""

    AI = "AI"
    MANUAL = "MANUAL"


class ArchiveDocumentType(str, Enum):
    """归档资料类型固定字典。"""

    CONTRACT = "CONTRACT"
    DESIGN = "DESIGN"
    CONSTRUCTION = "CONSTRUCTION"
    MEETING_MINUTES = "MEETING_MINUTES"
    ACCEPTANCE = "ACCEPTANCE"
    OTHER = "OTHER"


class ProjectStage(str, Enum):
    """项目阶段固定字典。"""

    PREPARATION = "PREPARATION"
    DESIGN = "DESIGN"
    CONSTRUCTION = "CONSTRUCTION"
    ACCEPTANCE = "ACCEPTANCE"
    CROSS_STAGE = "CROSS_STAGE"
    OTHER_STAGE = "OTHER_STAGE"


class EvidenceLocationType(str, Enum):
    """字段证据的冻结定位方式。"""

    PDF_PAGE = "PDF_PAGE"
    DOCX_PARAGRAPH = "DOCX_PARAGRAPH"
    TEXT_LINE_RANGE = "TEXT_LINE_RANGE"


class ArchiveDocument(SQLModel, table=True):
    """扩展原文件，保存归档状态和正式可见性事实。

    Attributes:
        document_id: 对应原始 `documents` 记录的主键，贯穿文件和向量存储。
        status: 用户可见的七态归档处理状态。
        current_snapshot_id: 当前不可变解析快照的 ID；同一文档最多一个。
        confirmed_by: 最后一次正式确认或重新确认的用户 ID。
        confirmed_at: 最后一次正式确认的 UTC 时间。
        final_index_snapshot_hash: 写入 Final Collection 所依据快照的 SHA-256。
        final_chunk_count: 当前正式索引中属于该文档的 Chunk 数量。
        last_error_code: 最近一次解析、建议或索引的受控失败代码。
        last_error_summary: 不暴露内部堆栈的最近一次失败摘要。
        version: 文档乐观锁版本；草稿或确认状态变化后递增。
        created_at: 归档扩展记录创建的 UTC 时间。
        updated_at: 归档扩展记录最后更新的 UTC 时间。
    """

    __tablename__ = "archive_documents"
    __table_args__ = (
        CheckConstraint("final_chunk_count >= 0", name="ck_archive_documents_final_chunk_count"),
        # CONFIRMED 是正式可见状态，必须同时具备确认人、确认时间、快照和索引哈希，
        # 防止业务层漏写任一项后文档仍被当作可检索资料。
        CheckConstraint(
            "status != 'CONFIRMED' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL "
            "AND current_snapshot_id IS NOT NULL AND length(final_index_snapshot_hash) = 64)",
            name="ck_archive_documents_confirmed_requirements",
        ),
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        Index("ix_archive_documents_status_confirmed_at_id", "status", "confirmed_at", "document_id"),
        Index("ix_archive_documents_status_updated_at_id", "status", "updated_at", "document_id"),
    )

    document_id: UUID = Field(primary_key=True)
    status: ArchiveDocumentStatus = Field(index=True)
    current_snapshot_id: UUID | None = Field(default=None, unique=True, index=True)
    confirmed_by: UUID | None = Field(default=None, foreign_key="users.id")
    confirmed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    final_index_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)
    final_chunk_count: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_summary: str | None = Field(default=None, max_length=500)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ParsedSnapshot(SQLModel, table=True):
    """当前归档文档可重建的、位置感知解析快照元数据。

    Attributes:
        id: 解析快照全局唯一标识。
        document_id: 快照所属归档文档 ID；当前设计中一份文档只有一个当前快照。
        snapshot_storage_path: 快照文本和定位片段在受控文件系统中的存储路径。
        snapshot_hash: 归一化解析快照的 SHA-256，用于一致性和索引校验。
        parser_name: 生成本快照的 Parser 名称。
        parser_version: 生成本快照的 Parser 版本。
        normalization_version: 文本/定位归一化规则的版本。
        text_character_count: 归一化后有效文本的字符数。
        fragment_count: 可定位解析片段的数量。
        created_at: 快照创建的 UTC 时间。
    """

    __tablename__ = "parsed_snapshots"
    __table_args__ = (
        # 快照必须含有可索引文本和可定位片段；document_id 唯一表示当前文档只有一个正式快照。
        CheckConstraint("text_character_count > 0", name="ck_parsed_snapshots_text_count"),
        CheckConstraint("fragment_count > 0", name="ck_parsed_snapshots_fragment_count"),
        ForeignKeyConstraint(["document_id"], ["archive_documents.document_id"], ondelete="CASCADE"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(unique=True, index=True)
    snapshot_storage_path: str = Field(min_length=1, max_length=1024)
    snapshot_hash: str = Field(min_length=64, max_length=64, index=True)
    parser_name: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=100)
    normalization_version: str = Field(min_length=1, max_length=50)
    text_character_count: int = Field(gt=0)
    fragment_count: int = Field(gt=0)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ArchiveFieldValue(SQLModel, table=True):
    """一个归档字段的值、来源与人工检查状态。

    Attributes:
        id: 字段值记录的全局唯一标识。
        document_id: 字段所属归档文档 ID。
        field_name: 七个固定正式字段之一，决定使用哪一种值列。
        text_value: 标题、类型、单位、版本或阶段等文本字段值。
        date_value: 文档日期字段值，仅用于 `DOCUMENT_DATE`。
        json_value: 关键词数组，仅用于 `KEYWORDS`。
        review_status: 待检查、确认有值或接受为空的人工检查状态。
        source: AI 建议或人工填写的字段来源。
        no_source_evidence: 人工值没有原文证据时的显式标记。
        updated_by: 最后填写、修改或检查字段的用户 ID。
        updated_at: 字段最后修改的 UTC 时间。
    """

    __tablename__ = "archive_field_values"
    __table_args__ = (
        UniqueConstraint("document_id", "field_name", name="uq_archive_field_values_document_field"),
        # 七个固定字段共用三种值列；该约束把字段名与允许使用的列绑定，避免同一记录
        # 同时写入互相矛盾的 text/date/json 值。
        CheckConstraint(
            "(field_name = 'DOCUMENT_DATE' AND text_value IS NULL AND json_value IS NULL) OR "
            "(field_name = 'KEYWORDS' AND text_value IS NULL AND date_value IS NULL) OR "
            "(field_name NOT IN ('DOCUMENT_DATE', 'KEYWORDS') AND date_value IS NULL AND json_value IS NULL)",
            name="ck_archive_field_values_value_column_by_name",
        ),
        ForeignKeyConstraint(["document_id"], ["archive_documents.document_id"], ondelete="CASCADE"),
        # EMPTY_ACCEPTED 表示人工确认字段确实为空，因此三种值列都必须是 SQL NULL。
        CheckConstraint("review_status != 'EMPTY_ACCEPTED' OR (text_value IS NULL AND date_value IS NULL AND json_value IS NULL)", name="ck_archive_field_values_empty_accepted"),
        # VALUE_CONFIRMED 只允许对应字段类型的值列非空，并拒绝空白文本冒充有效值。
        CheckConstraint(
            "review_status != 'VALUE_CONFIRMED' OR "
            "(field_name = 'DOCUMENT_DATE' AND date_value IS NOT NULL) OR "
            "(field_name = 'KEYWORDS' AND json_value IS NOT NULL) OR "
            "(field_name NOT IN ('DOCUMENT_DATE', 'KEYWORDS') AND text_value IS NOT NULL AND length(trim(text_value)) > 0)",
            name="ck_archive_field_values_confirmed_value",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(index=True)
    field_name: ArchiveFieldName = Field(index=True)
    text_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    date_value: date | None = Field(default=None)
    json_value: list[str] | None = Field(default=None, sa_column=Column(ARCHIVE_JSON, nullable=True))
    review_status: FieldReviewStatus = Field(default=FieldReviewStatus.PENDING_CHECK)
    source: FieldSource | None = Field(default=None)
    no_source_evidence: bool = Field(default=False)
    updated_by: UUID | None = Field(default=None, foreign_key="users.id")
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class FieldEvidence(SQLModel, table=True):
    """字段值可追溯到解析快照的原文摘录与定位。

    Attributes:
        id: 字段证据的全局唯一标识。
        field_value_id: 被该证据支持的字段值记录 ID。
        snapshot_id: 证据所属的冻结解析快照 ID。
        excerpt: 用户可查证的原文摘录。
        location_type: 页码、DOCX 段落或文本行号范围等定位类型。
        location_start: 从 1 开始的定位起始值。
        location_end: 从 1 开始的定位结束值；点定位与起始值相同。
        normalized_anchor: 用于 DOCX 等定位辅助核对的规范化文本锚点。
        created_at: 证据记录创建的 UTC 时间。
    """

    __tablename__ = "field_evidences"
    __table_args__ = (
        # 点定位只能指向一个位置，只有文本行范围允许 start 与 end 不同。
        CheckConstraint("location_start >= 1", name="ck_field_evidences_location_start"),
        CheckConstraint("location_end >= location_start", name="ck_field_evidences_location_range"),
        CheckConstraint("location_type = 'TEXT_LINE_RANGE' OR location_start = location_end", name="ck_field_evidences_point_location"),
        ForeignKeyConstraint(["field_value_id"], ["archive_field_values.id"], ondelete="CASCADE"),
        # 快照被证据引用时禁止删除，保证历史证据仍能回溯到原始定位上下文。
        ForeignKeyConstraint(["snapshot_id"], ["parsed_snapshots.id"], ondelete="RESTRICT"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    field_value_id: UUID = Field(index=True)
    snapshot_id: UUID = Field(index=True)
    excerpt: str = Field(min_length=1, sa_column=Column(Text, nullable=False))
    location_type: EvidenceLocationType = Field()
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    normalized_anchor: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
