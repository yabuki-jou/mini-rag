"""实现 Agent 会话、Graph 调用、响应转换和安全审计的应用服务。"""

import json
from json import JSONDecodeError
from typing import Any, Iterable
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import ValidationError
from sqlmodel import Session, select

from app.agents.admin.observability import (
    ToolObservation,
    begin_tool_observation,
    consume_tool_observations,
)
from app.agents.admin.runtime import AdminAgentRuntime
from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.core.logging import get_request_id
from app.models import (
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    KnowledgeBase,
    MessageRole,
    User,
    utc_now,
)
from app.schemas import (
    AgentExecutionStatus,
    AgentMessageRead,
    AgentResponse,
    AgentToolCallLogRead,
    SourceRead,
)


def create_agent_session(
    current_user: User,
    kb_id: UUID,
    session: Session,
) -> AgentSession:
    """为当前用户创建绑定知识库和 Graph 线程的 Agent 会话。

    Args:
        current_user: 已通过 HTTP 身份依赖校验的当前用户。
        kb_id: 客户端选择的知识库 UUID。
        session: 当前请求使用的业务数据库 Session。

    Returns:
        已提交并刷新的 AgentSession。

    Raises:
        AppError: 知识库不存在、不属于当前用户或会话保存失败。
    """
    knowledge_base = session.get(KnowledgeBase, kb_id)
    if knowledge_base is None:
        raise AppError(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在。")
    if knowledge_base.owner_id != current_user.id:
        raise AppError(403, "KNOWLEDGE_BASE_FORBIDDEN", "无权访问该知识库。")

    agent_session = AgentSession(user_id=current_user.id, kb_id=kb_id)
    try:
        session.add(agent_session)
        session.commit()
        session.refresh(agent_session)
    except Exception as exc:
        session.rollback()
        raise AppError(500, "AGENT_SESSION_CREATE_FAILED", "Agent 会话创建失败。") from exc
    return agent_session


def _get_checkpoint_snapshot(
    runtime: AdminAgentRuntime,
    thread_id: str,
) -> Any:
    """读取 Checkpoint，并把底层异常转换为安全错误代码。

    Args:
        runtime: 当前请求使用的 Agent Runtime。
        thread_id: AgentSession 中保存的稳定线程标识。

    Returns:
        Runtime 返回的 LangGraph StateSnapshot。

    Raises:
        AppError: Checkpoint 超时、连接失败或发生未知读取错误。
    """
    try:
        return runtime.get_state(thread_id=thread_id)
    except AppError:
        raise
    except TimeoutError as exc:
        raise AppError(
            503,
            "AGENT_CHECKPOINT_TIMEOUT",
            "Agent 会话状态读取超时。",
        ) from exc
    except ConnectionError as exc:
        raise AppError(
            503,
            "AGENT_CHECKPOINT_UNAVAILABLE",
            "Agent 会话状态暂时不可用。",
        ) from exc
    except Exception as exc:
        raise AppError(
            503,
            "AGENT_CHECKPOINT_FAILED",
            "Agent 会话状态读取失败。",
        ) from exc


def _checkpoint_messages(runtime: AdminAgentRuntime, thread_id: str) -> list[BaseMessage]:
    """读取并验证一个 Agent 线程的消息列表。

    Args:
        runtime: 当前请求使用的 Agent Runtime。
        thread_id: AgentSession 中保存的稳定 Graph 线程标识。

    Returns:
        Checkpoint 中的 LangChain 消息列表；新线程返回空列表。

    Raises:
        AppError: Checkpoint 消息结构损坏时抛出。
    """
    snapshot = _get_checkpoint_snapshot(runtime, thread_id)
    raw_messages = snapshot.values.get("messages", [])
    if not isinstance(raw_messages, list) or not all(
        isinstance(message, BaseMessage) for message in raw_messages
    ):
        raise AppError(500, "AGENT_CHECKPOINT_INVALID", "Agent 会话状态无效。")
    return raw_messages


def _parse_tool_payload(message: ToolMessage) -> dict[str, Any] | None:
    """把 JSON ToolMessage 转换为字典，非 JSON 错误消息返回 None。

    Args:
        message: Graph 中的一条工具结果消息。

    Returns:
        JSON 对象工具结果；内容不是 JSON 对象时返回 None。
    """
    if not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
    except (JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sources_from_tool_messages(messages: Iterable[BaseMessage]) -> list[SourceRead]:
    """提取制度检索工具返回的结构化引用。

    Args:
        messages: 当前回答之前需要检查的 Graph 消息。

    Returns:
        按检索顺序编号为 S1、S2 的来源列表。

    Raises:
        AppError: 制度工具返回了无法转换的来源字段时抛出。
    """
    sources: list[SourceRead] = []
    try:
        for message in messages:
            if not isinstance(message, ToolMessage) or message.name != "search_company_policy":
                continue
            payload = _parse_tool_payload(message)
            if payload is None or payload.get("found") is not True:
                continue
            for result in payload.get("results", []):
                sources.append(
                    SourceRead(
                        source_id=f"S{len(sources) + 1}",
                        chunk_id=result["chunk_id"],
                        document_id=result["document_id"],
                        document_name=result["document_name"],
                        page=result["page"],
                        excerpt=result["content"],
                        score=result["score"],
                    )
                )
    except (KeyError, TypeError, ValidationError) as exc:
        raise AppError(500, "AGENT_SOURCE_DATA_INVALID", "Agent 引用数据无效。") from exc
    return sources


def _last_answer(messages: Iterable[BaseMessage]) -> str:
    """读取当前执行中新产生的最后一条非空模型回答。

    Args:
        messages: 当前执行新增的 Graph 消息。

    Returns:
        去除两端空白后的助手自然语言回答。

    Raises:
        AppError: Graph 结束但没有产生有效文本回答时抛出。
    """
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            answer = message.content.strip()
            if answer:
                return answer
    raise AppError(502, "AGENT_RESPONSE_INVALID", "Agent 返回了无效回答。")


def _safe_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    """保留模型可见业务参数并明确丢弃所有身份字段。

    Args:
        tool_call: AIMessage 中的原始工具调用结构。

    Returns:
        只包含白名单字段的参数摘要。
    """
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return {}
    # 自由文本可能包含个人信息，只记录是否提供和长度。身份字段即使
    # 异常出现在模型参数中也不会进入摘要。
    summary: dict[str, Any] = {}
    for key in ("query",):
        value = args.get(key)
        if isinstance(value, str):
            summary[f"{key}_provided"] = bool(value.strip())
            summary[f"{key}_length"] = len(value)
    return summary


def _safe_result(message: ToolMessage | None) -> dict[str, Any] | None:
    """从工具结果中生成不含制度正文的摘要。

    Args:
        message: 与 Tool Call 对应的工具消息；未产生时为空。

    Returns:
        可持久化的安全结果摘要；错误或非 JSON 结果返回 None。
    """
    if message is None:
        return None
    payload = _parse_tool_payload(message)
    if payload is None:
        return None
    summary: dict[str, Any] = {}
    for key in ("status", "found"):
        if key in payload:
            summary[key] = payload[key]
    if isinstance(payload.get("results"), list):
        summary["result_count"] = len(payload["results"])
    return summary or None


def _tool_status(message: ToolMessage | None) -> AgentToolCallStatus:
    """根据工具消息计算 API 审计状态。

    Args:
        message: 与当前 Tool Call 对应的工具结果。

    Returns:
        完成或失败状态。
    """
    if message is None or getattr(message, "status", None) == "error":
        return AgentToolCallStatus.FAILED
    return AgentToolCallStatus.COMPLETED


def _record_new_tool_calls(
    agent_session: AgentSession,
    messages: list[BaseMessage],
    observations: tuple[ToolObservation, ...],
    session: Session,
) -> None:
    """把当前 Graph 执行新增的 Tool Call 幂等写入业务日志表。

    Args:
        agent_session: 工具调用所属且已授权的 Agent 会话。
        messages: 当前执行新增的 AIMessage 和 ToolMessage。
        observations: 当前 Graph 执行中按尝试顺序采集的安全耗时和错误。
        session: 当前业务数据库 Session；由外层统一提交事务。
    """
    tool_messages = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolMessage)
    }
    observations_by_call: dict[str, list[ToolObservation]] = {}
    for observation in observations:
        observations_by_call.setdefault(observation.tool_call_id, []).append(
            observation
        )
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            tool_call_id = str(tool_call.get("id", "")).strip()
            tool_name = str(tool_call.get("name", "")).strip()
            if not tool_call_id or not tool_name:
                continue
            existing = session.exec(
                select(AgentToolCallLog).where(
                    AgentToolCallLog.agent_session_id == agent_session.id,
                    AgentToolCallLog.tool_call_id == tool_call_id,
                )
            ).first()
            if existing is not None:
                continue
            tool_message = tool_messages.get(tool_call_id)
            status = _tool_status(tool_message)
            safe_result = _safe_result(tool_message)
            call_observations = observations_by_call.get(tool_call_id, [])
            duration_ms = (
                sum(item.duration_ms for item in call_observations)
                if call_observations
                else None
            )
            error_code = (
                call_observations[-1].error_code
                if call_observations and status == AgentToolCallStatus.FAILED
                else None
            )
            session.add(
                AgentToolCallLog(
                    agent_session_id=agent_session.id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    status=status,
                    arguments_summary_json=json.dumps(
                        _safe_arguments(tool_call),
                        ensure_ascii=False,
                        default=str,
                    ),
                    result_summary_json=(
                        json.dumps(safe_result, ensure_ascii=False)
                        if safe_result is not None
                        else None
                    ),
                    duration_ms=duration_ms,
                    error_code=(error_code or "AGENT_TOOL_FAILED")
                    if status == AgentToolCallStatus.FAILED
                    else None,
                )
            )


def _observe_agent_routing(messages: list[BaseMessage]) -> None:
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


def _record_failed_observations(
    agent_session: AgentSession,
    observations: tuple[ToolObservation, ...],
    session: Session,
) -> None:
    """在 Graph 整体失败时保存没有结果正文的工具失败审计。

    Args:
        agent_session: 当前已授权 Agent 会话。
        observations: Graph 抛出异常前已经采集的工具执行尝试。
        session: 当前业务数据库 Session；由外层统一提交。
    """
    grouped: dict[str, list[ToolObservation]] = {}
    for observation in observations:
        if observation.tool_call_id:
            grouped.setdefault(observation.tool_call_id, []).append(observation)
    for tool_call_id, attempts in grouped.items():
        existing = session.exec(
            select(AgentToolCallLog).where(
                AgentToolCallLog.agent_session_id == agent_session.id,
                AgentToolCallLog.tool_call_id == tool_call_id,
            )
        ).first()
        if existing is not None:
            continue
        last_attempt = attempts[-1]
        session.add(
            AgentToolCallLog(
                agent_session_id=agent_session.id,
                tool_call_id=tool_call_id,
                tool_name=last_attempt.tool_name,
                status=AgentToolCallStatus.FAILED,
                arguments_summary_json=None,
                result_summary_json=None,
                duration_ms=sum(item.duration_ms for item in attempts),
                error_code=last_attempt.error_code or "AGENT_TOOL_FAILED",
            )
        )


def _raise_agent_execution_error(error: Exception) -> None:
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


def _commit_execution(
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


def _build_response(
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
        answer=_last_answer(new_messages),
        sources=_sources_from_tool_messages(new_messages),
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
    previous_messages = _checkpoint_messages(runtime, agent_session.thread_id)
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
        _record_failed_observations(agent_session, observations, session)
        _commit_execution(agent_session, session)
        _raise_agent_execution_error(exc)
    observations = consume_tool_observations()
    all_messages = state.get("messages", [])
    new_messages = list(all_messages[len(previous_messages):])
    _observe_agent_routing(new_messages)
    _record_new_tool_calls(agent_session, new_messages, observations, session)
    _commit_execution(agent_session, session)
    return _build_response(agent_session, state, new_messages)


def read_agent_messages(
    agent_session: AgentSession,
    runtime: AdminAgentRuntime,
) -> list[AgentMessageRead]:
    """读取 Checkpoint 中用户可见的会话历史。

    Args:
        agent_session: 已通过当前用户所有权校验的会话。
        runtime: 用于读取对应线程 Checkpoint 的 Runtime。

    Returns:
        过滤 ToolMessage 和空 Tool Call AIMessage 后的消息列表。
    """
    messages = _checkpoint_messages(runtime, agent_session.thread_id)
    result: list[AgentMessageRead] = []
    pending_sources: list[SourceRead] = []
    for message in messages:
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            content = message.content.strip()
            if content:
                result.append(AgentMessageRead(role=MessageRole.USER, content=content))
            pending_sources = []
        elif isinstance(message, ToolMessage):
            pending_sources.extend(_sources_from_tool_messages([message]))
        elif isinstance(message, AIMessage) and isinstance(message.content, str):
            content = message.content.strip()
            if content:
                result.append(
                    AgentMessageRead(
                        role=MessageRole.ASSISTANT,
                        content=content,
                        sources=pending_sources,
                    )
                )
                pending_sources = []
    return result


def read_agent_tool_calls(
    agent_session: AgentSession,
    session: Session,
) -> list[AgentToolCallLogRead]:
    """读取当前 Agent 会话的脱敏工具调用日志。

    Args:
        agent_session: 已通过当前用户所有权校验的会话。
        session: 当前业务数据库 Session。

    Returns:
        按创建时间从旧到新排列的安全日志响应。

    Raises:
        AppError: 数据库中的摘要 JSON 已损坏时抛出。
    """
    records = session.exec(
        select(AgentToolCallLog)
        .where(AgentToolCallLog.agent_session_id == agent_session.id)
        .order_by(AgentToolCallLog.created_at.asc())
    ).all()
    try:
        return [
            AgentToolCallLogRead(
                id=record.id,
                tool_call_id=record.tool_call_id,
                tool_name=record.tool_name,
                status=record.status,
                arguments_summary=(
                    json.loads(record.arguments_summary_json)
                    if record.arguments_summary_json is not None
                    else None
                ),
                result_summary=(
                    json.loads(record.result_summary_json)
                    if record.result_summary_json is not None
                    else None
                ),
                duration_ms=record.duration_ms,
                error_code=record.error_code,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        ]
    except (JSONDecodeError, TypeError, ValidationError) as exc:
        raise AppError(500, "AGENT_TOOL_LOG_INVALID", "Agent 工具日志数据无效。") from exc
