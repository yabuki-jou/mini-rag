"""管理旧制度知识库 Collection、向量写入和精确删除。"""

import logging
from functools import lru_cache
from hashlib import sha256
from time import perf_counter
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.errors import AppError
from app.services.infrastructure import chroma
from app.services.rag.embeddings import EmbeddedChunk

logger = logging.getLogger(__name__)

@lru_cache
def get_chunk_collection() -> Any:
    """获取既有制度检索使用的 cosine Collection，并校验距离度量。"""
    try:
        # 本应用始终自行提供 BGE 向量，不能让 Chroma 隐式下载默认模型。
        collection = chroma.get_chroma_client().get_or_create_collection(
            name=settings.chroma_collection,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )
        configuration = collection.configuration
        metric = configuration["hnsw"]["space"]
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "chroma_collection_open_failed collection=%s",
            settings.chroma_collection,
        )
        raise AppError(
            status_code=503,
            code="VECTOR_UNAVAILABLE",
            message="无法连接 Chroma 向量服务。",
        ) from exc

    # 集合已存在时 Chroma 会忽略 creation configuration；必须主动拒绝错误度量。
    if metric != "cosine":
        logger.error(
            "chroma_collection_metric_invalid collection=%s metric=%s",
            settings.chroma_collection,
            metric,
        )
        raise AppError(
            status_code=500,
            code="VECTOR_COLLECTION_CONFIG_INVALID",
            message="Chroma Collection 距离度量配置错误。",
        )
    return collection


def ensure_chunk_collection() -> str:
    """确保既有制度检索的 Chroma Collection 可用并返回其名称。"""
    return str(get_chunk_collection().name)

def build_document_filter(
    user_id: UUID,
    kb_id: UUID,
    document_id: UUID,
) -> dict[str, list[dict[str, str]]]:
    """构造只由服务端身份生成的精确文档范围过滤条件。"""
    return {
        "$and": [
            {"user_id": str(user_id)},
            {"kb_id": str(kb_id)},
            {"document_id": str(document_id)},
        ]
    }


def insert_chunks(
    user_id: UUID,
    kb_id: UUID,
    document_id: UUID,
    document_name: str,
    embedded_chunks: list[EmbeddedChunk],
) -> int:
    """将已生成 BGE 向量的 Chunk 与隔离、引用元数据写入 Chroma。

    ``upsert`` 以稳定 ``chunk_id`` 覆盖同一 Chunk，因而网络重试不会产生
    重复向量；调用方仍会在重新解析前显式删除整篇文档的旧 Chunk。
    """
    if not embedded_chunks:
        return 0

    ids = [chunk.chunk_id for chunk in embedded_chunks]
    documents = [chunk.content for chunk in embedded_chunks]
    embeddings = [chunk.embedding for chunk in embedded_chunks]
    metadatas = [
        {
            "user_id": str(user_id),
            "kb_id": str(kb_id),
            "document_id": str(document_id),
            "document_name": document_name,
            "page": chunk.page,
            "start_index": chunk.start_index,
            "chunk_index": chunk.chunk_index,
            "content_hash": sha256(chunk.content.encode("utf-8")).hexdigest(),
        }
        for chunk in embedded_chunks
    ]

    try:
        get_chunk_collection().upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "chroma_insert_failed user_id=%s kb_id=%s document_id=%s",
            user_id,
            kb_id,
            document_id,
        )
        raise AppError(
            status_code=503,
            code="VECTOR_UNAVAILABLE",
            message="无法连接 Chroma 向量服务。",
        ) from exc

    return len(embedded_chunks)


def delete_document_chunks(
    user_id: UUID,
    kb_id: UUID,
    document_id: UUID,
) -> int:
    """按服务端用户、知识库和文档范围删除全部旧 Chroma Chunk。"""
    delete_filter = build_document_filter(user_id, kb_id, document_id)
    delete_started_at = perf_counter()

    try:
        collection = get_chunk_collection()
        # delete 不返回删除数量；先仅读取 ID，既记录真实数量，也不读取正文。
        existing = collection.get(where=delete_filter, include=[])
        collection.delete(where=delete_filter)
        deleted_count = len(existing["ids"])
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "chroma_document_delete_failed user_id=%s kb_id=%s document_id=%s duration_ms=%.2f",
            user_id,
            kb_id,
            document_id,
            (perf_counter() - delete_started_at) * 1000,
        )
        raise AppError(
            status_code=503,
            code="VECTOR_UNAVAILABLE",
            message="无法连接 Chroma 向量服务。",
        ) from exc

    logger.info(
        "chroma_document_deleted user_id=%s kb_id=%s document_id=%s deleted_chunks=%s duration_ms=%.2f",
        user_id,
        kb_id,
        document_id,
        deleted_count,
        (perf_counter() - delete_started_at) * 1000,
    )
    return deleted_count