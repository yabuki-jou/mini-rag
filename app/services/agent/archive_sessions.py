"""提供项目档案助手会话创建和五要素范围查找。"""

from uuid import UUID

from sqlmodel import Session, select

from app.core.errors import AppError
from app.dependencies.project_context import ProjectContext
from app.models import AgentSession, AgentType, utc_now


def create_archive_agent_session(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    session: Session,
) -> AgentSession:
    """只使用已授权项目上下文创建 ARCHIVE 会话。

    Args:
        user_id: 已验证项目所有者的用户 ID。
        project_id: 已验证的项目 ID。
        kb_id: 项目在服务端绑定的知识库 ID。
        session: 当前请求使用的业务数据库会话。

    Returns:
        已提交且创建、更新时间相同的档案助手会话。

    Raises:
        AppError: PostgreSQL 会话保存失败。
    """
    now = utc_now()
    agent_session = AgentSession(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        agent_type=AgentType.ARCHIVE,
        created_at=now,
        updated_at=now,
    )
    try:
        session.add(agent_session)
        session.commit()
        session.refresh(agent_session)
    except Exception as exc:
        session.rollback()
        raise AppError(
            503,
            "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "项目档案助手暂不可用。",
        ) from exc
    return agent_session


def find_archive_agent_session(
    *,
    session_id: UUID,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    session: Session,
) -> AgentSession:
    """按会话、用户、项目、知识库和 ARCHIVE 类型整体查找。

    Args:
        session_id: 路径中的档案助手会话 ID。
        user_id: 已验证项目所有者的用户 ID。
        project_id: 已验证的项目 ID。
        kb_id: 项目在服务端绑定的知识库 ID。
        session: 当前请求使用的业务数据库会话。

    Returns:
        与全部可信范围字段匹配的 ARCHIVE 会话。

    Raises:
        AppError: 任一范围字段不匹配或会话不存在。
    """
    agent_session = session.exec(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.user_id == user_id,
            AgentSession.project_id == project_id,
            AgentSession.kb_id == kb_id,
            AgentSession.agent_type == AgentType.ARCHIVE,
        )
    ).first()
    if agent_session is None:
        raise AppError(
            404,
            "ARCHIVE_AGENT_SESSION_NOT_FOUND",
            "项目档案助手会话不存在。",
        )
    return agent_session


def find_latest_archive_agent_session(
    *,
    project_context: ProjectContext,
    session: Session,
) -> AgentSession | None:
    """在已验证项目范围内按稳定顺序查找最近的 ARCHIVE 会话。

    Args:
        project_context: 已验证的用户、项目和知识库范围。
        session: 当前请求使用的业务数据库会话。

    Returns:
        当前项目最近的档案助手会话；没有会话时返回 ``None``。

    Raises:
        AppError: PostgreSQL 查询失败。
    """
    try:
        return session.exec(
            select(AgentSession)
            .where(
                AgentSession.user_id == project_context.user_id,
                AgentSession.project_id == project_context.project_id,
                AgentSession.kb_id == project_context.kb_id,
                AgentSession.agent_type == AgentType.ARCHIVE,
            )
            .order_by(
                AgentSession.updated_at.desc(),
                AgentSession.created_at.desc(),
                AgentSession.id.desc(),
            )
            .limit(1)
        ).first()
    except Exception as exc:
        raise AppError(
            503,
            "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "项目档案助手暂不可用。",
        ) from exc
