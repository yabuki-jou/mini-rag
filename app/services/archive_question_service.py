"""执行只基于正式档案证据的 DeepSeek 问答。"""

import logging
from time import perf_counter
from uuid import UUID

from sqlmodel import Session

from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.schemas.archive_question import (
    ArchiveAnswerStatus,
    ArchiveQuestionResponse,
)
from app.services.archive_retrieval_service import retrieve_archive_chunks
from app.services.model_service import get_chat_model


logger = logging.getLogger(__name__)


def _build_archive_prompt(question: str, excerpts: list[str]) -> str:
    """构造只允许使用编号证据的最小提示。"""
    evidence = "\n".join(
        f"[S{index}] {excerpt}" for index, excerpt in enumerate(excerpts, start=1)
    )
    return (
        "你是工程项目档案问答助手。只能依据下列正式档案证据回答，"
        "不得补充证据之外的事实；无法确定时明确说证据不足。回答中使用对应的 [S1] 等引用。\n"
        f"正式档案证据：\n{evidence}\n"
        f"问题：{question}\n"
        "请给出简洁回答并保留引用。"
    )


def answer_archive_question(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    question: str,
    session: Session,
) -> ArchiveQuestionResponse:
    """检索正式证据并在有依据时调用 DeepSeek 生成回答。"""
    retrieval = retrieve_archive_chunks(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        query=question,
        top_k=5,
        session=session,
    )
    eval_wrap(
        {
            "has_evidence": bool(retrieval.items),
            "retrieved_item_count": len(retrieval.items),
        },
        purpose="state",
        name="archive_question_decision",
        description="档案问答是否因正式证据存在而进入模型调用分支。",
    )
    if not retrieval.items:
        response = ArchiveQuestionResponse(
            answer_status=ArchiveAnswerStatus.REFUSED_NO_EVIDENCE,
            answer="正式档案中没有足够依据。",
            citations=[],
        )
        eval_wrap(
            response.model_dump(mode="json"),
            purpose="output",
            name="archive_question_response",
            description="档案问答向调用方返回的最终状态、回答和引用。",
        )
        return response

    prompt_evidence = [item.model_dump(mode="json") for item in retrieval.items]
    eval_wrap(
        prompt_evidence,
        purpose="state",
        name="archive_question_prompt_evidence",
        description="本轮进入 DeepSeek 提示的正式档案证据项。",
    )
    prompt = _build_archive_prompt(question, [item.excerpt for item in retrieval.items])
    started_at = perf_counter()
    try:
        response = get_chat_model().invoke(prompt)
        answer = response.content if hasattr(response, "content") else None
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty model response")
    except AppError as exc:
        logger.warning(
            "archive_question_model_unavailable user_id=%s project_id=%s code=%s duration_ms=%.2f",
            user_id,
            project_id,
            exc.code,
            (perf_counter() - started_at) * 1000,
        )
        raise AppError(503, "ARCHIVE_ANSWER_UNAVAILABLE", "档案问答模型暂不可用。") from exc
    except Exception as exc:
        logger.exception(
            "archive_question_model_failed user_id=%s project_id=%s duration_ms=%.2f",
            user_id,
            project_id,
            (perf_counter() - started_at) * 1000,
        )
        raise AppError(503, "ARCHIVE_ANSWER_UNAVAILABLE", "档案问答模型暂不可用。") from exc

    response = ArchiveQuestionResponse(
        answer_status=ArchiveAnswerStatus.ANSWERED,
        answer=answer.strip(),
        citations=retrieval.items,
    )
    eval_wrap(
        response.model_dump(mode="json"),
        purpose="output",
        name="archive_question_response",
        description="档案问答向调用方返回的最终状态、回答和引用。",
    )
    return response
