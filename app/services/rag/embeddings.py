"""为切分后的文本块批量生成向量，并保留 Chunk 业务字段。"""

import logging
from dataclasses import dataclass
from uuid import UUID

from app.core.config import settings
from app.core.errors import AppError
from app.services.rag.chunks import TextChunk, build_chunk_id
from app.services.infrastructure.ai_models import get_embeddings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddedChunk:
    """表示已经生成稳定 ID 和向量的文本块。

    Attributes:
        chunk_id: 根据文档身份、位置和正文生成的稳定标识。
        page: Chunk 所属的原始页码。
        start_index: Chunk 在当前页正文中的起始字符位置。
        chunk_index: Chunk 在整个文档中的顺序。
        content: Chunk 正文。
        embedding: Chunk 正文对应的浮点向量。
    """

    chunk_id: str
    page: int
    start_index: int
    chunk_index: int
    content: str
    embedding: list[float]


def embed_chunks(
    document_id: UUID,
    text_chunks: list[TextChunk],
) -> list[EmbeddedChunk]:
    """批量生成 Chunk 向量，并组合成统一的向量化结果。

    Args:
        document_id: 当前处理文档的 UUID。
        text_chunks: 按文档顺序排列的文本块。

    Returns:
        与输入 Chunk 顺序一致的向量化结果。

    Raises:
        AppError: 模型调用失败，或返回的向量数量、维度不正确。
    """
    # 没有待处理 Chunk 时直接返回，避免无意义地加载模型。
    if not text_chunks:
        return []

    # 按 Chunk 原始顺序提取正文，保证向量与 Chunk 可以按位置配对。
    contents: list[str] = [text_chunk.content for text_chunk in text_chunks]

    # 加载模型并批量生成向量，将底层模型异常转换为统一业务错误。
    try:
        # 获取应用缓存的本地 BGE Embedding 模型。
        emb = get_embeddings()

        # 一次批量生成全部正文向量，避免逐条调用模型。
        vectors = emb.embed_documents(contents)

    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "embedding_failed document_id=%s chunks=%s",
            document_id,
            len(text_chunks),
        )
        raise AppError(
            status_code=503,
            code="EMBEDDING_FAILED",
            message="Chunk 向量生成失败。",
        ) from exc

    # 校验向量数量，防止 Chunk 与向量发生错位。
    if len(contents) != len(vectors):
        raise AppError(
            status_code=500,
            code="EMBEDDING_RESULT_INVALID",
            message="Chunk 向量生成失败。",
        )

    # 校验每个向量的维度是否与 Chroma Collection 已冻结的模型维度一致。
    if any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise AppError(
            status_code=500,
            code="EMBEDDING_RESULT_INVALID",
            message="Chunk 向量生成失败。",
        )

    # 按输入顺序组合 Chunk、稳定 ID 和向量，生成最终向量化结果。
    embedded_chunks: list[EmbeddedChunk] = []
    for chunk, vector in zip(text_chunks, vectors, strict=True):
        embedded_chunk: EmbeddedChunk = EmbeddedChunk(
            chunk_id=build_chunk_id(document_id, chunk),
            page=chunk.page,
            start_index=chunk.start_index,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            embedding=vector,
        )
        embedded_chunks.append(embedded_chunk)

    return embedded_chunks
