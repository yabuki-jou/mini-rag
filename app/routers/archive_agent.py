"""提供项目档案助手会话、消息、历史和审计接口。"""

from contextlib import contextmanager, nullcontext
from collections.abc import Iterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.agents.archive.runtime import build_archive_runtime
from app.core.errors import AppError
from app.dependencies import ArchiveAgentSessionDep, ProjectContextDep, SessionDep
from app.models import AgentSession
from app.schemas import (
    AgentToolCallLogRead,
    ArchiveAgentMessageCreate,
    ArchiveAgentMessageRead,
    ArchiveAgentResponse,
    ArchiveAgentSessionCreate,
    ArchiveAgentSessionRead,
)
from app.services.agent.archive_execution import (
    read_archive_agent_messages,
    read_archive_agent_tool_calls,
    send_archive_agent_message,
)
from app.services.agent.archive_sessions import (
    create_archive_agent_session,
    find_archive_agent_session,
    find_latest_archive_agent_session,
)


router = APIRouter(
    prefix="/projects/{project_id}/agent-sessions",
    tags=["archive-agent"],
)


@router.post("", response_model=ArchiveAgentSessionRead, status_code=status.HTTP_201_CREATED)
def create_archive_agent_session_endpoint(
    project_id: UUID,
    payload: ArchiveAgentSessionCreate,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> AgentSession:
    """使用服务端已验证的项目范围创建档案助手会话。

    Args:
        project_id: URL 中的项目标识；实际授权范围由 project_context 提供。
        payload: 必须为空对象的创建请求体。
        project_context: 已验证的用户、项目和知识库范围。
        session: 当前请求使用的业务数据库会话。

    Returns:
        已保存的项目档案助手会话。
    """
    _ = payload, project_id
    return create_archive_agent_session(
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        session=session,
    )


@router.get("/latest", response_model=ArchiveAgentSessionRead | None)
def find_latest_archive_agent_session_endpoint(
    project_id: UUID,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> AgentSession | None:
    """返回当前项目最近的档案助手会话，或在不存在时返回空值。

    Args:
        project_id: URL 中的项目标识；实际授权范围由 project_context 提供。
        project_context: 已验证的用户、项目和知识库范围，不能被客户端参数覆盖。
        session: 用于读取会话元数据的业务数据库会话。

    Returns:
        最近一条档案助手会话；项目尚无会话时返回 ``None``，对应 HTTP 200 的 JSON null。

    Raises:
        AppError: 会话元数据查询依赖不可用时，由 Service 映射为冻结的 503 契约。
    """
    _ = project_id
    return find_latest_archive_agent_session(
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        session=session,
    )


def _runtime_for_session(
    agent_session: AgentSession,
    session: SessionDep,
):
    """使用五要素已校验会话构造当前请求 Runtime。

    Args:
        agent_session: 已按用户、项目、知识库和会话类型校验过的业务会话。
        session: 由请求依赖注入的数据库会话，用于 Runtime 内部的受控操作。

    Returns:
        档案助手 Runtime 的上下文管理器；Router 不直接持有数据库或模型客户端。

    Raises:
        AssertionError: 会话缺少项目绑定，表示违反 ARCHIVE 会话的数据不变量。
    """
    if agent_session.project_id is None:
        raise AssertionError("ARCHIVE 会话必须绑定项目。")
    return build_archive_runtime(
        user_id=agent_session.user_id,
        project_id=agent_session.project_id,
        kb_id=agent_session.kb_id,
        session_factory=lambda: nullcontext(session),
    )


@contextmanager
def _archive_runtime_scope(
    agent_session: AgentSession,
    session: SessionDep,
) -> Iterator[Any]:
    """打开并关闭 Runtime，并隔离外部依赖异常与已知业务错误。

    Args:
        agent_session: 已通过资源归属校验的档案助手会话。
        session: 当前请求的数据库会话，传给 Runtime 的受控工厂。

    Yields:
        已打开的档案助手 Runtime，供消息或历史读取 Service 使用。

    Raises:
        AppError: Runtime 构造、执行后的关闭或未知依赖异常统一映射为 503；
            已知的 AppError 保留原有业务状态码和错误码。
    """
    try:
        runtime_manager = _runtime_for_session(agent_session, session)
        runtime = runtime_manager.__enter__()
    except Exception as exc:
        raise AppError(
            503,
            "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "项目档案助手暂不可用。",
        ) from exc

    try:
        yield runtime
    except Exception as exc:
        try:
            suppressed = runtime_manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as close_exc:
            raise AppError(
                503,
                "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
                "项目档案助手暂不可用。",
            ) from close_exc
        if suppressed:
            return
        if isinstance(exc, AppError):
            # 执行阶段的已知业务错误已有冻结契约，不应被依赖错误覆盖。
            raise
        raise AppError(
            503,
            "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "项目档案助手暂不可用。",
        ) from exc
    else:
        try:
            runtime_manager.__exit__(None, None, None)
        except Exception as exc:
            raise AppError(
                503,
                "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
                "项目档案助手暂不可用。",
            ) from exc


@router.post("/{session_id}/messages", response_model=ArchiveAgentResponse)
def send_archive_agent_message_endpoint(
    project_id: UUID,
    session_id: UUID,
    payload: ArchiveAgentMessageCreate,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ArchiveAgentResponse:
    """在请求体校验和资源归属校验后执行一轮受控 Graph。

    Args:
        project_id: URL 中的项目标识；不能替代已验证的 project_context。
        session_id: 要继续使用的档案助手会话标识。
        payload: 经过 Schema 校验的用户消息。
        project_context: 提供可信的用户、项目和知识库范围。
        session: 当前请求的业务数据库会话。

    Returns:
        本轮生成的助手回复及其脱敏引用/工具观测结果。

    Raises:
        AppError: 会话不存在或不属于当前范围、Runtime 不可用或业务执行失败时，
            由依赖和 Service 层转换为相应的 HTTP 错误契约。
    """
    _ = project_id
    agent_session = find_archive_agent_session(
        session_id=session_id,
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        session=session,
    )
    with _archive_runtime_scope(agent_session, session) as runtime:
        return send_archive_agent_message(
            agent_session=agent_session,
            message=payload.message,
            runtime=runtime,
            session=session,
        )


@router.get("/{session_id}/messages", response_model=list[ArchiveAgentMessageRead])
def read_archive_agent_messages_endpoint(
    project_id: UUID,
    session_id: UUID,
    agent_session: ArchiveAgentSessionDep,
) -> list[ArchiveAgentMessageRead]:
    """返回当前用户可访问会话的完整可见对话轮次。

    Args:
        project_id: URL 中的项目标识；实际范围由已解析的 agent_session 保证。
        session_id: 要读取的档案助手会话标识。
        agent_session: 已由依赖按用户、项目、知识库和会话类型校验的会话。

    Returns:
        按固定投影规则恢复的完整可见消息列表；未完成异常轮次不对外展示。
    """
    _ = project_id, session_id
    return read_archive_agent_messages(agent_session)


@router.get("/{session_id}/tool-calls", response_model=list[AgentToolCallLogRead])
def read_archive_agent_tool_calls_endpoint(
    project_id: UUID,
    session_id: UUID,
    agent_session: ArchiveAgentSessionDep,
    session: SessionDep,
) -> list[AgentToolCallLogRead]:
    """按创建时间返回当前档案会话的脱敏工具日志。

    Args:
        project_id: URL 中的项目标识；实际范围由已解析的 agent_session 保证。
        session_id: 要读取工具日志的档案助手会话标识。
        agent_session: 已通过资源归属和会话类型校验的会话。
        session: 当前请求的业务数据库会话。

    Returns:
        不包含完整正文、Token 或隐藏推理的工具调用记录列表。
    """
    _ = project_id, session_id
    return read_archive_agent_tool_calls(agent_session, session)
