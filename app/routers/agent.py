"""提供企业知识库 Agent 会话、执行和历史查询接口。"""

from uuid import UUID

from fastapi import APIRouter, status

from app.dependencies import (
    AdminAgentRuntimeDep,
    CurrentUserDep,
    OwnedAgentSessionDep,
    SessionDep,
)
from app.models import AgentSession
from app.schemas import (
    AgentMessageCreate,
    AgentMessageRead,
    AgentResponse,
    AgentSessionCreate,
    AgentSessionRead,
    AgentToolCallLogRead,
)
from app.services.agent.audit import read_agent_tool_calls
from app.services.agent.execution import send_agent_message
from app.services.agent.messages import read_agent_messages
from app.services.agent.sessions import create_agent_session


router = APIRouter(prefix="/agent-sessions", tags=["agent"])


@router.post("", response_model=AgentSessionRead, status_code=status.HTTP_201_CREATED)
def create_agent_session_endpoint(
    payload: AgentSessionCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> AgentSession:
    """创建绑定当前用户和指定知识库的 Agent 会话。

    Args:
        payload: 只包含目标知识库 UUID 的请求体。
        current_user: 已通过 Bearer Access Token 校验的当前用户。
        session: 当前请求使用的业务数据库 Session。

    Returns:
        已保存的 Agent 会话。
    """
    return create_agent_session(current_user, payload.kb_id, session)


@router.post("/{session_id}/messages", response_model=AgentResponse)
def send_agent_message_endpoint(
    session_id: UUID,
    payload: AgentMessageCreate,
    agent_session: OwnedAgentSessionDep,
    runtime: AdminAgentRuntimeDep,
    session: SessionDep,
) -> AgentResponse:
    """执行一轮只读 Agent，并返回最终回答。

    Args:
        session_id: 路径中的会话 UUID，由所有权依赖消费并校验。
        payload: 当前用户的自然语言消息。
        agent_session: 已通过当前用户所有权校验的 Agent 会话。
        runtime: 连接独立 Checkpoint SQLite 的 Agent Runtime。
        session: 当前请求使用的业务数据库 Session。

    Returns:
        已完成的制度问答响应。
    """
    _ = session_id
    return send_agent_message(agent_session, payload.message, runtime, session)


@router.get("/{session_id}/messages", response_model=list[AgentMessageRead])
def read_agent_messages_endpoint(
    session_id: UUID,
    agent_session: OwnedAgentSessionDep,
    runtime: AdminAgentRuntimeDep,
) -> list[AgentMessageRead]:
    """读取当前用户可见的 Agent 对话历史。

    Args:
        session_id: 路径中的会话 UUID，由所有权依赖消费并校验。
        agent_session: 已通过当前用户所有权校验的 Agent 会话。
        runtime: 用于读取同一线程 Checkpoint 的 Agent Runtime。

    Returns:
        不包含 ToolMessage 和内部状态的用户、助手消息。
    """
    _ = session_id
    return read_agent_messages(agent_session, runtime)


@router.get("/{session_id}/tool-calls", response_model=list[AgentToolCallLogRead])
def read_agent_tool_calls_endpoint(
    session_id: UUID,
    agent_session: OwnedAgentSessionDep,
    session: SessionDep,
) -> list[AgentToolCallLogRead]:
    """读取当前会话的脱敏工具调用日志。

    Args:
        session_id: 路径中的会话 UUID，由所有权依赖消费并校验。
        agent_session: 已通过当前用户所有权校验的 Agent 会话。
        session: 当前请求使用的业务数据库 Session。

    Returns:
        按创建时间正序排列的安全工具日志。
    """
    _ = session_id
    return read_agent_tool_calls(agent_session, session)
