"""定义 FR-030 工程项目接口的请求和响应契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProjectCreate(BaseModel):
    """创建工程项目时允许客户端提交的字段。

    Attributes:
        name: 项目名称；服务会保存去除首尾空格后的值。
        description: 可选项目说明；不承担行业扩展字段。
        use_demo_checklist: 是否在创建事务内复制五项虚构演示清单。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200, description="项目名称；保存前会去除首尾空格。")
    description: str | None = Field(default=None, description="可选项目说明。")
    use_demo_checklist: bool = Field(
        default=False,
        description="是否复制五项虚构演示清单；该模板不代表法定或行业归档要求。",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """去除项目名称首尾空格，防止空白名称和伪重复名称。

        Args:
            value: 待规范化的项目名称。
        """
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("项目名称不能为空。")
        return normalized_name


class ProjectUpdate(BaseModel):
    """修改工程项目时允许客户端提交的字段。

    Attributes:
        name: 可选的新项目名称；传入时会去除首尾空格。
        description: 可选的新项目说明；显式传入 `null` 时清空说明。
        expected_version: 客户端读取到的项目版本，用于乐观锁校验。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        """规范化传入的新名称；未传入时保留 `None` 表示不修改。

        Args:
            value: 待规范化的新项目名称；未提交时为 ``None``。
        """
        if value is None:
            return None
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("项目名称不能为空。")
        return normalized_name

    @model_validator(mode="after")
    def require_mutable_field(self) -> "ProjectUpdate":
        """拒绝只携带版本号的空更新，避免无意义地增加版本。"""
        # 必须依据 model_fields_set 判断客户端是否提交 description，因为 None 是清空说明的
        # 有效操作，不能用字段值本身区分“未提交”和“显式传 null”。
        if not ({"name", "description"} & self.model_fields_set):
            raise ValueError("至少提交项目名称或项目说明之一。")
        return self


class ProjectRead(BaseModel):
    """返回给客户端的项目公开信息。

    Attributes:
        id: 项目唯一标识。
        name: 已规范化的项目名称。
        description: 项目可选说明。
        uses_demo_checklist: 项目创建时是否复制过内置演示清单。
        active_document_count: 当前未删除项目文档计数。
        version: 项目乐观锁版本。
        created_at: 项目创建时间。
        updated_at: 项目最后修改时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    uses_demo_checklist: bool
    active_document_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectPageRead(BaseModel):
    """项目列表的稳定分页响应。

    Attributes:
        items: 当前页内仅属于当前用户的项目。
        page: 从 1 开始的页码。
        page_size: 当前页的最大项目数。
        total: 当前用户可见项目总数。
    """

    items: list[ProjectRead]
    page: int
    page_size: int
    total: int
