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
    """返回按资料类型和项目阶段匹配的非正式关联建议。

    Attributes:
        checklist_item_id: 匹配到的清单项标识。
        name: 清单项名称。
        document_type: 清单项要求的资料类型。
        project_stage: 清单项适用的项目阶段。
        is_required: 清单项是否为必需项。
        already_linked: 当前文档是否已经存在该关联。
    """

    checklist_item_id: UUID
    name: str
    document_type: ArchiveDocumentType
    project_stage: ProjectStage
    is_required: bool
    already_linked: bool


class ChecklistLinkSuggestionListRead(BaseModel):
    """返回一份档案的全部关联建议。

    Attributes:
        items: 当前文档可用的清单关联建议列表。
    """

    items: list[ChecklistLinkSuggestionRead]


class ChecklistLinkCreate(BaseModel):
    """携带文档和清单项当前版本的人工关联请求。

    Attributes:
        checklist_item_id: 要关联的清单项标识。
        expected_document_version: 客户端读取到的文档版本。
        expected_checklist_item_version: 客户端读取到的清单项版本。
    """

    model_config = ConfigDict(extra="forbid")

    checklist_item_id: UUID
    expected_document_version: int = Field(ge=1)
    expected_checklist_item_version: int = Field(ge=1)


class ChecklistLinkRead(BaseModel):
    """返回一条已确认或已失效的档案—清单关联。

    Attributes:
        id: 关联记录的全局唯一标识。
        document_id: 被关联的归档文档标识。
        checklist_item_id: 被关联的清单项标识。
        status: 关联当前的确认或失效状态。
        confirmed_by: 确认该关联的用户标识；未确认时为空。
        confirmed_at: 关联确认时间；未确认时为空。
        invalidated_at: 关联失效时间；未失效时为空。
        invalidated_reason: 关联失效原因；未失效时为空。
        version: 关联记录的乐观锁版本。
    """

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
    """返回文档已有的全部关联记录。

    Attributes:
        items: 当前文档的关联记录列表。
    """

    items: list[ChecklistLinkRead]


class ProcessDocumentPageRead(BaseModel):
    """返回项目内未删除文档的稳定分页列表。

    Attributes:
        items: 当前页的文档处理状态列表。
        page: 从 1 开始的当前页码。
        page_size: 当前页的最大记录数。
        total: 符合条件的文档总数。
    """

    items: list["ProcessDocumentRead"]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class ArchiveSummaryRead(BaseModel):
    """返回正式档案目录中的结构化摘要。

    Attributes:
        id: 归档文档的全局唯一标识。
        filename: 原文件名。
        status: 归档文档当前状态。
        title: 人工确认的文档标题；未填写时为空。
        document_type: 人工确认的资料类型；未填写时为空。
        document_date: 人工确认的文档日期；未填写时为空。
        authoring_organization: 人工确认的编制单位；未填写时为空。
        project_stage: 人工确认的项目阶段；未填写时为空。
        confirmed_at: 最近一次人工确认时间；未确认时为空。
        version: 归档文档的乐观锁版本。
    """

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
    """返回正式档案目录的稳定分页结果。

    Attributes:
        items: 当前页的正式档案摘要列表。
        page: 从 1 开始的当前页码。
        page_size: 当前页的最大记录数。
        total: 符合条件的正式档案总数。
    """

    items: list[ArchiveSummaryRead]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class ArchiveDetailRead(ArchiveSummaryRead):
    """返回正式档案详情及七个字段证据。

    Attributes:
        fields: 归档文档七个固定字段的值和证据列表。
    """

    fields: list[FieldDraftRead]


class AuditLogRead(BaseModel):
    """返回一条不含正文的脱敏业务审计。

    Attributes:
        id: 审计记录的全局唯一标识。
        actor_id: 执行业务操作的用户标识。
        operation_type: 面向审计查询的业务操作名称。
        resource_type: 被操作资源的类型。
        resource_id: 被操作资源的标识。
        redacted_summary: 不含正文和敏感信息的操作摘要。
        created_at: 审计记录创建时间。
    """

    id: UUID
    actor_id: UUID
    operation_type: str
    resource_type: str
    resource_id: UUID
    redacted_summary: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogPageRead(BaseModel):
    """返回项目审计的稳定分页结果。

    Attributes:
        items: 当前页的脱敏审计记录列表。
        page: 从 1 开始的当前页码。
        page_size: 当前页的最大记录数。
        total: 符合条件的审计记录总数。
    """

    items: list[AuditLogRead]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


from app.schemas.document import ProcessDocumentRead  # noqa: E402  循环类型前置声明

ProcessDocumentPageRead.model_rebuild()
