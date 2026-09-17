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


def _runtime_for_session(
    agent_session: AgentSession,
    session: SessionDep,
):
    """使用五要素已校验会话构造当前请求 Runtime。"""
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
    """打开并关闭 Runtime，将构造或关闭失败投影为冻结 503。"""
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
    """在请求体校验后查找会话并执行一轮受控 Graph。"""
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
    session: SessionDep,
) -> list[ArchiveAgentMessageRead]:
    """返回同一投影规则生成的完整可见轮次。"""
    _ = project_id, session_id
    with _archive_runtime_scope(agent_session, session) as runtime:
        return read_archive_agent_messages(agent_session, runtime)


@router.get("/{session_id}/tool-calls", response_model=list[AgentToolCallLogRead])
def read_archive_agent_tool_calls_endpoint(
    project_id: UUID,
    session_id: UUID,
    agent_session: ArchiveAgentSessionDep,
    session: SessionDep,
) -> list[AgentToolCallLogRead]:
    """按创建时间返回当前档案会话的脱敏工具日志。"""
    _ = project_id, session_id
    return read_archive_agent_tool_calls(agent_session, session)
