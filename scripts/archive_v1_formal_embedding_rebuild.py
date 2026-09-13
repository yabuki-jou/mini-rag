"""在安全门保护下把确认档案重建到正式 bge-base/768 Collection。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

from chromadb.errors import NotFoundError
from sqlmodel import Session

from app.core.config import settings
from app.db import engine
from app.services.infrastructure.chroma import get_chroma_client
from scripts.archive_v1_d1_embedding_rebuild import (
    rebuild_confirmed_documents,
)


_EXPECTED_DIMENSION = 768
_EXPECTED_CONTEXT_MODE = "evidence_values"
_CANARY_ID = "archive_v1_formal_embedding_rebuild_canary"
_CANARY_DOCUMENT = "formal-embedding-rebuild-canary"


@dataclass(frozen=True, slots=True)
class FormalRebuildSummary:
    """表示正式向量重建产生的聚合结果。"""

    document_count: int
    chunk_count: int
    contextual_chunk_count: int


def validate_formal_collection_name(name: str) -> str:
    """确保脚本只能操作配置中的正式 Collection。"""
    if name != settings.chroma_final_collection:
        raise ValueError("正式重建只能操作配置中的正式 Collection。")
    return name


def validate_formal_settings() -> None:
    """校验正式模型、维度和向量上下文配置。"""
    if settings.embedding_dimension != _EXPECTED_DIMENSION:
        raise RuntimeError("正式重建要求 Embedding 维度为 768。")
    if settings.archive_embedding_context_mode != _EXPECTED_CONTEXT_MODE:
        raise RuntimeError("正式重建要求 evidence_values 向量上下文模式。")
    model_path = settings.embedding_path
    if not model_path.is_dir():
        raise RuntimeError("正式 Embedding 模型目录不存在。")


def _collection_metric(collection: Any) -> str:
    """读取 Collection 的距离度量，缺失配置时返回空标识。"""
    try:
        return str(collection.configuration["hnsw"]["space"])
    except (AttributeError, KeyError, TypeError):
        return ""


def _get_existing_collection(client: Any) -> Any | None:
    """读取正式 Collection；不存在时返回空，不吞连接错误。"""
    try:
        return client.get_collection(name=validate_formal_collection_name(settings.chroma_final_collection))
    except NotFoundError:
        return None


def _create_cosine_collection(client: Any) -> Any:
    """创建并校验正式 cosine Collection。"""
    collection = client.get_or_create_collection(
        name=validate_formal_collection_name(settings.chroma_final_collection),
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )
    if (
        collection.name != settings.chroma_final_collection
        or _collection_metric(collection) != "cosine"
    ):
        raise RuntimeError("正式 Collection 必须使用 cosine 距离。")
    return collection


def prepare_empty_formal_collection(*, client: Any | None = None) -> Any:
    """检查并删除空旧 Collection，再创建空 cosine Collection。"""
    client = client or get_chroma_client()
    existing = _get_existing_collection(client)
    deleted_existing = False
    if existing is not None:
        count = int(existing.count())
        if count != 0:
            raise RuntimeError("正式 Collection 非空，拒绝重建。")
        client.delete_collection(name=settings.chroma_final_collection)
        deleted_existing = True
    try:
        return _create_cosine_collection(client)
    except Exception:
        # 删除空旧集合后若创建失败，必须恢复一个空的正式集合，避免留下不可用状态。
        if deleted_existing:
            _restore_empty_formal_collection(client)
        raise


def _restore_empty_formal_collection(client: Any) -> Any:
    """清理本次正式写入并恢复空 cosine Collection。"""
    try:
        client.delete_collection(name=settings.chroma_final_collection)
    except NotFoundError:
        # Collection 已被清理时继续建立空集合；连接和权限错误必须继续抛出。
        pass
    collection = _create_cosine_collection(client)
    if int(collection.count()) != 0:
        raise RuntimeError("正式重建失败后未能恢复空 Collection。")
    return collection


def _verify_canary_dimension(collection: Any) -> None:
    """写入并删除无业务内容的固定 768 维 canary，验证 Collection 维度。"""
    # 使用固定单位向量避免某些向量索引拒绝零范数，同时不引入业务语义。
    vector = [1.0] + [0.0] * (_EXPECTED_DIMENSION - 1)
    collection.upsert(
        ids=[_CANARY_ID],
        documents=[_CANARY_DOCUMENT],
        embeddings=[vector],
        metadatas=[{"kind": "formal-rebuild-canary"}],
    )
    found = collection.get(ids=[_CANARY_ID], include=[])["ids"]
    if _CANARY_ID not in found:
        raise RuntimeError("正式 Collection canary 写入验证失败。")
    collection.delete(ids=[_CANARY_ID])
    _verify_canary_absent(collection)


def _verify_canary_absent(collection: Any) -> None:
    """确认正式 Collection 中不存在本次重建的 canary。"""
    remaining = collection.get(ids=[_CANARY_ID], include=[])["ids"]
    if _CANARY_ID in remaining:
        raise RuntimeError("正式 Collection canary 清理失败。")


def run_rebuild(*, confirm_empty_rebuild: bool, client: Any | None = None) -> FormalRebuildSummary:
    """执行正式重建，并在任一步骤失败后恢复空 Collection。"""
    if not confirm_empty_rebuild:
        raise RuntimeError("必须显式提供 --confirm-empty-rebuild。")
    validate_formal_settings()
    client = client or get_chroma_client()
    collection = prepare_empty_formal_collection(client=client)
    try:
        _verify_canary_dimension(collection)
        with Session(engine) as session:
            summary = rebuild_confirmed_documents(
                session=session,
                target_collection=collection,
                allow_formal_collection=True,
            )
        _verify_canary_absent(collection)
        if int(collection.count()) != summary.chunk_count:
            raise RuntimeError("正式 Collection 数量与重建汇总不一致。")
        return FormalRebuildSummary(
            document_count=summary.document_count,
            chunk_count=summary.chunk_count,
            contextual_chunk_count=summary.contextual_chunk_count,
        )
    except Exception:
        _restore_empty_formal_collection(client)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    """运行正式重建命令，只输出非敏感聚合结果。"""
    parser = argparse.ArgumentParser(description="重建正式 bge-base/768 档案向量。")
    parser.add_argument(
        "--confirm-empty-rebuild",
        action="store_true",
        help="确认正式 Collection 已为空并允许删除空 Collection 后重建。",
    )
    args = parser.parse_args(argv)
    if not args.confirm_empty_rebuild:
        parser.error("必须显式提供 --confirm-empty-rebuild；未连接任何外部服务。")
    summary = run_rebuild(confirm_empty_rebuild=True)
    print(
        json.dumps(
            {
                "collection": settings.chroma_final_collection,
                "embedding_model": Path(settings.embedding_path).name,
                **asdict(summary),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
