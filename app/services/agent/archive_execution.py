"""提供项目档案助手的执行、历史和脱敏审计应用服务。"""

import json
from json import JSONDecodeError
from typing import Any, Iterable
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import ValidationError
from sqlmodel import Session, select

from app.agents.archive.graph import (
    ArchiveAgentExecutionError,
    ArchiveAgentModelOutputError,
    ArchiveToolEvent,
)
from app.agents.archive.history import project_complete_archive_messages
from app.agents.archive.runtime import ArchiveAgentRuntime
from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.core.logging import get_request_id
from app.models import (
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    MessageRole,
    utc_now,
)
from app.schemas import (
    AgentToolCallLogRead,
    ArchiveAgentMessageRead,
    ArchiveAgentResponse,
)


_ARCHIVE_TOOL_NAMES = {
    "list_formal_archives",
    "search_confirmed_archive_evidence",
}
_ARCHIVE_TOOL_ERROR_CODES = {
    "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID",
    "ARCHIVE_AGENT_TOOL_CONNECTION_FAILED",
    "ARCHIVE_AGENT_TOOL_TIMEOUT",
    "ARCHIVE_AGENT_TOOL_FAILED",
}
_CATALOG_ARGUMENT_KEYS = {"filter_names", "page", "page_size"}
_EVIDENCE_ARGUMENT_KEYS = {"query_provided", "query_length"}
_RESULT_KEYS = {"found", "result_count"}
_CATALOG_FILTER_NAMES = {
    "document_type",
    "project_stage",
    "document_date_from",
    "document_date_to",
    "authoring_organization",
}


def _normalize_message(message: str) -> str:
    """按 HTTP 契约再次规范化应用服务入口。"""
    normalized = message.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > 2000:
        raise AppError(422, "VALIDATION_ERROR", "请求参数校验失败。")
    return normalized


def _validate_arguments_summary(
    tool_name: str,
    summary: dict[str, object] | None,
) -> dict[str, object] | None:
    """只接受冻结的两类参数摘要。"""
    if summary is None:
        return None
    expected_keys = (
        _CATALOG_ARGUMENT_KEYS
        if tool_name == "list_formal_archives"
        else _EVIDENCE_ARGUMENT_KEYS
    )
    if set(summary) != expected_keys:
        raise ValueError("archive tool argument summary keys are invalid")
    if tool_name == "list_formal_archives":
        filter_names = summary.get("filter_names")
        page = summary.get("page")
        page_size = summary.get("page_size")
        if (
            not isinstance(filter_names, list)
            or any(name not in _CATALOG_FILTER_NAMES for name in filter_names)
            or not isinstance(page, int)
            or isinstance(page, bool)
            or page < 1
            or not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 1 <= page_size <= 20
        ):
            raise ValueError("archive catalog argument summary is invalid")
    else:
        query_provided = summary.get("query_provided")
        query_length = summary.get("query_length")
        if (
            not isinstance(query_provided, bool)
            or not isinstance(query_length, int)
            or isinstance(query_length, bool)
            or not 0 <= query_length <= 2000
        ):
            raise ValueError("archive evidence argument summary is invalid")
    return summary


def _validate_result_summary(
    summary: dict[str, object] | None,
) -> dict[str, object] | None:
    """只接受是否命中与结果数量两个字段。"""
    if summary is None:
        return None
    found = summary.get("found")
    result_count = summary.get("result_count")
    if (
        set(summary) != _RESULT_KEYS
        or not isinstance(found, bool)
        or not isinstance(result_count, int)
        or isinstance(result_count, bool)
        or result_count < 0
        or found != (result_count > 0)
    ):
        raise ValueError("archive tool result summary is invalid")
    return summary


def _validated_event(
    event: ArchiveToolEvent,
) -> tuple[AgentToolCallStatus, dict[str, object] | None, dict[str, object] | None]:
    """确认 Graph 事件只含冻结工具、状态和摘要。"""
    if event.tool_name not in _ARCHIVE_TOOL_NAMES:
        raise ValueError("archive tool name is invalid")
    status = AgentToolCallStatus(event.status)
    if event.duration_ms < 0 or event.attempt_count not in {1, 2}:
        raise ValueError("archive tool observation is invalid")
    if status == AgentToolCallStatus.COMPLETED and event.error_code is not None:
        raise ValueError("completed archive tool cannot have an error code")
    if status == AgentToolCallStatus.FAILED and event.error_code not in _ARCHIVE_TOOL_ERROR_CODES:
        raise ValueError("archive tool error code is invalid")
    arguments = _validate_arguments_summary(event.tool_name, event.arguments_summary)
    result = _validate_result_summary(event.result_summary)
    return status, arguments, result


def record_archive_tool_events(
    *,
    agent_session: AgentSession,
    events: Iterable[ArchiveToolEvent],
    session: Session,
) -> None:
    """按会话和 Tool Call ID 幂等保存当前轮安全事件。"""
    for tool_event in events:
        existing = session.exec(
            select(AgentToolCallLog).where(
                AgentToolCallLog.agent_session_id == agent_session.id,
                AgentToolCallLog.tool_call_id == tool_event.tool_call_id,
            )
        ).first()
        if existing is not None:
            continue
        status, arguments, result = _validated_event(tool_event)
        session.add(
            AgentToolCallLog(
                agent_session_id=agent_session.id,
                tool_call_id=tool_event.tool_call_id,
                tool_name=tool_event.tool_name,
                status=status,
                arguments_summary_json=(
                    json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                    if arguments is not None
                    else None
                ),
                result_summary_json=(
                    json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                    if result is not None
                    else None
                ),
                duration_ms=tool_event.duration_ms,
                error_code=tool_event.error_code,
            )
        )


def _commit_archive_records(
    *,
    agent_session: AgentSession,
    session: Session,
    update_session: bool,
) -> None:
    """提交当前轮日志，仅完整成功轮次更新会话时间。"""
    if update_session:
        agent_session.updated_at = utc_now()
        session.add(agent_session)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(
            503,
            "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "项目档案助手暂不可用。",
        ) from exc


def _raise_archive_execution_error(error: Exception) -> None:
    """将 Graph 或 Checkpoint 失败投影为两个冻结 HTTP 错误码。"""
    if isinstance(error, ArchiveAgentModelOutputError):
        raise AppError(
            503,
            "ARCHIVE_AGENT_MODEL_OUTPUT_INVALID",
            "项目档案助手模型输出无效。",
        ) from error
    raise AppError(
        503,
        "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
        "项目档案助手暂不可用。",
    ) from error


def send_archive_agent_message(
    *,
    agent_session: AgentSession,
    message: str,
    runtime: ArchiveAgentRuntime,
    session: Session,
) -> ArchiveAgentResponse:
    """执行一轮档案 Graph，持久安全审计并返回可信投影。"""
    normalized_message = _normalize_message(message)
    try:
        runtime_result = runtime.invoke(
            {
                "messages": [HumanMessage(content=normalized_message, id=str(uuid4()))],
                "user_id": str(agent_session.user_id),
                "project_id": str(agent_session.project_id),
                "kb_id": str(agent_session.kb_id),
                "tool_call_count": 0,
            },
            thread_id=agent_session.thread_id,
        )
    except Exception as exc:
        events = exc.tool_events if isinstance(exc, ArchiveAgentExecutionError) else ()
        try:
            record_archive_tool_events(
                agent_session=agent_session,
                events=events,
                session=session,
            )
            _commit_archive_records(
                agent_session=agent_session,
                session=session,
                update_session=False,
            )
        except AppError:
            raise
        except Exception as audit_error:
            session.rollback()
            _raise_archive_execution_error(audit_error)
        _raise_archive_execution_error(exc)

    try:
        record_archive_tool_events(
            agent_session=agent_session,
            events=runtime_result.turn.tool_events,
            session=session,
        )
        _commit_archive_records(
            agent_session=agent_session,
            session=session,
            update_session=True,
        )
        response = ArchiveAgentResponse(
            session_id=agent_session.id,
            answer_status=runtime_result.turn.answer_status,
            answer=runtime_result.turn.answer,
            citations=list(runtime_result.turn.citations),
            request_id=get_request_id(),
        )
        _observe_archive_agent_turn(runtime_result.turn, response)
        return response
    except AppError:
        raise
    except Exception as exc:
        session.rollback()
        _raise_archive_execution_error(exc)


def _observe_archive_agent_turn(
    turn: Any,
    response: ArchiveAgentResponse,
) -> None:
    """记录评测需要的脱敏工具路径、路由结果和用户可见响应。"""
    safe_tool_calls = [
        {
            "tool_name": event.tool_name,
            "status": event.status,
            "error_code": event.error_code,
            "attempt_count": event.attempt_count,
            "arguments_summary": event.arguments_summary,
            "result_summary": event.result_summary,
        }
        for event in turn.tool_events
    ]
    eval_wrap(
        safe_tool_calls,
        purpose="state",
        name="archive_agent_tool_calls",
        description="当前轮实际执行的档案工具及其脱敏参数、结果和重试摘要。",
    )
    eval_wrap(
        {
            "answer_kind": turn.answer_kind,
            "answer_status": turn.answer_status.value,
            "tool_call_count": turn.tool_call_count,
            "tool_names": [item["tool_name"] for item in safe_tool_calls],
        },
        purpose="state",
        name="archive_agent_routing_decision",
        description="当前轮最终工具路径、回答类型和调用数量。",
    )
    eval_wrap(
        {
            "answer_status": response.answer_status.value,
            "answer": response.answer,
            "citations": [
                citation.model_dump(mode="json") for citation in response.citations
            ],
        },
        purpose="output",
        name="archive_agent_response",
        description="去除会话与请求标识后的项目档案助手最终用户响应。",
    )


def _checkpoint_archive_messages(
    agent_session: AgentSession,
    runtime: ArchiveAgentRuntime,
) -> list[BaseMessage]:
    """读取并验证 ARCHIVE 线程中的消息列表。"""
    try:
        snapshot = runtime.get_state(thread_id=agent_session.thread_id)
        messages = snapshot.values.get("messages", [])
        if not isinstance(messages, list) or not all(
            isinstance(message, BaseMessage) for message in messages
        ):
            raise TypeError("archive checkpoint messages are invalid")
        return messages
    except Exception as exc:
        _raise_archive_execution_error(exc)


def read_archive_agent_messages(
    agent_session: AgentSession,
    runtime: ArchiveAgentRuntime,
) -> list[ArchiveAgentMessageRead]:
    """返回只含完整用户/助手轮次的可见历史。"""
    messages = project_complete_archive_messages(
        _checkpoint_archive_messages(agent_session, runtime)
    )
    return [
        ArchiveAgentMessageRead(
            role=(MessageRole.USER if isinstance(message, HumanMessage) else MessageRole.ASSISTANT),
            content=str(message.content).strip(),
            citations=[],
        )
        for message in messages
        if isinstance(message, (HumanMessage, AIMessage))
    ]


def _decode_archive_summary(
    raw_value: str | None,
    *,
    tool_name: str,
    result: bool,
) -> dict[str, object] | None:
    """解析并再校验持久化的工具摘要。"""
    if raw_value is None:
        return None
    value = json.loads(raw_value)
    if not isinstance(value, dict):
        raise TypeError("archive tool summary must be an object")
    return (
        _validate_result_summary(value)
        if result
        else _validate_arguments_summary(tool_name, value)
    )


def read_archive_agent_tool_calls(
    agent_session: AgentSession,
    session: Session,
) -> list[AgentToolCallLogRead]:
    """按创建时间正序返回经白名单复核的脱敏工具日志。"""
    try:
        records = session.exec(
            select(AgentToolCallLog)
            .where(AgentToolCallLog.agent_session_id == agent_session.id)
            .order_by(AgentToolCallLog.created_at.asc())
        ).all()
    except Exception as exc:
        raise AppError(
            503,
            "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "项目档案助手暂不可用。",
        ) from exc
    try:
        result: list[AgentToolCallLogRead] = []
        for record in records:
            if record.tool_name not in _ARCHIVE_TOOL_NAMES:
                raise ValueError("archive tool name is invalid")
            if (
                record.status == AgentToolCallStatus.FAILED
                and record.error_code not in _ARCHIVE_TOOL_ERROR_CODES
            ):
                raise ValueError("archive tool error code is invalid")
            if record.status == AgentToolCallStatus.COMPLETED and record.error_code is not None:
                raise ValueError("completed archive tool cannot have an error code")
            result.append(
                AgentToolCallLogRead(
                    id=record.id,
                    tool_call_id=record.tool_call_id,
                    tool_name=record.tool_name,
                    status=record.status,
                    arguments_summary=_decode_archive_summary(
                        record.arguments_summary_json,
                        tool_name=record.tool_name,
                        result=False,
                    ),
                    result_summary=_decode_archive_summary(
                        record.result_summary_json,
                        tool_name=record.tool_name,
                        result=True,
                    ),
                    duration_ms=record.duration_ms,
                    error_code=record.error_code,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        return result
    except (JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        raise AppError(
            500,
            "ARCHIVE_AGENT_TOOL_LOG_INVALID",
            "项目档案助手工具记录无效。",
        ) from exc
