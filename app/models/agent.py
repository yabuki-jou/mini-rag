"""定义企业知识库 Agent 的业务会话和工具调用审计实体。"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.models.common import utc_now


class AgentToolCallStatus(str, Enum):
    """表示一条 Agent 工具调用当前可公开的执行状态。

    Attributes:
        COMPLETED: 只读工具完成。
        FAILED: 工具参数、业务校验或执行过程失败。
    """

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentType(str, Enum):
    """表示 Agent 会话所属的业务类型。

    Attributes:
        POLICY: 仅绑定用户和知识库的企业制度会话。
        ARCHIVE: 同时绑定用户、项目和项目知识库的档案会话。
    """

    POLICY = "POLICY"
    ARCHIVE = "ARCHIVE"


class AgentSession(SQLModel, table=True):
    """把一个 HTTP Agent 会话绑定到授权用户、知识库和 Graph 线程。

    Attributes:
        id: 对外暴露的 Agent 会话 UUID。
        user_id: 创建并拥有该会话的可信用户 ID。
        kb_id: 该会话固定检索的知识库 ID。
        agent_type: 会话所属的制度或项目档案业务类型。
        project_id: 档案会话固定绑定的项目 ID；制度会话为空。
        thread_id: 对应 LangGraph Checkpoint 的唯一线程标识。
        created_at: 会话创建的 UTC 时间。
        updated_at: 最近一次成功执行 Graph 的 UTC 时间。
    """

    __tablename__ = "agent_sessions"
    __table_args__ = (
        CheckConstraint(
            "agent_type IN ('POLICY', 'ARCHIVE')",
            name="ck_agent_sessions_agent_type",
        ),
        CheckConstraint(
            "(agent_type = 'POLICY' AND project_id IS NULL) "
            "OR (agent_type = 'ARCHIVE' AND project_id IS NOT NULL)",
            name="ck_agent_sessions_type_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "kb_id"],
            ["projects.id", "projects.kb_id"],
            name="fk_agent_sessions_project_kb",
            ondelete="CASCADE",
            match="SIMPLE",
        ),
        Index("ix_agent_sessions_project_id", "project_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    kb_id: UUID = Field(foreign_key="knowledge_bases.id", index=True)
    agent_type: AgentType = Field(
        default=AgentType.POLICY,
        sa_column=Column(
            String(length=16),
            nullable=False,
            server_default=AgentType.POLICY.value,
        ),
    )
    project_id: UUID | None = Field(default=None)
    thread_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=100,
        unique=True,
        index=True,
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentToolCallLog(SQLModel, table=True):
    """保存可由当前用户查看的脱敏工具调用审计记录。

    Attributes:
        id: 日志记录 UUID。
        agent_session_id: 工具调用所属的 Agent 会话。
        tool_call_id: 模型生成的 Tool Call ID，同一会话内唯一。
        tool_name: 被调用的正式工具名称。
        status: 调用完成或失败状态。
        arguments_summary_json: 不包含身份字段的参数摘要 JSON。
        result_summary_json: 不包含制度正文和内部异常的结果摘要 JSON。
        duration_ms: 工具耗时；A-08 完成精确持久化前允许为空。
        error_code: 可安全展示的稳定错误代码；成功时为空。
        created_at: 首次观察到工具调用的 UTC 时间。
        updated_at: 日志记录最后更新时间。
    """

    __tablename__ = "agent_tool_call_logs"
    __table_args__ = (
        UniqueConstraint(
            "agent_session_id",
            "tool_call_id",
            name="uq_agent_tool_logs_session_call",
        ),
        ForeignKeyConstraint(
            ["agent_session_id"],
            ["agent_sessions.id"],
            name="fk_agent_tool_logs_session_cascade",
            ondelete="CASCADE",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    agent_session_id: UUID = Field(index=True)
    tool_call_id: str = Field(min_length=1, max_length=200, index=True)
    tool_name: str = Field(min_length=1, max_length=100, index=True)
    status: AgentToolCallStatus = Field(index=True)
    arguments_summary_json: str | None = Field(default=None)
    result_summary_json: str | None = Field(default=None)
    duration_ms: float | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)
