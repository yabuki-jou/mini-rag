"""提供 Agent 工具调用的脱敏审计和日志读取能力。"""

import json
from json import JSONDecodeError
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import ValidationError
from sqlmodel import Session, select

from app.agents.admin.observability import ToolObservation
from app.core.errors import AppError
from app.models import AgentSession, AgentToolCallLog, AgentToolCallStatus
from app.schemas import AgentToolCallLogRead
from app.services.agent.messages import parse_tool_payload


def safe_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
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


def safe_result(message: ToolMessage | None) -> dict[str, Any] | None:
    """从工具结果中生成不含制度正文的摘要。

    Args:
        message: 与 Tool Call 对应的工具消息；未产生时为空。

    Returns:
        可持久化的安全结果摘要；错误或非 JSON 结果返回 None。
    """
    if message is None:
        return None
    payload = parse_tool_payload(message)
    if payload is None:
        return None
    summary: dict[str, Any] = {}
    for key in ("status", "found"):
        if key in payload:
            summary[key] = payload[key]
    if isinstance(payload.get("results"), list):
        summary["result_count"] = len(payload["results"])
    return summary or None


def tool_status(message: ToolMessage | None) -> AgentToolCallStatus:
    """根据工具消息计算 API 审计状态。

    Args:
        message: 与当前 Tool Call 对应的工具结果。

    Returns:
        完成或失败状态。
    """
    if message is None or getattr(message, "status", None) == "error":
        return AgentToolCallStatus.FAILED
    return AgentToolCallStatus.COMPLETED


def record_new_tool_calls(
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
            status = tool_status(tool_message)
            result_summary = safe_result(tool_message)
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
                        safe_arguments(tool_call),
                        ensure_ascii=False,
                        default=str,
                    ),
                    result_summary_json=(
                        json.dumps(result_summary, ensure_ascii=False)
                        if result_summary is not None
                        else None
                    ),
                    duration_ms=duration_ms,
                    error_code=(error_code or "AGENT_TOOL_FAILED")
                    if status == AgentToolCallStatus.FAILED
                    else None,
                )
            )


def record_failed_observations(
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
