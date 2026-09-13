"""管理聊天会话、历史消息、引用数据和一问一答事务。"""

import json
import logging
from datetime import timedelta
from json import JSONDecodeError
from time import perf_counter
from uuid import UUID

from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ChatMessage,
    ChatSession,
    KnowledgeBase,
    MessageRole,
    User,
    utc_now,
)
from app.schemas import ChatAnswerResponse, SourceRead
from app.services.infrastructure.ai_models import get_chat_model
from app.services.rag.prompting import build_prompt_messages, build_sources
from app.services.rag.retrieval import retrieve_chunks


logger = logging.getLogger(__name__)


def create_chat_session(
    current_user: User,
    kb_id: UUID,
    session: Session,
) -> ChatSession:
    """为当前用户在指定知识库中创建聊天会话。

    Args:
        current_user: 已通过请求头身份校验的当前用户。
        kb_id: 客户端请求绑定的知识库 UUID。
        session: 当前请求使用的数据库 Session。

    Returns:
        已提交并刷新后的聊天会话记录。

    Raises:
        AppError: 知识库不存在、当前用户无权访问或数据库写入失败。
    """
    # 请求体只提供知识库 ID，必须以 PostgreSQL 中的真实记录验证存在性。
    knowledge_base = session.get(KnowledgeBase, kb_id)
    if knowledge_base is None:
        raise AppError(
            status_code=404,
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="知识库不存在。",
        )

    # 会话所属用户必须来自已认证用户，不能由客户端直接指定。
    if knowledge_base.owner_id != current_user.id:
        raise AppError(
            status_code=403,
            code="KNOWLEDGE_BASE_FORBIDDEN",
            message="无权访问该知识库。",
        )

    # 使用已验证的用户和知识库主键建立会话归属关系。
    chat_session = ChatSession(
        user_id=current_user.id,
        kb_id=knowledge_base.id,
    )

    # 数据库提交失败时回滚当前事务，并隐藏底层数据库错误细节。
    try:
        session.add(chat_session)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(
            status_code=500,
            code="CHAT_SESSION_CREATE_FAILED",
            message="聊天会话创建失败。",
        ) from exc

    session.refresh(chat_session)
    return chat_session


def read_recent_messages(
    chat_session: ChatSession,
    session: Session,
) -> list[ChatMessage]:
    """读取会话最近 20 条消息，并按时间从旧到新返回。

    Args:
        chat_session: 已通过当前用户所有权校验的聊天会话。
        session: 当前请求使用的数据库 Session。

    Returns:
        最多 20 条按时间正序排列的聊天消息。
    """
    # 先倒序查询并限制 20 条，确保取得的是最新消息而不是最早消息。
    chat_messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == chat_session.id)
        .order_by(
            ChatMessage.created_at.desc(),
            ChatMessage.role.asc(),
        )
        .limit(20)
    ).all()

    # 倒序查询中，同一时间先放 ASSISTANT、再放 USER；整体反转后即可
    # 恢复为 USER、ASSISTANT，兼容已经存在的同时间历史记录。
    chat_messages.reverse()
    return chat_messages


def serialize_sources(sources: list[SourceRead]) -> str:
    """将结构化引用转换为可存入 PostgreSQL 的 JSON 字符串。

    Args:
        sources: 当前回答实际使用的引用列表。

    Returns:
        保留中文并将 UUID 转成字符串后的 JSON 数组。
    """
    # ``mode="json"`` 会把 UUID 等 Python 类型转换为 JSON 兼容值。
    source_data = [
        source.model_dump(mode="json")
        for source in sources
    ]
    return json.dumps(source_data, ensure_ascii=False)


def deserialize_sources(sources_json: str | None) -> list[SourceRead]:
    """将 PostgreSQL 中的引用 JSON 恢复为经过校验的引用对象。

    Args:
        sources_json: 助手消息保存的引用 JSON；用户消息通常为 ``None``。

    Returns:
        经过 ``SourceRead`` 字段约束校验的引用列表。

    Raises:
        AppError: JSON 损坏、根节点不是数组或引用字段不符合约束。
    """
    if sources_json is None:
        return []

    try:
        # 先验证 JSON 根节点，避免把对象或标量误当成引用列表处理。
        source_data = json.loads(sources_json)
        if not isinstance(source_data, list):
            raise TypeError("引用 JSON 的根节点必须是数组。")

        # 逐条恢复为 SourceRead，重新执行 UUID、页码和分数等字段校验。
        return [
            SourceRead.model_validate(item)
            for item in source_data
        ]
    except (JSONDecodeError, TypeError, ValidationError) as exc:
        raise AppError(
            status_code=500,
            code="CHAT_SOURCE_DATA_INVALID",
            message="聊天消息的引用数据无效。",
        ) from exc


def save_chat_exchange(
    chat_session: ChatSession,
    question: str,
    answer: str,
    sources: list[SourceRead],
    session: Session,
) -> tuple[ChatMessage, ChatMessage]:
    """在一个 PostgreSQL 事务中保存完整的一问一答。

    Args:
        chat_session: 本次问答所属且已通过权限校验的聊天会话。
        question: 用户提交的问题正文。
        answer: DeepSeek 回答或系统预设的拒答文本。
        sources: 实际进入 Prompt 的引用；拒答时应为空列表。
        session: 当前请求使用的数据库 Session。

    Returns:
        依次返回已保存的用户消息和助手消息。

    Raises:
        AppError: 两条消息或会话更新时间提交失败。
    """
    # 为同一轮消息设置明确的先后时间，避免数据库在时间相同时任意排序。
    user_created_at = utc_now()
    assistant_created_at = user_created_at + timedelta(microseconds=1)

    # 用户消息不保存引用；引用只属于使用这些 Chunk 生成的助手回答。
    user_message = ChatMessage(
        session_id=chat_session.id,
        role=MessageRole.USER,
        content=question,
        sources_json=None,
        created_at=user_created_at,
    )
    assistant_message = ChatMessage(
        session_id=chat_session.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        sources_json=serialize_sources(sources),
        created_at=assistant_created_at,
    )

    # 会话更新时间与助手消息一致，表示这一轮问答完整保存的时间。
    chat_session.updated_at = assistant_created_at

    try:
        # 三项修改只提交一次，确保不会只留下问题而缺少回答。
        session.add(user_message)
        session.add(assistant_message)
        session.add(chat_session)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(
            status_code=500,
            code="CHAT_HISTORY_SAVE_FAILED",
            message="聊天记录保存失败。",
        ) from exc

    return user_message, assistant_message


def ask_question(
    chat_session: ChatSession,
    question: str,
    session: Session,
) -> ChatAnswerResponse:
    """根据会话知识库回答问题，并保存完整的一问一答。

    Args:
        chat_session: 已通过当前用户所有权校验的聊天会话。
        question: 已通过请求模型校验的当前问题。
        session: 当前请求使用的数据库 Session。

    Returns:
        包含回答、是否拒答和实际引用来源的响应对象。

    Raises:
        AppError: 检索、模型调用、模型响应校验或历史保存失败。
    """
    question_started_at = perf_counter()

    # 保存当前问题之前先读取历史，避免本轮问题重复进入 Prompt。
    recent_messages = read_recent_messages(chat_session, session)

    # 检索范围完全来自已授权会话，客户端不能在问题中覆盖用户或知识库。
    retrieved_chunks = retrieve_chunks(
        user_id=chat_session.user_id,
        kb_id=chat_session.kb_id,
        question=question,
    )

    # 检索服务无结果时返回空列表；此分支直接拒答，不创建或调用模型。
    if not retrieved_chunks:
        answer = "知识库中没有足够依据。"
        sources: list[SourceRead] = []
        save_chat_exchange(
            chat_session=chat_session,
            question=question,
            answer=answer,
            sources=sources,
            session=session,
        )
        logger.info(
            "chat_rejected user_id=%s kb_id=%s session_id=%s "
            "reason=no_chunk_above_threshold duration_ms=%.2f",
            chat_session.user_id,
            chat_session.kb_id,
            chat_session.id,
            (perf_counter() - question_started_at) * 1000,
        )
        return ChatAnswerResponse(
            answer=answer,
            rejected=True,
            sources=sources,
        )

    # 使用同一份有序 Chunk 构造 Prompt，保证其中的 S1、S2 顺序稳定。
    prompt_messages = build_prompt_messages(
        question=question,
        retrieved_chunks=retrieved_chunks,
        recent_messages=recent_messages,
    )

    llm_started_at = perf_counter()
    try:
        # 客户端本身会被缓存，但只有存在合格 Chunk 时才真正请求 DeepSeek。
        chat_model = get_chat_model()
        model_response = chat_model.invoke(prompt_messages)
    except AppError as exc:
        # 保留配置层已经产生的安全业务异常，例如未配置 API 密钥。
        logger.warning(
            "llm_request_failed user_id=%s kb_id=%s session_id=%s "
            "code=%s duration_ms=%.2f",
            chat_session.user_id,
            chat_session.kb_id,
            chat_session.id,
            exc.code,
            (perf_counter() - llm_started_at) * 1000,
        )
        raise
    except Exception as exc:
        logger.exception(
            "llm_request_failed user_id=%s kb_id=%s session_id=%s "
            "duration_ms=%.2f",
            chat_session.user_id,
            chat_session.kb_id,
            chat_session.id,
            (perf_counter() - llm_started_at) * 1000,
        )
        raise AppError(
            status_code=503,
            code="DEEPSEEK_REQUEST_FAILED",
            message="回答生成失败。",
        ) from exc

    # 当前项目只接受非空字符串回答，不能把列表或空内容直接存入 PostgreSQL。
    if not isinstance(model_response.content, str):
        raise AppError(
            status_code=502,
            code="DEEPSEEK_RESPONSE_INVALID",
            message="回答模型返回了无效内容。",
        )

    answer = model_response.content.strip()
    if not answer:
        raise AppError(
            status_code=502,
            code="DEEPSEEK_RESPONSE_INVALID",
            message="回答模型返回了无效内容。",
        )

    # 引用与 Prompt 使用相同的 Chunk 顺序，因此 [S1] 对应 sources[0]。
    sources = build_sources(retrieved_chunks)

    # 模型成功后再以一个事务保存问题、回答和引用。
    save_chat_exchange(
        chat_session=chat_session,
        question=question,
        answer=answer,
        sources=sources,
        session=session,
    )

    logger.info(
        "chat_answered user_id=%s kb_id=%s session_id=%s "
        "prompt_chunk_ids=%s llm_ms=%.2f total_ms=%.2f",
        chat_session.user_id,
        chat_session.kb_id,
        chat_session.id,
        [chunk.chunk_id for chunk in retrieved_chunks],
        (perf_counter() - llm_started_at) * 1000,
        (perf_counter() - question_started_at) * 1000,
    )

    return ChatAnswerResponse(
        answer=answer,
        rejected=False,
        sources=sources,
    )
