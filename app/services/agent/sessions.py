"""提供 Agent 会话创建和知识库授权校验。"""

from uuid import UUID

from sqlmodel import Session

from app.core.errors import AppError
from app.models import AgentSession, KnowledgeBase, User


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
