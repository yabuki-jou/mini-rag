"""提供聊天会话创建、知识库问答和历史消息查询接口。"""

from uuid import UUID

from fastapi import APIRouter, status

from app.dependencies import CurrentUserDep, OwnedChatSessionDep, SessionDep
from app.models import ChatSession
from app.schemas import (
    ChatAnswerResponse,
    ChatMessageRead,
    ChatQuestionRequest,
    ChatSessionCreate,
    ChatSessionRead,
)
from app.services.chat_service import (
    ask_question,
    create_chat_session,
    deserialize_sources,
    read_recent_messages,
)

router = APIRouter(
    prefix="/chat-sessions",
    tags=["chat"],
)


@router.post(
    "",
    response_model=ChatSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_chat_session_endpoint(
    payload: ChatSessionCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ChatSession:
    """为当前用户在其知识库中创建聊天会话。

    Args:
        payload: 包含目标知识库 UUID 的请求体。
        current_user: 已通过 Bearer Access Token 身份校验的当前用户。
        session: 当前请求使用的 SQLite Session。

    Returns:
        数据库提交并刷新后的聊天会话记录。

    Raises:
        AppError: 知识库不存在、当前用户无权访问或会话创建失败。
    """
    # 路由只传递经过校验的请求数据，权限检查和数据库事务由服务层负责。
    return create_chat_session(
        current_user=current_user,
        kb_id=payload.kb_id,
        session=session,
    )


@router.post(
    "/{session_id}/messages",
    response_model=ChatAnswerResponse,
)
def ask_question_endpoint(
    session_id: UUID,
    payload: ChatQuestionRequest,
    chat_session: OwnedChatSessionDep,
    session: SessionDep,
) -> ChatAnswerResponse:
    """在当前用户拥有的聊天会话中执行知识库问答。

    Args:
        session_id: 路径中的聊天会话 UUID；同时供所有权依赖使用。
        payload: 包含当前自然语言问题的请求体。
        chat_session: 已通过当前用户所有权校验的聊天会话。
        session: 当前请求使用的 SQLite Session。

    Returns:
        包含回答、拒答状态和实际引用来源的问答响应。

    Raises:
        AppError: 会话无权访问，或检索、模型调用和历史保存失败。
    """
    # session_id 已由 FastAPI 解析，并由 OwnedChatSessionDep 完成查询和权限校验。

    # 路由不参与检索和模型编排，只传递经过校验的会话与问题。
    return ask_question(
        chat_session=chat_session,
        question=payload.question,
        session=session,
    )


@router.get(
    "/{session_id}/messages",
    response_model=list[ChatMessageRead],
)
def read_chat_messages_endpoint(
    session_id: UUID,
    chat_session: OwnedChatSessionDep,
    session: SessionDep,
) -> list[ChatMessageRead]:
    """返回当前用户会话中最近 20 条聊天消息。

    Args:
        session_id: 路径中的聊天会话 UUID；同时供所有权依赖使用。
        chat_session: 已通过当前用户所有权校验的聊天会话。
        session: 当前请求使用的 SQLite Session。

    Returns:
        按时间从旧到新排列，并带有结构化引用的聊天消息列表。

    Raises:
        AppError: 会话无权访问，或数据库中的引用 JSON 无效。
    """
    # 先取得最近 20 条数据库消息；服务层已经将顺序恢复为从旧到新。
    chat_messages = read_recent_messages(
        chat_session=chat_session,
        session=session,
    )

    # 将数据库专用的 sources_json 转换成 API 需要的结构化 sources。
    chat_message_reads: list[ChatMessageRead] = []
    for chat_message in chat_messages:
        chat_message_read = ChatMessageRead(
            id=chat_message.id,
            session_id=chat_message.session_id,
            role=chat_message.role,
            content=chat_message.content,
            sources=deserialize_sources(chat_message.sources_json),
            created_at=chat_message.created_at,
        )
        chat_message_reads.append(chat_message_read)

    return chat_message_reads
