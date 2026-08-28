"""实现 P09 ArchiveOperation(INDEX) 的可恢复事务编排。"""

from dataclasses import dataclass
import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    Document,
    FieldReviewStatus,
    ParsedSnapshot,
    Project,
    utc_now,
)
from app.services.archive_final_chunk_service import (
    ArchiveEmbeddedChunk,
    build_final_chunks,
    embed_final_chunks,
    insert_final_chunks,
)


logger = logging.getLogger(__name__)

_INDEX_STARTED = "STARTED"
_INDEX_CHROMA_UPSERT = "CHROMA_UPSERT"

_EMBEDDING_FIELD_LABELS = {
    ArchiveFieldName.TITLE: "档案标题",
    ArchiveFieldName.DOCUMENT_TYPE: "资料类型",
    ArchiveFieldName.DOCUMENT_DATE: "文档日期",
    ArchiveFieldName.AUTHORING_ORGANIZATION: "编制单位",
    ArchiveFieldName.VERSION_NUMBER: "版本号",
    ArchiveFieldName.PROJECT_STAGE: "项目阶段",
    ArchiveFieldName.KEYWORDS: "关键词",
}


@dataclass(frozen=True, slots=True)
class ArchiveIndexResult:
    """表示一次成功或幂等复用的归档索引结果。"""

    operation_id: UUID
    snapshot_hash: str
    chunk_count: int


def _embedding_field_value(field: ArchiveFieldValue) -> str | None:
    """将已确认字段转换为仅用于向量表示的简短上下文值。"""
    if field.field_name == ArchiveFieldName.DOCUMENT_DATE:
        return field.date_value.isoformat() if field.date_value is not None else None
    if field.field_name == ArchiveFieldName.KEYWORDS:
        return "、".join(field.json_value) if field.json_value else None
    return field.text_value.strip() if field.text_value else None


def _build_embedding_context(
    *,
    document: Document,
    fields: list[ArchiveFieldValue],
    mode: str,
) -> str:
    """按模式组合已确认元数据，且不改变最终返回的原文摘录。"""
    if mode == "none":
        return ""

    by_name = {field.field_name: field for field in fields}
    values: list[tuple[str, str]] = []
    for field_name in ArchiveFieldName:
        field = by_name.get(field_name)
        if field is None or field.review_status != FieldReviewStatus.VALUE_CONFIRMED:
            continue
        value = _embedding_field_value(field)
        if value is not None:
            values.append((_EMBEDDING_FIELD_LABELS[field_name], value))

    if mode == "values":
        return "\n".join(value for _, value in values)
    if mode == "labeled":
        lines = [f"档案文件：{document.filename}"]
        lines.extend(f"{label}：{value}" for label, value in values)
        return "\n".join(lines)
    raise ValueError("archive embedding context mode 无效。")


def _result(
    *,
    operation: ArchiveOperation,
    archive_document: ArchiveDocument,
    snapshot: ParsedSnapshot,
) -> ArchiveIndexResult:
    """组合不包含原文正文的索引结果。"""
    return ArchiveIndexResult(
        operation_id=operation.id,
        snapshot_hash=snapshot.snapshot_hash,
        chunk_count=archive_document.final_chunk_count,
    )


def _mark_index_failed(
    *,
    operation_id: UUID,
    document_id: UUID,
    error: AppError,
    session: Session,
) -> None:
    """将外部失败安全落库，保留可重试的 INDEX 终态。"""
    session.rollback()
    operation = session.get(ArchiveOperation, operation_id)
    archive_document = session.get(ArchiveDocument, document_id)
    now = utc_now()
    if operation is not None:
        operation.operation_status = ArchiveOperationStatus.FAILED
        operation.failure_code = error.code
        operation.failure_summary = error.message[:500]
        operation.finished_at = now
        operation.updated_at = now
        session.add(operation)
    if archive_document is not None:
        archive_document.last_error_code = error.code
        archive_document.last_error_summary = error.message[:500]
        archive_document.updated_at = now
        session.add(archive_document)
    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("archive_index_failure_record_failed document_id=%s", document_id)


def index_confirmed_document(
    *,
    document: Document,
    session: Session,
) -> ArchiveIndexResult:
    """为已确认文档创建 INDEX 操作并写入 Final Collection。

    操作记录先提交为 ``RUNNING``，再执行快照构建、Embedding 和 Chroma upsert；
    外部失败会把同一操作标为 ``FAILED``，允许后续入口递增尝试次数重试。
    """
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document.id)
        .with_for_update()
    ).first()
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.status != ArchiveDocumentStatus.CONFIRMED:
        raise AppError(409, "INDEX_NOT_ALLOWED", "当前文档状态不允许正式索引。")
    if document.project_id is None:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")
    project = session.get(Project, document.project_id)
    if project is None or project.kb_id != document.kb_id:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档的项目归属无效。")
    if archive_document.current_snapshot_id is None:
        raise AppError(409, "INDEX_NOT_ALLOWED", "文档缺少当前解析快照。")
    snapshot = session.get(ParsedSnapshot, archive_document.current_snapshot_id)
    if snapshot is None or snapshot.document_id != document.id:
        raise AppError(409, "INDEX_NOT_ALLOWED", "文档当前解析快照无效。")

    running = session.exec(
        select(ArchiveOperation).where(
            ArchiveOperation.document_id == document.id,
            ArchiveOperation.operation_status == ArchiveOperationStatus.RUNNING,
        )
    ).first()
    if running is not None:
        raise AppError(409, "DOCUMENT_OPERATION_IN_PROGRESS", "文档已有运行中的内部操作。")

    latest_index = session.exec(
        select(ArchiveOperation)
        .where(
            ArchiveOperation.document_id == document.id,
            ArchiveOperation.operation_type == ArchiveOperationType.INDEX,
        )
        .order_by(ArchiveOperation.created_at.desc())
    ).first()
    if (
        latest_index is not None
        and latest_index.operation_status == ArchiveOperationStatus.SUCCEEDED
        and archive_document.final_index_snapshot_hash == snapshot.snapshot_hash
        and archive_document.final_chunk_count > 0
    ):
        return _result(
            operation=latest_index,
            archive_document=archive_document,
            snapshot=snapshot,
        )

    operation = ArchiveOperation(
        document_id=document.id,
        operation_type=ArchiveOperationType.INDEX,
        operation_status=ArchiveOperationStatus.RUNNING,
        attempt_no=(latest_index.attempt_no + 1) if latest_index is not None else 1,
        last_completed_step=_INDEX_STARTED,
        started_at=utc_now(),
    )
    session.add(operation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(409, "DOCUMENT_OPERATION_IN_PROGRESS", "文档已有运行中的内部操作。") from exc
    except Exception as exc:
        session.rollback()
        raise AppError(500, "INDEX_OPERATION_CREATE_FAILED", "索引操作创建失败。") from exc
    session.refresh(operation)

    try:
        chunks = build_final_chunks(document_id=document.id, snapshot=snapshot)
        fields = session.exec(
            select(ArchiveFieldValue).where(
                ArchiveFieldValue.document_id == document.id
            )
        ).all()
        embedded_chunks: list[ArchiveEmbeddedChunk] = embed_final_chunks(
            document_id=document.id,
            chunks=chunks,
            embedding_context=_build_embedding_context(
                document=document,
                fields=fields,
                mode=settings.archive_embedding_context_mode,
            ),
        )
        if not embedded_chunks:
            raise AppError(422, "FINAL_CHUNKS_EMPTY", "当前快照没有可索引的 Final Chunk。")
        chunk_count = insert_final_chunks(
            user_id=project.owner_id,
            project_id=project.id,
            kb_id=document.kb_id,
            document_id=document.id,
            filename=document.filename,
            chunks=embedded_chunks,
        )
        if chunk_count != len(embedded_chunks):
            raise AppError(500, "FINAL_CHUNK_WRITE_INCOMPLETE", "Final Chunk 写入未完成。")

        now = utc_now()
        archive_document = session.get(ArchiveDocument, document.id)
        operation = session.get(ArchiveOperation, operation.id)
        if archive_document is None or operation is None:
            raise AppError(500, "INDEX_OPERATION_STATE_LOST", "索引操作状态不存在。")
        archive_document.final_index_snapshot_hash = snapshot.snapshot_hash
        archive_document.final_chunk_count = chunk_count
        archive_document.last_error_code = None
        archive_document.last_error_summary = None
        archive_document.updated_at = now
        operation.operation_status = ArchiveOperationStatus.SUCCEEDED
        operation.last_completed_step = _INDEX_CHROMA_UPSERT
        operation.finished_at = now
        operation.updated_at = now
        session.add_all([archive_document, operation])
        session.commit()
    except AppError as exc:
        _mark_index_failed(
            operation_id=operation.id,
            document_id=document.id,
            error=exc,
            session=session,
        )
        raise
    except Exception as exc:
        error = AppError(500, "INDEX_FAILED", "正式索引失败。")
        _mark_index_failed(
            operation_id=operation.id,
            document_id=document.id,
            error=error,
            session=session,
        )
        raise error from exc

    return _result(
        operation=operation,
        archive_document=archive_document,
        snapshot=snapshot,
    )
