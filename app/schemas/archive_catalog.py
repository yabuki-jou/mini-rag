"""定义 AV1-P10 清单关联、处理列表、正式目录和审计响应契约。"""

from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    ArchiveDocumentStatus,
    ArchiveDocumentType,
    ChecklistLinkStatus,
    ProjectStage,
)
from app.schemas.archive_draft import FieldDraftRead


class ArchiveAuditOperationType(str, Enum):
    """审计查询允许使用的受控业务操作类型。"""

    ARCHIVE_CONFIRMED = "ARCHIVE_CONFIRMED"
    ARCHIVE_CONFIRMATION_CANCELLED = "ARCHIVE_CONFIRMATION_CANCELLED"
    ARCHIVE_FIELD_UPDATED = "ARCHIVE_FIELD_UPDATED"
    CHECKLIST_ITEM_CREATED = "CHECKLIST_ITEM_CREATED"
    CHECKLIST_ITEM_UPDATED = "CHECKLIST_ITEM_UPDATED"
    CHECKLIST_ITEM_DELETED = "CHECKLIST_ITEM_DELETED"
    CHECKLIST_LINK_CONFIRMED = "CHECKLIST_LINK_CONFIRMED"
    CHECKLIST_LINK_DELETED = "CHECKLIST_LINK_DELETED"
    PARSE_RETRIED = "PARSE_RETRIED"
    SUGGESTION_RETRIED = "SUGGESTION_RETRIED"
    SUGGESTION_REGENERATED = "SUGGESTION_REGENERATED"
    DOCUMENT_DELETED = "DOCUMENT_DELETED"


class ChecklistLinkSuggestionRead(BaseModel):
    """返回按资料类型和项目阶段匹配的非正式关联建议。"""

    checklist_item_id: UUID
    name: str
    document_type: ArchiveDocumentType
    project_stage: ProjectStage
    is_required: bool
    already_linked: bool


class ChecklistLinkSuggestionListRead(BaseModel):
    """返回一份档案的全部关联建议。"""

    items: list[ChecklistLinkSuggestionRead]


class ChecklistLinkCreate(BaseModel):
    """携带文档和清单项当前版本的人工关联请求。"""

    model_config = ConfigDict(extra="forbid")

    checklist_item_id: UUID
    expected_document_version: int = Field(ge=1)
    expected_checklist_item_version: int = Field(ge=1)


class ChecklistLinkRead(BaseModel):
    """返回一条已确认或已失效的档案—清单关联。"""

    id: UUID
    document_id: UUID
    checklist_item_id: UUID
    status: ChecklistLinkStatus
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    invalidated_at: datetime | None
    invalidated_reason: str | None
    version: int

    model_config = ConfigDict(from_attributes=True)


class ChecklistLinkListRead(BaseModel):
    """返回文档已有的全部关联记录。"""

    items: list[ChecklistLinkRead]


class ProcessDocumentPageRead(BaseModel):
    """返回项目内未删除文档的稳定分页列表。"""

    items: list["ProcessDocumentRead"]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class ArchiveSummaryRead(BaseModel):
    """返回正式档案目录中的结构化摘要。"""

    id: UUID
    filename: str
    status: ArchiveDocumentStatus
    title: str | None
    document_type: ArchiveDocumentType | None
    document_date: date | None
    authoring_organization: str | None
    project_stage: ProjectStage | None
    confirmed_at: datetime | None
    version: int


class ArchivePageRead(BaseModel):
    """返回正式档案目录的稳定分页结果。"""

    items: list[ArchiveSummaryRead]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class ArchiveDetailRead(ArchiveSummaryRead):
    """返回正式档案详情及七个字段证据。"""

    fields: list[FieldDraftRead]


class AuditLogRead(BaseModel):
    """返回一条不含正文的脱敏业务审计。"""

    id: UUID
    operation_type: str
    resource_type: str
    resource_id: UUID
    redacted_summary: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogPageRead(BaseModel):
    """返回项目审计的稳定分页结果。"""

    items: list[AuditLogRead]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


from app.schemas.document import ProcessDocumentRead  # noqa: E402  循环类型前置声明

ProcessDocumentPageRead.model_rebuild()
