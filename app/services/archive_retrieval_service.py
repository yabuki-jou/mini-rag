"""执行智慧档案正式范围内的 Chroma 证据检索。"""

import logging
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveOperation,
    ArchiveOperationStatus,
    Document,
    EvidenceLocationType,
)
from app.schemas.archive_retrieval import (
    ArchiveRetrievalItemRead,
    ArchiveRetrievalResponse,
)
from app.services.archive_catalog_service import _blocked_document_ids
from app.services.archive_final_chunk_service import get_final_collection
from app.services.model_service import get_embeddings
from app.services.archive_reranker_service import score_archive_candidates


logger = logging.getLogger(__name__)

# bge-small-zh-v1.5 的文档语料向量不加指令；查询侧使用公开推荐的检索前缀，
# 避免问题句与原文片段处于不一致的语义表示空间。
_BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
_ArchiveCandidate = tuple[float, ArchiveRetrievalItemRead]
_RerankedArchiveCandidate = tuple[float, float, ArchiveRetrievalItemRead]


def _query_values(result: dict[str, Any], name: str) -> list[Any]:
    """读取 Chroma 单查询返回的第一组列式值。"""
    value = result.get(name)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], list):
        raise AppError(500, "VECTOR_RESULT_INVALID", "Chroma 检索结果缺少必要字段。")
    return value[0]


def _formal_document_ids(*, user_id: UUID, project_id: UUID, kb_id: UUID, session: Session) -> list[UUID]:
    """从 PostgreSQL 取得当前用户项目的正式且未阻断文档集合。"""
    # 注释 1：PostgreSQL 仍是确认和删除可见性的事实来源；只有通过该业务状态
    # 闸门后才查询 Chroma。
    blocked_ids = _blocked_document_ids(project_id, session)
    rows = session.exec(
        select(Document.id)
        .join(ArchiveDocument, ArchiveDocument.document_id == Document.id)
        .where(
            Document.project_id == project_id,
            Document.kb_id == kb_id,
            ArchiveDocument.status == ArchiveDocumentStatus.CONFIRMED,
        )
        .order_by(Document.id)
    ).all()
    # 该辅助函数有意接收 user_id，避免调用方意外构造出缺少已认证主体的检索范围。
    del user_id
    return [document_id for document_id in rows if document_id not in blocked_ids]


def _build_validated_candidates(
    *,
    chunk_ids: list[Any],
    documents: list[Any],
    metadatas: list[Any],
    distances: list[Any],
    formal_ids: list[UUID],
) -> list[_ArchiveCandidate]:
    """将通过正式范围复核的 Chroma 列式响应投影为可重排候选。"""
    formal_id_set = set(formal_ids)
    candidates: list[_ArchiveCandidate] = []
    # 注释 4：再次校验 Chroma 元数据，而非将向量库作为访问控制事实来源；
    # 这是检索链路的纵深防护。
    for chunk_id, content, metadata, raw_distance in zip(
        chunk_ids, documents, metadatas, distances, strict=True
    ):
        try:
            if not isinstance(metadata, dict) or content is None:
                raise ValueError
            document_id = UUID(str(metadata["document_id"]))
            if document_id not in formal_id_set:
                continue
            distance = float(raw_distance)
            location_type = EvidenceLocationType(str(metadata["location_type"]))
            location_start = int(metadata["location_start"])
            location_end = int(metadata["location_end"])
            if location_start < 1 or location_end < location_start:
                raise ValueError
            item = ArchiveRetrievalItemRead(
                chunk_id=str(chunk_id),
                document_id=document_id,
                filename=str(metadata["filename"]),
                location_type=location_type,
                location_start=location_start,
                location_end=location_end,
                excerpt=str(content),
                # 保留 Chroma 分数以兼容既有 API；P14 的独立阈值使用 reranker_score。
                score=1.0 - distance,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(500, "VECTOR_RESULT_INVALID", "Chroma 检索结果缺少必要字段。") from exc
        candidates.append((distance, item))
    return candidates


def _rerank_candidates(*, query: str, candidates: list[_ArchiveCandidate]) -> list[_RerankedArchiveCandidate]:
    """按本地重排分数筛选并生成可复现的候选顺序。"""
    rerank_scores = score_archive_candidates(
        query=query.strip(),
        contents=[item.excerpt for _, item in candidates],
    )
    rerank_threshold = settings.archive_reranker_score_threshold
    reranked_candidates = [
        (
            score,
            distance,
            item.model_copy(update={"reranker_score": score}),
        )
        for score, (distance, item) in zip(rerank_scores, candidates, strict=True)
        if rerank_threshold is None or score >= rerank_threshold
    ]
    # 重排分数相同时，使用 Chroma distance 和 Chunk ID 作为稳定次级排序键，
    # 使固定集与 API 客户端得到可复现顺序。
    reranked_candidates.sort(key=lambda value: (-value[0], value[1], value[2].chunk_id))
    return reranked_candidates


def retrieve_archive_chunks(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    query: str,
    top_k: int,
    session: Session,
) -> ArchiveRetrievalResponse:
    """检索当前项目正式档案并转换为可追溯证据。"""
    if not query or not query.strip():
        raise AppError(422, "VALIDATION_ERROR", "检索问题不能为空。")
    if top_k < 1 or top_k > 10:
        raise AppError(422, "VALIDATION_ERROR", "检索数量必须在 1 到 10 之间。")

    formal_ids = _formal_document_ids(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        session=session,
    )
    eval_wrap(
        {
            "formal_document_count": len(formal_ids),
            "scope_keys": ["user_id", "project_id", "kb_id", "document_id"],
        },
        purpose="state",
        name="archive_retrieval_scope",
        description="当前项目正式档案检索的服务端范围结构，不记录真实资源标识。",
    )
    if not formal_ids:
        response = ArchiveRetrievalResponse(
            items=[], requested_top_k=top_k, returned_count=0
        )
        eval_wrap(
            response.model_dump(mode="json"),
            purpose="state",
            name="archive_retrieval_result",
            description="正式档案检索实际返回的可追溯证据项。",
        )
        return response

    retrieval_started_at = perf_counter()
    # 注释 2：每个元数据条件都由服务端提供；尤其是文档 ID 列表能防止其他项目的
    # 已确认向量通过宽泛的知识库检索泄露。
    scope_filter = {
        "$and": [
            {"user_id": str(user_id)},
            {"project_id": str(project_id)},
            {"kb_id": str(kb_id)},
            {"document_id": {"$in": [str(document_id) for document_id in formal_ids]}},
        ]
    }
    try:
        # 注释 3：指令只用于查询向量；已存储文档 Chunk 保持可读证据，
        # 不为模型输入而改写。
        query_embedding = get_embeddings().embed_query(
            f"{_BGE_ZH_QUERY_INSTRUCTION}{query.strip()}"
        )
        raw_result = get_final_collection().query(
            query_embeddings=[query_embedding],
            n_results=settings.archive_reranker_candidate_k,
            where=scope_filter,
            include=["documents", "metadatas", "distances"],
        )
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "archive_retrieval_failed user_id=%s project_id=%s top_k=%s",
            user_id,
            project_id,
            top_k,
        )
        raise AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。") from exc

    try:
        chunk_ids = _query_values(raw_result, "ids")
        documents = _query_values(raw_result, "documents")
        metadatas = _query_values(raw_result, "metadatas")
        distances = _query_values(raw_result, "distances")
    except AppError:
        raise
    if not (len(chunk_ids) == len(documents) == len(metadatas) == len(distances)):
        raise AppError(500, "VECTOR_RESULT_INVALID", "Chroma 检索结果列长度不一致。")

    candidates = _build_validated_candidates(
        chunk_ids=chunk_ids,
        documents=documents,
        metadatas=metadatas,
        distances=distances,
        formal_ids=formal_ids,
    )
    rerank_threshold = settings.archive_reranker_score_threshold
    reranked_candidates = _rerank_candidates(query=query, candidates=candidates)
    items = [item for _, _, item in reranked_candidates[:top_k]]
    logger.info(
        "archive_retrieval_complete user_id=%s project_id=%s top_k=%s formal_documents=%s returned_count=%s rerank_threshold=%s duration_ms=%.2f",
        user_id,
        project_id,
        top_k,
        len(formal_ids),
        len(items),
        rerank_threshold,
        (perf_counter() - retrieval_started_at) * 1000,
    )
    response = ArchiveRetrievalResponse(
        items=items,
        requested_top_k=top_k,
        returned_count=len(items),
    )
    eval_wrap(
        response.model_dump(mode="json"),
        purpose="state",
        name="archive_retrieval_result",
        description="正式档案检索实际返回的可追溯证据项。",
    )
    return response
