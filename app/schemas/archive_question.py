"""定义智慧档案带证据问答的请求和响应契约。"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.archive_retrieval import ArchiveRetrievalItemRead


class ArchiveAnswerStatus(str, Enum):
    """正式证据问答的两种可见结果。"""

    ANSWERED = "ANSWERED"
    REFUSED_NO_EVIDENCE = "REFUSED_NO_EVIDENCE"


class ArchiveQuestionRequest(BaseModel):
    """接收项目内正式档案问答问题。

    Attributes:
        question: 用户提出的项目档案问题。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=2000)


class ArchiveQuestionResponse(BaseModel):
    """返回仅基于正式 Chunk 的回答和引用。

    Attributes:
        answer_status: 回答或无依据拒答的公开状态。
        answer: 面向用户的回答或拒答正文。
        citations: 支持回答的正式检索证据列表。
    """

    answer_status: ArchiveAnswerStatus
    answer: str = Field(min_length=1)
    citations: list[ArchiveRetrievalItemRead]
