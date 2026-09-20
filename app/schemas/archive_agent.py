"""定义 FR-042 项目档案助手的独立 HTTP 契约。"""

from datetime import datetime
from enum import Enum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import AgentToolCallStatus, EvidenceLocationType
from app.schemas.archive_question import ArchiveAnswerStatus


class MessageRole(str, Enum):
    """表示档案助手历史消息的发送角色。"""

    USER = "USER"
    ASSISTANT = "ASSISTANT"

class ArchiveAgentSessionCreate(BaseModel):
    """创建项目档案助手会话时只接受空 JSON 对象。"""

    model_config = ConfigDict(extra="forbid")


class ArchiveAgentSessionRead(BaseModel):
    """返回项目档案助手会话的最小公开信息。

    Attributes:
        id: 项目档案助手会话的全局唯一标识。
        project_id: 会话固定绑定的项目标识。
        created_at: 会话创建时间。
        updated_at: 会话最近一次成功更新时间。
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    project_id: UUID
    created_at: datetime
    updated_at: datetime


class ArchiveAgentMessageCreate(BaseModel):
    """接收一条规范化后的项目档案助手用户消息。

    Attributes:
        message: 规范化后的用户消息正文。
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> Any:
        """先统一换行和首尾 Unicode 空白，再执行字符串长度校验。

        Args:
            value: 待规范化的用户消息原始值。
        """
        if not isinstance(value, str):
            # 非字符串保持原值，交给 Pydantic 的字段类型校验产生客户端错误。
            return value
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class ArchiveAgentCitationRead(BaseModel):
    """返回不含持久化标识和检索分数的当前回答引用。

    Attributes:
        filename: 引用所属的原文件名。
        location_type: 引用位置的类型。
        location_start: 引用位置的起始值，从 1 开始。
        location_end: 引用位置的结束值，从 1 开始。
        excerpt: 可供用户核对的原文摘录。
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    location_type: EvidenceLocationType
    location_start: int = Field(ge=1)
    location_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_location_range(self) -> Self:
        """拒绝结束位置早于开始位置的引用范围。"""
        if self.location_end < self.location_start:
            raise ValueError("location_end 不能早于 location_start")
        return self


class ArchiveAgentMessageRead(BaseModel):
    """返回一个不含工具消息和内部标识的可见历史消息。

    Attributes:
        role: 消息发送角色。
        content: 对用户可见的消息正文。
        citations: 当前助手消息关联的脱敏引用列表。
    """

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str = Field(min_length=1)
    citations: list[ArchiveAgentCitationRead] = Field(default_factory=list)


class ArchiveAgentResponse(BaseModel):
    """返回项目档案助手当前轮次的可信最终投影。

    Attributes:
        session_id: 当前项目档案助手会话标识。
        answer_status: 当前回答的公开状态。
        answer: 面向用户的最终回答正文。
        citations: 当前回答关联的脱敏引用列表。
        request_id: 当前请求的追踪标识。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    answer_status: ArchiveAnswerStatus
    answer: str = Field(min_length=1)
    citations: list[ArchiveAgentCitationRead] = Field(default_factory=list)
    request_id: UUID


class AgentToolCallLogRead(BaseModel):
    """返回给会话所有者的脱敏档案工具调用记录。

    Attributes:
        id: 工具调用日志的全局唯一标识。
        tool_call_id: 模型生成的工具调用标识。
        tool_name: 被调用的正式工具名称。
        status: 工具调用的完成或失败状态。
        arguments_summary: 不包含身份字段的参数摘要。
        result_summary: 不包含内部原文和异常细节的结果摘要。
        duration_ms: 工具调用耗时；未知时为空。
        error_code: 可安全展示的错误代码；成功时为空。
        created_at: 日志创建时间。
        updated_at: 日志最近更新时间。
    """

    id: UUID
    tool_call_id: str
    tool_name: str
    status: AgentToolCallStatus
    arguments_summary: dict[str, Any] | None
    result_summary: dict[str, Any] | None
    duration_ms: float | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
