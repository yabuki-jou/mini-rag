"""执行知识库范围内的 Chroma 向量检索和结果转换。"""

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.errors import AppError
from app.services.infrastructure.ai_models import get_embeddings
from app.services.rag.vector_store import get_chunk_collection


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """表示一个已按 Chroma cosine distance 排序的内部检索结果。

    ``score`` 是为保持既有 HTTP 契约而返回的 ``1 - distance``；只在
    collection 使用 cosine 度量、且 BGE 输出已经归一化时可解释为相似度。
    """

    chunk_id: str
    document_id: UUID
    document_name: str
    page: int
    content: str
    score: float


def build_retrieval_filter(
    user_id: UUID,
    kb_id: UUID,
) -> dict[str, list[dict[str, str]]]:
    """构造必须在 Chroma 查询前执行的用户与知识库范围过滤条件。"""
    return {
        "$and": [
            {"user_id": str(user_id)},
            {"kb_id": str(kb_id)},
        ]
    }


def _first_query_values(
    result: dict[str, Any],
    field_name: str,
) -> list[Any]:
    """读取 Chroma 单查询返回的第一组列式字段，并检查结构完整性。"""
    value = result.get(field_name)
    if not isinstance(value, list) or len(value) != 1:
        raise AppError(
            status_code=500,
            code="VECTOR_RESULT_INVALID",
            message="Chroma 检索结果缺少必要字段。",
        )
    first_batch = value[0]
    if not isinstance(first_batch, list):
        raise AppError(
            status_code=500,
            code="VECTOR_RESULT_INVALID",
            message="Chroma 检索结果缺少必要字段。",
        )
    return first_batch


def retrieve_chunks(
    user_id: UUID,
    kb_id: UUID,
    question: str,
) -> list[RetrievedChunk]:
    """在当前已授权知识库范围内检索并返回最多 Top-N 个 Chunk。"""
    retrieval_started_at = perf_counter()
    retrieval_filter = build_retrieval_filter(user_id, kb_id)

    try:
        # 薄客户端没有默认 Embedding；BGE 始终在 FastAPI 进程中生成查询向量。
        query_embedding = get_embeddings().embed_query(question)
        raw_result = get_chunk_collection().query(
            query_embeddings=[query_embedding],
            n_results=settings.retrieval_top_k,
            where=retrieval_filter,
            include=["documents", "metadatas", "distances"],
        )
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "chroma_search_failed user_id=%s kb_id=%s top_k=%s",
            user_id,
            kb_id,
            settings.retrieval_top_k,
        )
        raise AppError(
            status_code=503,
            code="VECTOR_UNAVAILABLE",
            message="无法连接 Chroma 向量服务。",
        ) from exc

    try:
        chunk_ids = _first_query_values(raw_result, "ids")
        documents = _first_query_values(raw_result, "documents")
        metadatas = _first_query_values(raw_result, "metadatas")
        distances = _first_query_values(raw_result, "distances")
    except AppError:
        raise

    if not (
        len(chunk_ids) == len(documents) == len(metadatas) == len(distances)
    ):
        raise AppError(
            status_code=500,
            code="VECTOR_RESULT_INVALID",
            message="Chroma 检索结果列长度不一致。",
        )

    qualified: list[tuple[float, RetrievedChunk]] = []
    candidate_summaries: list[dict[str, str | float]] = []
    for chunk_id, content, metadata, raw_distance in zip(
        chunk_ids,
        documents,
        metadatas,
        distances,
        strict=True,
    ):
        try:
            distance = float(raw_distance)
            if content is None or not isinstance(metadata, dict):
                raise ValueError("missing content or metadata")
            # cosine distance 越小越好；保留旧 API 的 score 字段，但不复用旧阈值。
            score = 1.0 - distance
            retrieved_chunk = RetrievedChunk(
                chunk_id=str(chunk_id),
                document_id=UUID(str(metadata["document_id"])),
                document_name=str(metadata["document_name"]),
                page=int(metadata["page"]),
                content=str(content),
                score=score,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                status_code=500,
                code="VECTOR_RESULT_INVALID",
                message="Chroma 检索结果缺少必要字段。",
            ) from exc

        candidate_summaries.append(
            {"chunk_id": retrieved_chunk.chunk_id, "distance": round(distance, 6)}
        )
        distance_threshold = settings.retrieval_distance_threshold
        if distance_threshold is not None and distance > distance_threshold:
            continue
        qualified.append((distance, retrieved_chunk))

    # 不能依赖服务端返回顺序；明确按 distance 升序，距离相同时按稳定 ID 排序。
    qualified.sort(key=lambda item: (item[0], item[1].chunk_id))
    final_chunks = [chunk for _, chunk in qualified[: settings.retrieval_top_n]]

    logger.info(
        "chroma_retrieval_complete user_id=%s kb_id=%s top_k=%s top_n=%s distance_threshold=%s candidates=%s prompt_chunk_ids=%s duration_ms=%.2f",
        user_id,
        kb_id,
        settings.retrieval_top_k,
        settings.retrieval_top_n,
        settings.retrieval_distance_threshold,
        candidate_summaries,
        [chunk.chunk_id for chunk in final_chunks],
        (perf_counter() - retrieval_started_at) * 1000,
    )
    return final_chunks
