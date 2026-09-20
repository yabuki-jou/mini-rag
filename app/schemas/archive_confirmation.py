"""定义 AV1-P09 人工确认请求契约。"""

from pydantic import BaseModel, ConfigDict, Field


class ArchiveConfirmationRequest(BaseModel):
    """携带文档当前版本的人工确认请求。

    Attributes:
        expected_version: 客户端读取到的文档版本，用于拒绝覆盖并发修改。
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
