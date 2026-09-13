"""安全重建旧制度 Collection 的冻结向量维度。

当前智慧档案与旧制度 RAG 共用 ``settings.embedding_dimension=768`` 和同一个
本地 BGE Embedding 实例，但旧制度 Collection 是历史 512 维 Collection，导致
制度 Agent 查询被 Chroma 拒绝并映射为 HTTP 503。

本脚本只处理空的旧制度 Collection，采用"原名空库重建"将冻结维度从 512
安全切换到当前配置的 768。任何非空 Collection 或 PostgreSQL 待重建文档
都会阻断重建，避免误删真实向量。

用法::

    # 只读预检（默认），不修改任何数据
    python scripts/policy_collection_embedding_rebuild.py

    # 显式重建：要求 API/写入入口已停止
    python scripts/policy_collection_embedding_rebuild.py --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.models import Document, DocumentStatus
from app.services.infrastructure import chroma
from app.services.infrastructure.ai_models import get_embeddings
from app.services.rag.vector_store import get_chunk_collection
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

# 本脚本只允许操作旧制度 Collection；禁止触碰智慧档案正式/实验 Collection。
_FORBIDDEN_COLLECTION_SUFFIXES = ("archive", "v2", "d1", "base", "large", "m3")


class RebuildError(Exception):
    """脚本层面的可预期失败，用于返回非零退出码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RebuildResult:
    """脚本执行结果的稳定摘要。"""

    phase: str
    collection_name: str
    configured_dimension: int
    actual_dimension: int | None
    collection_count: int | None
    postgres_ready_count: int | None
    postgres_chunk_count: int | None
    status: str
    detail: str = ""


@dataclass
class RebuildContext:
    """脚本执行中可注入的依赖，便于测试 mock。"""

    chroma_client: Any = field(default=None)
    embedding_fn: Any = field(default=None)
    db_session: Any = field(default=None)


def _get_chroma_client() -> Any:
    """获取 Chroma HTTP 客户端；测试可通过 monkeypatch 替换。"""
    return chroma.get_chroma_client()


def _get_embedding_dimension() -> int:
    """通过实际模型探针确认当前 Embedding 输出维度。"""
    embeddings = get_embeddings()
    probe = embeddings.embed_documents(["rebuild dimension probe"])
    if not probe or not probe[0]:
        raise RebuildError(
            "EMBEDDING_PROBE_EMPTY", "Embedding 模型探针返回空向量。"
        )
    return len(probe[0])


def _validate_target_collection_name(name: str) -> None:
    """目标 Collection 名必须精确等于旧制度配置名。"""
    if name != settings.chroma_collection:
        raise RebuildError(
            "TARGET_COLLECTION_MISMATCH",
            f"目标 Collection 必须为 {settings.chroma_collection!r}，收到 {name!r}。",
        )
    if name == settings.chroma_final_collection:
        raise RebuildError(
            "ARCHIVE_COLLECTION_FORBIDDEN",
            "禁止操作智慧档案正式 Collection。",
        )
    lowered = name.lower()
    for suffix in _FORBIDDEN_COLLECTION_SUFFIXES:
        if lowered.endswith(suffix):
            raise RebuildError(
                "ARCHIVE_OR_EXPERIMENT_COLLECTION_FORBIDDEN",
                f"禁止操作档案/实验 Collection（后缀 {suffix!r}）。",
            )


def _query_postgres_pending_documents(session: Session) -> tuple[int, int]:
    """聚合查询旧制度待重建文档数，只返回计数。"""
    ready_count = session.exec(
        select(Document).where(Document.status == DocumentStatus.READY)
    ).all()
    chunked_count = session.exec(
        select(Document).where(Document.chunk_count > 0)
    ).all()
    return len(ready_count), len(chunked_count)


def _probe_collection_dimension(collection: Any, dimension: int) -> bool:
    """用指定维度的零向量探测 Collection 是否接受该维度。"""
    probe = [0.0] * dimension
    try:
        collection.query(query_embeddings=[probe], n_results=1)
        return True
    except Exception:
        return False


def run_preflight() -> RebuildResult:
    """执行只读预检，不修改任何数据。"""
    target_name = settings.chroma_collection
    _validate_target_collection_name(target_name)

    configured_dimension = settings.embedding_dimension
    if configured_dimension != 768:
        raise RebuildError(
            "CONFIG_DIMENSION_INVALID",
            f"当前配置维度必须为 768，收到 {configured_dimension}。",
        )

    actual_dimension = _get_embedding_dimension()
    if actual_dimension != 768:
        raise RebuildError(
            "MODEL_DIMENSION_INVALID",
            f"实际模型输出维度必须为 768，收到 {actual_dimension}。",
        )

    client = _get_chroma_client()
    collection = client.get_or_create_collection(
        name=target_name,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )
    count = collection.count()

    if count != 0:
        raise RebuildError(
            "COLLECTION_NOT_EMPTY",
            f"Collection 条目数必须为 0，收到 {count}。",
        )

    metric = collection.configuration["hnsw"]["space"]
    if metric != "cosine":
        raise RebuildError(
            "COLLECTION_METRIC_INVALID",
            f"Collection 距离度量必须为 cosine，收到 {metric!r}。",
        )

    session = Session(bind=__import__("app.db", fromlist=["engine"]).engine)
    try:
        ready_count, chunk_count = _query_postgres_pending_documents(session)
    finally:
        session.close()

    if ready_count != 0 or chunk_count != 0:
        raise RebuildError(
            "POSTGRES_PENDING_DOCUMENTS",
            f"PostgreSQL 存在待重建文档：READY={ready_count}, chunk_count>0={chunk_count}。",
        )

    accepts_512 = _probe_collection_dimension(collection, 512)
    accepts_768 = _probe_collection_dimension(collection, 768)

    if accepts_768 and not accepts_512:
        return RebuildResult(
            phase="preflight",
            collection_name=target_name,
            configured_dimension=configured_dimension,
            actual_dimension=actual_dimension,
            collection_count=count,
            postgres_ready_count=ready_count,
            postgres_chunk_count=chunk_count,
            status="already_768",
            detail="Collection 已是 768 维，无需重建。",
        )

    if not accepts_512 or accepts_768:
        raise RebuildError(
            "COLLECTION_DIMENSION_UNEXPECTED",
            f"Collection 维度现状异常：512_accepts={accepts_512}, 768_accepts={accepts_768}。",
        )

    return RebuildResult(
        phase="preflight",
        collection_name=target_name,
        configured_dimension=configured_dimension,
        actual_dimension=actual_dimension,
        collection_count=count,
        postgres_ready_count=ready_count,
        postgres_chunk_count=chunk_count,
        status="rebuild_required",
        detail="Collection 为 512 维空库，可安全重建。",
    )


def _write_canary(collection: Any, dimension: int) -> tuple[str, dict[str, str]]:
    """写入虚构 canary 并返回其 chunk_id 和三字段身份。"""
    canary_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    kb_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    embedding = [0.0] * dimension
    embedding[0] = 1.0
    metadata = {
        "user_id": user_id,
        "kb_id": kb_id,
        "document_id": document_id,
        "document_name": "rebuild-canary.txt",
        "page": 1,
        "start_index": 0,
        "chunk_index": 0,
        "content_hash": "canary",
    }
    collection.upsert(
        ids=[canary_id],
        documents=["rebuild canary"],
        embeddings=[embedding],
        metadatas=[metadata],
    )
    return canary_id, {
        "user_id": user_id,
        "kb_id": kb_id,
        "document_id": document_id,
    }


def _verify_canary(
    collection: Any, identity: dict[str, str], dimension: int
) -> None:
    """用三字段范围过滤查询 canary，验证范围过滤和精确命中。"""
    where = {
        "$and": [
            {"user_id": identity["user_id"]},
            {"kb_id": identity["kb_id"]},
            {"document_id": identity["document_id"]},
        ]
    }
    probe = [0.0] * dimension
    probe[0] = 1.0
    result = collection.query(query_embeddings=[probe], n_results=1, where=where)
    if not result["ids"] or not result["ids"][0]:
        raise RebuildError("CANARY_QUERY_EMPTY", "canary 范围查询未命中。")


def _delete_canary(collection: Any, identity: dict[str, str]) -> None:
    """精确删除 canary，验证三字段过滤生效。"""
    where = {
        "$and": [
            {"user_id": identity["user_id"]},
            {"kb_id": identity["kb_id"]},
            {"document_id": identity["document_id"]},
        ]
    }
    collection.delete(where=where)


def _restore_512_empty_collection(client: Any, name: str) -> None:
    """失败恢复：删除不完整 Collection，重建 512 维空库。"""
    try:
        client.delete_collection(name)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=name,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )
    canary_id = str(uuid.uuid4())
    collection.upsert(
        ids=[canary_id],
        documents=["restore 512 canary"],
        embeddings=[[0.0] * 512],
        metadatas=[{"restore": "true"}],
    )
    collection.delete(ids=[canary_id])
    if collection.count() != 0:
        raise RebuildError(
            "RESTORE_512_FAILED", "512 维空库恢复后条目数不为 0。"
        )


def run_apply() -> RebuildResult:
    """显式重建：删除旧 512 维空 Collection，创建 768 维同名 Collection。"""
    result = run_preflight()
    if result.status == "already_768":
        return result
    if result.status != "rebuild_required":
        raise RebuildError("PREFLIGHT_FAILED", result.detail)

    target_name = settings.chroma_collection
    client = _get_chroma_client()
    dimension = settings.embedding_dimension

    try:
        client.delete_collection(target_name)
        collection = client.get_or_create_collection(
            name=target_name,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )
        _, identity = _write_canary(collection, dimension)
        _verify_canary(collection, identity, dimension)
        _delete_canary(collection, identity)

        if collection.count() != 0:
            raise RebuildError(
                "CANARY_RESIDUE", "canary 删除后 Collection 条目数不为 0。"
            )

        get_chunk_collection.cache_clear()

        return RebuildResult(
            phase="apply",
            collection_name=target_name,
            configured_dimension=dimension,
            actual_dimension=dimension,
            collection_count=0,
            postgres_ready_count=0,
            postgres_chunk_count=0,
            status="rebuild_succeeded",
            detail="Collection 已重建为 768 维空库。",
        )
    except RebuildError:
        _restore_512_empty_collection(client, target_name)
        raise
    except Exception as exc:
        _restore_512_empty_collection(client, target_name)
        raise RebuildError("REBUILD_FAILED", str(exc)) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="安全重建旧制度 Collection 的冻结向量维度。"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式执行重建；要求 API/写入入口已停止。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        if args.apply:
            result = run_apply()
        else:
            result = run_preflight()
    except RebuildError as exc:
        logger.error("rebuild_failed code=%s message=%s", exc.code, exc.message)
        print(f"FAILED {exc.code}: {exc.message}")
        return 1
    except Exception as exc:
        logger.exception("rebuild_unexpected_error")
        print(f"FAILED UNEXPECTED: {exc}")
        return 1

    print(
        f"OK phase={result.phase} collection={result.collection_name} "
        f"configured_dim={result.configured_dimension} "
        f"actual_dim={result.actual_dimension} "
        f"count={result.collection_count} "
        f"pg_ready={result.postgres_ready_count} "
        f"pg_chunked={result.postgres_chunk_count} "
        f"status={result.status}"
    )
    if result.detail:
        print(f"  {result.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
