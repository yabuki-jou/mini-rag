"""构建智慧档案 Final Chunk，并封装独立 Chroma Collection 写入契约。"""

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
import json
from functools import lru_cache
from hashlib import sha256
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.errors import AppError
from app.models import EvidenceLocationType, ParsedSnapshot
from app.services.infrastructure import chroma
from app.services.infrastructure.ai_models import get_embeddings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ArchiveFinalChunk:
    """表示由当前解析快照规范化得到的、尚未向量化的 Final Chunk。

    Attributes:
        chunk_id: 由文档、定位和正文内容计算的稳定 Chunk 身份。
        content: 归一化后的原文片段。
        location_type: 原文定位类型。
        location_start: 定位范围起点。
        location_end: 定位范围终点。
        normalized_anchor: 可选的定位锚点文本。
        snapshot_hash: 生成该 Chunk 的解析快照哈希。
        parser_version: 生成该 Chunk 的解析器版本。
    """

    chunk_id: str
    content: str
    location_type: str
    location_start: int
    location_end: int
    normalized_anchor: str | None
    snapshot_hash: str
    parser_version: str


@dataclass(frozen=True, slots=True)
class ArchiveEmbeddedChunk(ArchiveFinalChunk):
    """表示已经生成向量、可写入 Final Collection 的 Chunk。

    Attributes:
        embedding: 与当前配置维度一致的向量值。
    """

    embedding: list[float]


def _invalid_snapshot() -> AppError:
    """构造不暴露快照路径和原文的稳定错误。"""
    return AppError(500, "FINAL_SNAPSHOT_INVALID", "当前解析快照无法生成 Final Chunk。")


def _read_snapshot_payload(snapshot: ParsedSnapshot) -> dict:
    """读取并检查快照 JSON 的顶层结构。

    Args:
        snapshot: 待读取并校验的解析快照记录。
    """
    # 数据库记录和快照文件是两份存储事实；调用方核对文档归属后，这里继续核对
    # 哈希、解析版本和片段容器，避免用被替换的 JSON 生成正式向量。
    try:
        payload = json.loads(
            Path(snapshot.snapshot_storage_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid_snapshot() from exc
    if not isinstance(payload, dict):
        raise _invalid_snapshot()
    if payload.get("snapshot_hash") != snapshot.snapshot_hash:
        raise _invalid_snapshot()
    if payload.get("parser_version") != snapshot.parser_version:
        raise _invalid_snapshot()
    if not isinstance(payload.get("fragments"), list):
        raise _invalid_snapshot()
    return payload


def build_final_chunks(
    *,
    document_id: UUID,
    snapshot: ParsedSnapshot,
) -> tuple[ArchiveFinalChunk, ...]:
    """从当前解析快照构建稳定、可追溯的 Final Chunk。

    Args:
        document_id: 该快照所属的文档身份。
        snapshot: 已从数据库读取的当前解析快照记录。

    Returns:
        按快照片段顺序排列、尚未生成向量的 Final Chunk。

    Raises:
        AppError: 快照所属文档、内容结构或定位范围不符合契约。
    """
    if snapshot.document_id != document_id:
        raise _invalid_snapshot()

    payload = _read_snapshot_payload(snapshot)
    if payload.get("document_id") != str(document_id):
        raise _invalid_snapshot()
    fragments = payload["fragments"]
    if len(fragments) != snapshot.fragment_count or not fragments:
        raise _invalid_snapshot()

    chunks: list[ArchiveFinalChunk] = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise _invalid_snapshot()
        try:
            location_type = EvidenceLocationType(fragment["location_type"])
            location_start = int(fragment["location_start"])
            location_end = int(fragment["location_end"])
            content = str(fragment["content"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise _invalid_snapshot() from exc
        if not content or location_start < 1 or location_end < location_start:
            raise _invalid_snapshot()
        if (
            location_type != EvidenceLocationType.TEXT_LINE_RANGE
            and location_end != location_start
        ):
            raise _invalid_snapshot()

        # Chunk 身份绑定文档、定位和归一化正文，而不是绑定临时数组下标；重跑解析时
        # 未变化的片段可以被 Chroma 幂等 upsert，snapshot_hash 则留在元数据中追踪版本。
        identity = (
            f"{document_id}|{location_type.value}|{location_start}|"
            f"{location_end}|{content}"
        )
        chunks.append(
            ArchiveFinalChunk(
                chunk_id=sha256(identity.encode("utf-8")).hexdigest(),
                content=content,
                location_type=location_type.value,
                location_start=location_start,
                location_end=location_end,
                normalized_anchor=(
                    str(fragment["anchor_text"]).strip()
                    if fragment.get("anchor_text")
                    else None
                ),
                snapshot_hash=snapshot.snapshot_hash,
                parser_version=snapshot.parser_version,
            )
        )
    return tuple(chunks)


def embed_final_chunks(
    *,
    document_id: UUID,
    chunks: Sequence[ArchiveFinalChunk],
    embedding_context: str = "",
    embedding_contexts: Mapping[str, str] | None = None,
) -> list[ArchiveEmbeddedChunk]:
    """使用确认字段上下文和原文片段生成 Final Chunk 向量。

    Args:
        document_id: 用于日志和错误定位的文档身份。
        chunks: 当前解析快照构建出的原文 Chunk。
        embedding_context: 应用于全部 Chunk 的已确认字段上下文。
        embedding_contexts: 按 Chunk 身份提供的已确认字段上下文。

    Returns:
        保留原 Chunk 身份和定位元数据、并附加向量的 Chunk 列表。

    Raises:
        AppError: Embedding 服务不可用或返回维度不符合配置。
        ValueError: 同时提供全局和按 Chunk 的上下文。
    """
    if not chunks:
        return []
    normalized_context = embedding_context.strip()
    if normalized_context and embedding_contexts:
        raise ValueError("全局与按 Chunk 的向量上下文不能同时使用。")
    normalized_contexts = {
        chunk_id: context.strip()
        for chunk_id, context in (embedding_contexts or {}).items()
        if context.strip()
    }
    embedding_inputs = [
        (
            f"{context}\n原文片段：{chunk.content}"
            if (context := normalized_contexts.get(chunk.chunk_id, normalized_context))
            else chunk.content
        )
        for chunk in chunks
    ]
    try:
        vectors = get_embeddings().embed_documents(embedding_inputs)
    except AppError:
        raise
    except Exception as exc:
        logger.exception("archive_final_embedding_failed document_id=%s", document_id)
        raise AppError(503, "EMBEDDING_FAILED", "Final Chunk 向量生成失败。") from exc
    if len(vectors) != len(chunks) or any(
        len(vector) != settings.embedding_dimension for vector in vectors
    ):
        raise AppError(500, "EMBEDDING_RESULT_INVALID", "Final Chunk 向量生成失败。")
    return [
        ArchiveEmbeddedChunk(
            chunk_id=chunk.chunk_id,
            content=chunk.content,
            location_type=chunk.location_type,
            location_start=chunk.location_start,
            location_end=chunk.location_end,
            normalized_anchor=chunk.normalized_anchor,
            snapshot_hash=chunk.snapshot_hash,
            parser_version=chunk.parser_version,
            embedding=vector,
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


@lru_cache
def get_final_collection():
    """获取与旧制度检索 Collection 分离的 archive Final Collection。"""
    try:
        collection = chroma.get_chroma_client().get_or_create_collection(
            name=settings.chroma_final_collection,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )
        metric = collection.configuration["hnsw"]["space"]
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "archive_final_collection_open_failed collection=%s",
            settings.chroma_final_collection,
        )
        raise AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。") from exc
    # 距离度量是检索分数语义的一部分；发现已有 Collection 配置不一致时必须失败，
    # 不能静默复用并让后续排序与既有基线失去可比性。
    if metric != "cosine":
        raise AppError(
            500,
            "VECTOR_COLLECTION_CONFIG_INVALID",
            "Chroma Collection 距离度量配置错误。",
        )
    return collection


def ensure_final_collection() -> str:
    """幂等准备独立 Final Collection 并返回其名称。"""
    return str(get_final_collection().name)


def insert_final_chunks(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    document_id: UUID,
    filename: str,
    chunks: list[ArchiveEmbeddedChunk],
    collection: Any | None = None,
) -> int:
    """将带服务端范围和快照元数据的 Chunk 写入指定 Final Collection。

    Args:
        user_id: 服务端验证的项目所有者身份。
        project_id: 服务端验证的项目身份。
        kb_id: 项目绑定的知识库身份。
        document_id: 当前档案文档身份。
        filename: 用于公开检索引用的文件名。
        chunks: 已完成向量化的 Final Chunk。
        collection: 可选的测试或调用方注入 Collection。

    Returns:
        实际提交的 Chunk 数量。

    Raises:
        AppError: 向量无效、Collection 不可用或写入失败。
    """
    if not chunks:
        return 0
    if any(not chunk.embedding for chunk in chunks):
        raise AppError(500, "FINAL_CHUNK_EMBEDDING_INVALID", "Final Chunk 向量无效。")

    try:
        target_collection = collection if collection is not None else get_final_collection()
        # 归属字段随每个向量一起写入，删除和检索都必须带完整范围，避免同名文档或
        # 其他用户的向量被跨项目误读、误删。
        target_collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            embeddings=[chunk.embedding for chunk in chunks],
            metadatas=[
                {
                    "user_id": str(user_id),
                    "project_id": str(project_id),
                    "kb_id": str(kb_id),
                    "document_id": str(document_id),
                    "filename": filename,
                    "snapshot_hash": chunk.snapshot_hash,
                    "parser_version": chunk.parser_version,
                    "location_type": chunk.location_type,
                    "location_start": chunk.location_start,
                    "location_end": chunk.location_end,
                    "excerpt_anchor": chunk.normalized_anchor or "",
                }
                for chunk in chunks
            ],
        )
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "archive_final_chunk_insert_failed user_id=%s project_id=%s document_id=%s",
            user_id,
            project_id,
            document_id,
        )
        raise AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。") from exc
    return len(chunks)


def delete_final_chunks(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    document_id: UUID,
) -> int:
    """按完整服务端归属范围删除一份档案的 Final Chunk。

    Args:
        user_id: 服务端验证的项目所有者身份。
        project_id: 服务端验证的项目身份。
        kb_id: 项目绑定的知识库身份。
        document_id: 要清理的档案文档身份。

    Returns:
        删除前匹配到的向量数量；已清理的目标可安全重试并返回零。

    Raises:
        AppError: Collection 不可用或删除失败。
    """
    delete_filter = {
        "$and": [
            {"user_id": str(user_id)},
            {"project_id": str(project_id)},
            {"kb_id": str(kb_id)},
            {"document_id": str(document_id)},
        ]
    }
    # 四个范围条件必须同时存在；document_id 单独并不能证明调用方拥有该向量。
    try:
        collection = get_final_collection()
        existing = collection.get(where=delete_filter, include=[])
        collection.delete(where=delete_filter)
        return len(existing["ids"])
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "archive_final_chunk_delete_failed user_id=%s project_id=%s kb_id=%s document_id=%s",
            user_id,
            project_id,
            kb_id,
            document_id,
        )
        raise AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。") from exc
