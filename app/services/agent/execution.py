"""提供 Agent Graph 执行、路由观测和响应转换能力。"""

from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlmodel import Session

from app.agents.admin.observability import (
    begin_tool_observation,
    consume_tool_observations,
)
from app.agents.admin.runtime import AdminAgentRuntime
from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.core.logging import get_request_id
from app.models import AgentSession, utc_now
from app.schemas import AgentExecutionStatus, AgentResponse
from app.services.agent.audit import (
    record_failed_observations,
    record_new_tool_calls,
)
from app.services.agent.messages import (
    checkpoint_messages,
    last_answer,
    sources_from_tool_messages,
)


def observe_agent_routing(messages: list[BaseMessage]) -> None:
    """每次 HTTP 执行只记录一次模型产生的工具调用与最终路由摘要。"""
    tool_calls = [
        {
            "name": str(call.get("name", "")),
            "arguments": call.get("args", {}),
        }
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    eval_wrap(
        tool_calls,
        purpose="state",
        name="agent_tool_calls",
        description="本轮模型生成且实际进入 Graph 路由的全部工具调用",
    )
    eval_wrap(
        {
            "used_tools": bool(tool_calls),
            "tool_names": [call["name"] for call in tool_calls],
        },
        purpose="state",
        name="agent_routing_decision",
        description="一次 HTTP 执行汇总后的 Agent 路由结果",
    )


def raise_agent_execution_error(error: Exception) -> None:
    """把 Graph 或 Checkpoint 异常转换为稳定 HTTP 业务错误。

    Args:
        error: Runtime 调用向应用服务传播的原始异常。

    Raises:
        AppError: 保留已有业务错误，或按超时、连接和普通执行失败分类。
    """
    if isinstance(error, AppError):
        raise error
    if isinstance(error, TimeoutError):
        raise AppError(503, "AGENT_TIMEOUT", "Agent 执行超时。") from error
    if isinstance(error, ConnectionError):
        raise AppError(503, "AGENT_CONNECTION_FAILED", "Agent 服务连接失败。") from error
    raise AppError(503, "AGENT_EXECUTION_FAILED", "Agent 执行失败。") from error


def commit_execution(
    agent_session: AgentSession,
    session: Session,
) -> None:
    """提交会话更新时间和本轮工具日志。

    Args:
        agent_session: 本轮成功执行的 Agent 会话。
        session: 当前业务数据库 Session。

    Raises:
        AppError: 业务审计事务提交失败时抛出。
    """
    agent_session.updated_at = utc_now()
    try:
        session.add(agent_session)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(500, "AGENT_EXECUTION_SAVE_FAILED", "Agent 执行记录保存失败。") from exc


def build_response(
    agent_session: AgentSession,
    state: dict[str, Any],
    new_messages: list[BaseMessage],
) -> AgentResponse:
    """把 Graph State 转换成稳定的 HTTP AgentResponse。

    Args:
        agent_session: 当前已授权会话。
        state: Runtime 返回的完整 Graph State；保留参数以稳定内部调用契约。
        new_messages: 本次执行新增的消息。

    Returns:
        已完成回答。
    """
    del state
    response = AgentResponse(
        session_id=agent_session.id,
        status=AgentExecutionStatus.COMPLETED,
        answer=last_answer(new_messages),
        sources=sources_from_tool_messages(new_messages),
        request_id=get_request_id(),
    )
    eval_wrap(
        response.model_dump(mode="json"),
        purpose="output",
        name="agent_response",
        description="Agent HTTP 应用服务生成的最终用户响应",
    )
    return response


def send_agent_message(
    agent_session: AgentSession,
    message: str,
    runtime: AdminAgentRuntime,
    session: Session,
) -> AgentResponse:
    """向授权 Agent 会话发送消息并执行 Graph。

    Args:
        agent_session: 已通过当前用户所有权校验的会话。
        message: 已通过 HTTP Schema 校验的用户消息。
        runtime: 当前请求使用的 Agent Runtime。
        session: 当前业务数据库 Session。

    Returns:
        已完成回答。

    Raises:
        AppError: Checkpoint 或 Graph 执行失败。
    """
    previous_messages = checkpoint_messages(runtime, agent_session.thread_id)
    begin_tool_observation()
    try:
        state = runtime.invoke(
            {
                "messages": [HumanMessage(content=message, id=str(uuid4()))],
                "user_id": str(agent_session.user_id),
                "kb_id": str(agent_session.kb_id),
            },
            thread_id=agent_session.thread_id,
        )
    except Exception as exc:
        observations = consume_tool_observations()
        record_failed_observations(agent_session, observations, session)
        commit_execution(agent_session, session)
        raise_agent_execution_error(exc)
    observations = consume_tool_observations()
    all_messages = state.get("messages", [])
    new_messages = list(all_messages[len(previous_messages):])
    observe_agent_routing(new_messages)
    record_new_tool_calls(agent_session, new_messages, observations, session)
    commit_execution(agent_session, session)
    return build_response(agent_session, state, new_messages)
