"""提供项目档案助手的执行、历史和脱敏审计应用服务。"""

import json
from json import JSONDecodeError
from pathlib import Path
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
from app.agents.archive.history_reader import build_archive_history_graph
from app.agents.archive.runtime import ArchiveAgentRuntime
from app.agents.checkpoint import build_thread_config, open_checkpoint_store
from app.core.config import settings
from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.core.logging import get_request_id
from app.models import (
    AgentSession,
    AgentToolCallLog,
    AgentToolCallStatus,
    utc_now,
)
from app.schemas import (
    AgentToolCallLogRead,
    ArchiveAgentMessageRead,
    ArchiveAgentResponse,
    MessageRole,
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
    """按 HTTP 契约再次规范化应用服务入口。

    Args:
        message: 用户提交的本轮消息。
    """
    normalized = message.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > 2000:
        raise AppError(422, "VALIDATION_ERROR", "请求参数校验失败。")
    return normalized


def _validate_arguments_summary(
    tool_name: str,
    summary: dict[str, object] | None,
) -> dict[str, object] | None:
    """只接受冻结的两类参数摘要。

    Args:
        tool_name: 产生摘要的档案工具名称。
        summary: 待校验的脱敏参数摘要。
    """
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
    """只接受是否命中与结果数量两个字段。

    Args:
        summary: 待校验的脱敏结果摘要。
    """
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
    """确认 Graph 事件只含冻结工具、状态和摘要。

    Args:
        event: Graph 生成的单次工具观测事件。
    """
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
    """按会话和 Tool Call ID 幂等保存当前轮安全事件。

    Args:
        agent_session: 已通过服务端项目范围校验的档案助手会话。
        events: Graph 本轮产生的工具观测事件；事件内容仍需经过白名单复核。
        session: 当前请求使用的 PostgreSQL 会话。

    Raises:
        ValueError: 工具、状态或脱敏摘要不符合冻结契约。
    """
    for tool_event in events:
        # Checkpoint 或应用层重试可能再次提交同一 tool_call_id；先查重再校验和写入，
        # 使重复事件不会膨胀审计记录，同时仍拒绝新出现的非法事件。
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
    """提交当前轮日志，仅完整成功轮次更新会话时间。

    Args:
        agent_session: 当前档案助手会话。
        session: 当前请求使用的 PostgreSQL 会话。
        update_session: 是否把会话更新时间推进到当前完整轮次。

    ``update_session=False`` 专门用于模型或工具失败后的安全审计：失败事件可以
    落库，但不能用一次未完成请求把“最近会话”时间推进到不完整轮次。
    """
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
    """将 Graph 或 Checkpoint 失败投影为两个冻结 HTTP 错误码。

    Args:
        error: Graph 或 Checkpoint 抛出的原始异常。
    """
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
    """执行一轮档案 Graph，持久安全审计并返回可信投影。

    Args:
        agent_session: 已验证范围的档案助手会话，身份字段不由模型或客户端覆盖。
        message: 用户本轮输入。
        runtime: 负责 Checkpoint 读写和 Graph 执行的运行时适配器。
        session: 保存 PostgreSQL 工具审计和会话时间的数据库会话。

    Returns:
        只包含允许公开的回答状态、引用和请求标识的响应投影。

    Raises:
        AppError: 输入无效、Graph/Checkpoint 失败或安全审计无法提交。
    """
    normalized_message = _normalize_message(message)
    # Graph 可能已经写入 Checkpoint，但 PostgreSQL 审计仍需单独补偿；先保存失败
    # 事件并提交，再把底层异常映射为稳定错误，避免把部分工具结果交给客户端。
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
        # 先提交工具审计和会话状态，再构造公开响应；这样返回成功时，持久化的
        # 观察结果已经存在，响应投影失败也不会伪装成一次未记录的成功轮次。
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
    """记录评测需要的脱敏工具路径、路由结果和用户可见响应。

    Args:
        turn: 当前 Graph 轮次及其工具观测结果。
        response: 已构造的用户可见响应投影。
    """
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
    checkpoint_path: Path | None = None,
) -> list[BaseMessage]:
    """通过轻量 Graph 读取并验证 ARCHIVE 线程中的消息列表。

    Args:
        agent_session: 用于确定 Checkpoint 线程的档案助手会话。
        checkpoint_path: 可选 Checkpoint 文件路径；省略时使用应用配置。
    """
    try:
        with open_checkpoint_store(checkpoint_path or settings.agent_checkpoint_path) as store:
            graph = build_archive_history_graph(store.checkpointer)
            snapshot = graph.get_state(build_thread_config(agent_session.thread_id))
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
    checkpoint_path: Path | None = None,
) -> list[ArchiveAgentMessageRead]:
    """返回只含完整用户/助手轮次的可见历史。

    Args:
        agent_session: 已通过全部范围字段查找到的档案助手会话。
        checkpoint_path: 可选 Checkpoint 文件路径；省略时使用应用配置。

    Returns:
        忽略中间 ToolMessage 和未形成最终助手消息的孤立轮次后的历史投影。

    Raises:
        AppError: Checkpoint 不可读或消息结构不符合档案线程契约。
    """
    # ToolMessage 只服务当前 Graph 执行；历史接口使用稳定的用户/助手投影，避免
    # 把内部工具参数、原始候选或未完成轮次误展示给普通用户。
    messages = project_complete_archive_messages(
        _checkpoint_archive_messages(agent_session, checkpoint_path)
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
    """解析并再校验持久化的工具摘要。

    Args:
        raw_value: 数据库中保存的 JSON 摘要文本。
        tool_name: 产生摘要的档案工具名称。
        result: 是否按结果摘要而不是参数摘要解析。
    """
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
    """按创建时间正序返回经白名单复核的脱敏工具日志。

    Args:
        agent_session: 已通过全部范围字段查找到的档案助手会话。
        session: 当前请求使用的 PostgreSQL 会话。

    Returns:
        按创建顺序排列、且重新经过工具名、状态和摘要校验的日志投影。

    Raises:
        AppError: 数据库不可用或已持久化的脱敏日志不符合契约。
    """
    # 记录虽然已经脱敏，读取时仍重新执行白名单校验；旧数据或手工篡改不能借由
    # 历史接口绕过当前的公开工具边界。
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
