"""为项目档案助手端点注入已完成五要素校验的会话。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.dependencies.database import SessionDep
from app.dependencies.project_context import ProjectContextDep
from app.models import AgentSession
from app.services.agent.archive_sessions import find_archive_agent_session


def get_archive_agent_session(
    session_id: UUID,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> AgentSession:
    """在可信项目上下文内查找 ARCHIVE 会话。

    Args:
        session_id: 路径中的档案助手会话 ID。
        project_context: 已验证的用户、项目和知识库范围。
        session: 当前请求使用的业务数据库会话。

    Returns:
        与全部范围字段匹配的档案助手会话。
    """
    return find_archive_agent_session(
        session_id=session_id,
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        session=session,
    )


ArchiveAgentSessionDep = Annotated[
    AgentSession,
    Depends(get_archive_agent_session),
]
