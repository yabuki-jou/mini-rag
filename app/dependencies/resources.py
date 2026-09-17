"""定义知识库、文档和聊天会话资源的所有权校验依赖。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.core.errors import AppError
from app.dependencies.auth import CurrentUserDep
from app.dependencies.database import SessionDep
from app.models import AgentSession, AgentType, ChatSession, Document, KnowledgeBase


def get_owned_knowledge_base(
    kb_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> KnowledgeBase:
    """查询知识库，并验证它是否属于当前用户。

    Args:
        kb_id: 请求路径中的知识库 UUID。
        session: 当前请求使用的数据库 Session。
        current_user: 已通过身份校验的当前用户。

    Returns:
        当前用户拥有的知识库记录。

    Raises:
        AppError: 知识库不存在或不属于当前用户。
    """
    # 先确认知识库存在，再单独判断当前用户是否拥有它。
    knowledge_base = session.get(KnowledgeBase, kb_id)
    if knowledge_base is None:
        raise AppError(
            status_code=404,
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="知识库不存在。",
        )

    # 所有权不匹配时禁止继续注入知识库，后续路由无法绕过此检查。
    if knowledge_base.owner_id != current_user.id:
        raise AppError(
            status_code=403,
            code="KNOWLEDGE_BASE_FORBIDDEN",
            message="无权访问该知识库。",
        )
    return knowledge_base


# 先验证当前用户，再把其有权访问的知识库注入路由。
OwnedKnowledgeBaseDep = Annotated[
    KnowledgeBase,
    Depends(get_owned_knowledge_base),
]


def get_document_in_knowledge_base(
    document_id: UUID,
    knowledge_base: OwnedKnowledgeBaseDep,
    session: SessionDep,
) -> Document:
    """查询文档，并验证它是否属于已经授权的知识库。

    Args:
        document_id: 请求路径中的文档 UUID。
        knowledge_base: 已通过当前用户所有权校验的知识库。
        session: 当前请求使用的数据库 Session。

    Returns:
        属于目标知识库的文档记录。

    Raises:
        AppError: 文档不存在或不属于目标知识库。
    """
    # 按主键查询文档，避免扫描整个文档表。
    document = session.get(Document, document_id)

    # 文档不存在时返回 404。
    if document is None:
        raise AppError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="该文档不存在。",
        )

    # 归属不一致时同样返回 404，避免泄露其他知识库的数据是否存在。
    if document.kb_id != knowledge_base.id:
        raise AppError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="该文档不存在。",
        )

    return document


# 将已完成知识库归属校验的 Document 注入后续路由。
OwnedDocumentDep = Annotated[
    Document,
    Depends(get_document_in_knowledge_base),
]


def get_owned_chat_session(
    session_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ChatSession:
    """查询聊天会话，并验证它是否属于当前用户。

    Args:
        session_id: 请求路径中的聊天会话 UUID。
        session: 当前请求使用的数据库 Session。
        current_user: 已通过身份校验的当前用户。

    Returns:
        当前用户拥有的聊天会话记录。

    Raises:
        AppError: 聊天会话不存在或不属于当前用户。
    """
    # 按主键读取会话；不存在时返回稳定的业务错误。
    chat_session = session.get(ChatSession, session_id)
    if chat_session is None:
        raise AppError(
            status_code=404,
            code="CHAT_SESSION_NOT_FOUND",
            message="聊天会话不存在。",
        )

    # 所有权不匹配时禁止将会话注入后续消息接口。
    if chat_session.user_id != current_user.id:
        raise AppError(
            status_code=403,
            code="CHAT_SESSION_FORBIDDEN",
            message="无权访问该聊天会话。",
        )
    return chat_session


# 将已完成当前用户所有权校验的 ChatSession 注入后续路由。
OwnedChatSessionDep = Annotated[
    ChatSession,
    Depends(get_owned_chat_session),
]


def get_owned_agent_session(
    session_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> AgentSession:
    """查询 Agent 会话，并验证它是否属于当前用户。

    Args:
        session_id: 请求路径中的 Agent 会话 UUID。
        session: 当前请求使用的数据库 Session。
        current_user: 已通过身份校验的当前用户。

    Returns:
        当前用户拥有的 Agent 会话记录。

    Raises:
        AppError: Agent 会话不存在或不属于当前用户。
    """
    agent_session = session.get(AgentSession, session_id)
    if (
        agent_session is None
        or agent_session.agent_type != AgentType.POLICY
        or agent_session.project_id is not None
    ):
        raise AppError(404, "AGENT_SESSION_NOT_FOUND", "Agent 会话不存在。")
    if agent_session.user_id != current_user.id:
        raise AppError(403, "AGENT_SESSION_FORBIDDEN", "无权访问该 Agent 会话。")
    return agent_session


# 所有基于会话执行的 Agent 路由都必须先通过此所有权依赖。
OwnedAgentSessionDep = Annotated[
    AgentSession,
    Depends(get_owned_agent_session),
]
