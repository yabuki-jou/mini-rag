"""定义 AV1-P08 AI 建议动作的 HTTP 输入契约。"""

from pydantic import BaseModel, ConfigDict, Field


class ArchiveSuggestionRegenerateRequest(BaseModel):
    """请求对尚未人工编辑的 AI 草稿执行安全重新生成。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(
        ge=1,
        description="客户端读取到的草稿版本，用于防止模型调用期间覆盖并发修改。",
    )
