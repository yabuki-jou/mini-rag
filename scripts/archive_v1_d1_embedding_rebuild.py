"""在独立 Chroma Collection 中重建确认档案向量，不创建业务操作记录。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from dataclasses import dataclass
import json
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.db import engine
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldValue,
    Document,
    FieldEvidence,
    ParsedSnapshot,
    Project,
)
from app.services.archive.reads import list_visibility_blocked_document_ids
from app.services.archive.final_chunks import (
    build_final_chunks,
    embed_final_chunks,
    insert_final_chunks,
)
from app.services.archive.indexing import (
    build_embedding_context,
    build_evidence_value_contexts,
)
from app.services.infrastructure.chroma import get_chroma_client


_EXPERIMENT_PREFIX = "archive_final_chunks_exp_"


@dataclass(frozen=True, slots=True)
class RebuildSummary:
    """表示一次只写入实验 Collection 的重建聚合结果。"""

    document_count: int
    chunk_count: int
    contextual_chunk_count: int


def validate_experiment_collection_name(name: str) -> str:
    """验证实验 Collection 名称不会覆盖正式 Collection。"""
    if (
        not isinstance(name, str)
        or not name.startswith(_EXPERIMENT_PREFIX)
        or name == settings.chroma_final_collection
        or not 3 <= len(name) <= 63
    ):
        raise ValueError("实验 Collection 名称必须使用独立的 archive_final_chunks_exp_ 前缀。")
    return name


def get_experiment_collection(name: str) -> Any:
    """创建或获取固定 cosine 度量的独立实验 Collection。"""
    validate_experiment_collection_name(name)
    collection = get_chroma_client().get_or_create_collection(
        name=name,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )
    if collection.name != name or collection.configuration["hnsw"]["space"] != "cosine":
        raise RuntimeError("实验 Collection 配置不符合隔离要求。")
    return collection


def delete_experiment_collection(name: str) -> None:
    """按精确实验名称删除实验 Collection，不允许删除正式 Collection。"""
    validate_experiment_collection_name(name)
    get_chroma_client().delete_collection(name=name)


def _embedding_context(
    *,
    document: Document,
    snapshot: ParsedSnapshot,
    chunks: tuple[Any, ...],
    session: Session,
) -> tuple[str, dict[str, str] | None, int]:
    """按当前固定上下文模式读取字段证据，且只返回向量输入辅助值。"""
    mode = settings.archive_embedding_context_mode
    if mode == "none":
        return "", None, 0

    fields = session.exec(
        select(ArchiveFieldValue).where(ArchiveFieldValue.document_id == document.id)
    ).all()
    if mode == "evidence_values":
        evidences = (
            session.exec(
                select(FieldEvidence).where(
                    FieldEvidence.snapshot_id == snapshot.id,
                    FieldEvidence.field_value_id.in_([field.id for field in fields]),
                )
            ).all()
            if fields
            else []
        )
        contexts = build_evidence_value_contexts(
            chunks=chunks,
            fields=fields,
            evidences=evidences,
            snapshot_id=snapshot.id,
        )
        return "", contexts, len(contexts)

    return build_embedding_context(document=document, fields=fields, mode=mode), None, 0


def rebuild_confirmed_documents(
    *,
    session: Session,
    target_collection: Any,
    allow_formal_collection: bool = False,
) -> RebuildSummary:
    """读取确认快照并重建向量，只写入显式传入的 Collection。

    Args:
        session: 只读业务查询使用的数据库会话。
        target_collection: 本次重建明确允许写入的 Chroma Collection。
        allow_formal_collection: 正式切换脚本经过安全门后允许使用正式名称。
    """
    target_name = str(getattr(target_collection, "name", ""))
    if allow_formal_collection:
        if target_name != settings.chroma_final_collection:
            raise ValueError("正式重建只能写入配置中的正式 Collection。")
    else:
        validate_experiment_collection_name(target_name)
    document_count = 0
    chunk_count = 0
    contextual_chunk_count = 0
    archive_documents = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.status == ArchiveDocumentStatus.CONFIRMED)
        .order_by(ArchiveDocument.document_id)
    ).all()

    for archive_document in archive_documents:
        document = session.get(Document, archive_document.document_id)
        if document is None or document.project_id is None:
            raise ValueError("确认档案缺少有效项目归属。")
        project = session.get(Project, document.project_id)
        snapshot = (
            session.get(ParsedSnapshot, archive_document.current_snapshot_id)
            if archive_document.current_snapshot_id is not None
            else None
        )
        if (
            project is None
            or project.kb_id != document.kb_id
            or snapshot is None
            or snapshot.document_id != document.id
        ):
            raise ValueError("确认档案的项目或解析快照无效。")
        if document.id in list_visibility_blocked_document_ids(project.id, session):
            continue

        chunks = build_final_chunks(document_id=document.id, snapshot=snapshot)
        embedding_context, embedding_contexts, contextual_count = _embedding_context(
            document=document,
            snapshot=snapshot,
            chunks=chunks,
            session=session,
        )
        embedded_chunks = embed_final_chunks(
            document_id=document.id,
            chunks=chunks,
            embedding_context=embedding_context,
            embedding_contexts=embedding_contexts,
        )
        if not embedded_chunks:
            raise ValueError("确认档案没有可写入的 Final Chunk。")
        inserted_count = insert_final_chunks(
            user_id=project.owner_id,
            project_id=project.id,
            kb_id=project.kb_id,
            document_id=document.id,
            filename=document.filename,
            chunks=embedded_chunks,
            collection=target_collection,
        )
        if inserted_count != len(embedded_chunks):
            raise RuntimeError("实验 Collection 写入数量与向量数量不一致。")
        document_count += 1
        chunk_count += inserted_count
        contextual_chunk_count += contextual_count

    return RebuildSummary(
        document_count=document_count,
        chunk_count=chunk_count,
        contextual_chunk_count=contextual_chunk_count,
    )


def main() -> int:
    """运行 D1 向量重建命令并只输出安全聚合结果。"""
    parser = argparse.ArgumentParser(description="在独立 Collection 中重建 D1 Embedding 向量。")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.cleanup:
        delete_experiment_collection(args.collection)
        print(json.dumps({"collection_deleted": True}, ensure_ascii=False))
        return 0

    target_collection = get_experiment_collection(args.collection)
    with Session(engine) as session:
        summary = rebuild_confirmed_documents(
            session=session,
            target_collection=target_collection,
        )
    print(json.dumps({"collection": args.collection, **asdict(summary)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
