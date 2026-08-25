"""定义 FR-031 项目清单项接口的请求和响应契约。"""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ArchiveDocumentType, ProjectStage


class ChecklistFulfillmentStatus(str, Enum):
    """清单项在当前项目中的实时满足状态。"""

    SATISFIED = "SATISFIED"
    MISSING = "MISSING"
    NOT_PROVIDED = "NOT_PROVIDED"


class ChecklistItemCreate(BaseModel):
    """创建项目独立清单项时允许客户端提交的字段。

    Attributes:
        name: 清单项名称；保存前去除首尾空格。
        document_type: 该项要求的固定资料类型。
        is_required: 该项是否计入项目缺失结果。
        project_stage: 该项适用的固定项目阶段。
        description: 可选的满足条件说明；显式传入 ``null`` 时不保存说明。
        expected_project_version: 客户端读取到的项目版本，用于保护新增操作。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    document_type: ArchiveDocumentType
    is_required: bool
    project_stage: ProjectStage
    description: str | None = None
    expected_project_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """拒绝仅含空白的名称，避免产生无法辨认的清单项。"""
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("清单项名称不能为空。")
        return normalized_name


class ChecklistItemUpdate(BaseModel):
    """修改项目独立清单项时允许客户端提交的字段。

    Attributes:
        name: 可选的新名称；传入时会去除首尾空格。
        document_type: 可选的新资料类型。
        is_required: 可选的新必需属性；显式 ``false`` 也是有效修改。
        project_stage: 可选的新项目阶段。
        description: 可选的新说明；显式传入 ``null`` 时清空说明。
        expected_version: 客户端读取到的清单项版本，用于乐观锁校验。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    document_type: ArchiveDocumentType | None = None
    is_required: bool | None = None
    project_stage: ProjectStage | None = None
    description: str | None = None
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        """规范化显式提交的新名称；未提交则由 ``None`` 表示不修改。"""
        if value is None:
            return None
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("清单项名称不能为空。")
        return normalized_name

    @model_validator(mode="after")
    def require_mutable_field(self) -> "ChecklistItemUpdate":
        """拒绝仅携带版本号的空更新，避免无意义地递增资源版本。"""
        mutable_fields = {
            "name",
            "document_type",
            "is_required",
            "project_stage",
            "description",
        }
        if not (mutable_fields & self.model_fields_set):
            raise ValueError("至少提交一个可修改的清单项字段。")
        non_nullable_fields = {"name", "document_type", "is_required", "project_stage"}
        for field_name in non_nullable_fields & self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null。")
        return self


class ChecklistItemRead(BaseModel):
    """返回清单项及其由 PostgreSQL 实时计算出的满足状态。

    Attributes:
        id: 清单项的全局唯一标识。
        name: 已规范化的清单项名称。
        document_type: 当前要求的资料类型。
        is_required: 当前是否为必需项。
        project_stage: 当前适用阶段。
        description: 可选满足条件说明。
        fulfillment_status: 由已确认档案和已确认关联共同计算的状态。
        confirmed_document_count: 当前满足该项的已确认档案数量。
        version: 清单项乐观锁版本。
    """

    id: UUID
    name: str
    document_type: ArchiveDocumentType
    is_required: bool
    project_stage: ProjectStage
    description: str | None
    fulfillment_status: ChecklistFulfillmentStatus
    confirmed_document_count: int = Field(ge=0)
    version: int = Field(ge=1)


class ChecklistItemListRead(BaseModel):
    """返回当前项目的全部清单项；项目没有清单时 ``items`` 为空数组。"""

    items: list[ChecklistItemRead]


class ChecklistItemCreateResponse(BaseModel):
    """返回创建结果以及已原子递增后的项目版本。"""

    item: ChecklistItemRead
    project_version: int = Field(ge=1)
