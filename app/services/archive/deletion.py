"""实现智慧档案文档的物理删除、可见性阻断和跨存储重试。"""

import logging
from time import perf_counter
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.errors import AppError
from app.core.evaluation import eval_wrap
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    ArchiveFieldValue,
    ChecklistLink,
    Document,
    DocumentStatus,
    FieldEvidence,
    ParsedSnapshot,
    Project,
    utc_now,
)
from app.services.archive.final_chunks import delete_final_chunks
from app.services.infrastructure.files import (
    delete_stored_document_file,
    delete_stored_snapshot_file,
)


logger = logging.getLogger(__name__)

DOCUMENT_DELETED = "DOCUMENT_DELETED"
DOCUMENT_RESOURCE_TYPE = "DOCUMENT"


def _mark_delete_failed(
    *,
    document_id: UUID,
    operation_id: UUID | None,
    session: Session,
) -> None:
    """将跨存储失败记录为保守隐藏、可重试的状态。

    Args:
        document_id: 删除失败的档案文档身份。
        operation_id: 本次删除操作的身份。
        session: 当前数据库会话。
    """
    session.rollback()
    document = session.get(Document, document_id)
    archive_document = session.get(ArchiveDocument, document_id)
    operation = (
        session.get(ArchiveOperation, operation_id)
        if operation_id is not None
        else session.exec(
            select(ArchiveOperation).where(
                ArchiveOperation.document_id == document_id,
                ArchiveOperation.operation_type == ArchiveOperationType.DELETE,
                ArchiveOperation.operation_status.in_(
                    (ArchiveOperationStatus.RUNNING, ArchiveOperationStatus.FAILED)
                ),
            )
        ).first()
    )
    if document is None or archive_document is None or operation is None:
        logger.error("archive_delete_failure_state_missing document_id=%s", document_id)
        return

    now = utc_now()
    document.status = DocumentStatus.DELETE_FAILED
    document.error_message = "跨存储删除未完成。"
    document.updated_at = now
    operation.operation_status = ArchiveOperationStatus.FAILED
    operation.failure_code = "DOCUMENT_DELETE_INCOMPLETE"
    operation.failure_summary = "跨存储删除未完成。"
    operation.finished_at = now
    operation.updated_at = now
    operation.visibility_blocking = True
    session.add(document)
    session.add(operation)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("archive_delete_failure_state_save_failed document_id=%s", document_id)


def delete_archive_document(*, document: Document, actor_id: UUID, session: Session) -> None:
    """物理删除项目文档，并在外部失败时保留可重入操作记录。

    Args:
        document: 已通过项目范围校验的档案文档。
        actor_id: 已认证执行删除的用户身份。
        session: 当前数据库会话。
    """
    document_id = document.id
    # 注释 1：行锁只能串行化删除准备，无法让 Chroma 和文件系统具备事务性；
    # 操作记录负责跨越后续外部调用保存恢复状态。
    current = session.exec(
        select(Document).where(Document.id == document_id).with_for_update()
    ).first()
    if current is None or current.project_id is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在或不属于当前项目。")
    project = session.get(Project, current.project_id)
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document_id)
        .with_for_update()
    ).first()
    if project is None or project.owner_id != actor_id or archive_document is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在或不属于当前项目。")

    operations = session.exec(
        select(ArchiveOperation)
        .where(ArchiveOperation.document_id == document_id)
        .with_for_update()
    ).all()
    # 注释 2：否则解析、建议或索引任务可能在删除期间重新创建数据；
    # 拒绝并发任务可使删除保持单向流转。
    for operation in operations:
        if (
            operation.operation_status == ArchiveOperationStatus.RUNNING
            and operation.operation_type != ArchiveOperationType.DELETE
        ):
            raise AppError(409, "DOCUMENT_OPERATION_IN_PROGRESS", "文档已有操作正在进行。")

    delete_operation = next(
        (
            operation
            for operation in operations
            if operation.operation_type == ArchiveOperationType.DELETE
            and operation.operation_status == ArchiveOperationStatus.RUNNING
        ),
        None,
    )
    if delete_operation is not None:
        raise AppError(409, "DOCUMENT_OPERATION_IN_PROGRESS", "文档删除正在进行。")
    delete_operation = next(
        (
            operation
            for operation in operations
            if operation.operation_type == ArchiveOperationType.DELETE
            and operation.operation_status == ArchiveOperationStatus.FAILED
        ),
        None,
    )

    now = utc_now()
    if delete_operation is None:
        delete_operation = ArchiveOperation(
            document_id=document_id,
            operation_type=ArchiveOperationType.DELETE,
            operation_status=ArchiveOperationStatus.RUNNING,
            visibility_blocking=True,
            started_at=now,
        )
    else:
        delete_operation.operation_status = ArchiveOperationStatus.RUNNING
        delete_operation.visibility_blocking = True
        delete_operation.attempt_no += 1
        delete_operation.failure_code = None
        delete_operation.failure_summary = None
        delete_operation.finished_at = None
        delete_operation.started_at = now
        delete_operation.updated_at = now
    # 注释 3：访问外部存储前先提交可见性阻断，避免部分删除的文档仍出现在
    # 目录或检索 API 中。
    current.status = DocumentStatus.DELETING
    current.error_message = None
    current.updated_at = now
    session.add(current)
    session.add(delete_operation)
    session.commit()
    session.refresh(delete_operation)

    operation_id = delete_operation.id
    started_at = perf_counter()
    try:
        # 注释 4：优先删除 Chroma，因为可被检索到的过期向量比删除失败后暂时保留
        # 原文件的风险更高。
        deleted_chunk_count = delete_final_chunks(
            user_id=actor_id,
            project_id=project.id,
            kb_id=project.kb_id,
            document_id=document_id,
        )
        eval_wrap(
            {"step": "CHROMA_DELETE", "deleted_chunk_count": deleted_chunk_count},
            purpose="state",
            name="archive_delete_external_result",
            description="物理删除的 Chroma 步骤结果；不记录文档内容或资源标识。",
        )
        delete_operation.last_completed_step = "CHROMA_DELETE"
        delete_operation.updated_at = utc_now()
        session.add(delete_operation)
        session.commit()

        # 注释 5：文件步骤单独记录检查点；即使进程重启，重试也能从已持久化的
        # 操作状态安全继续。快照路径先从数据库对象读取，随后与原文件一起清理。
        snapshot_id = archive_document.current_snapshot_id
        snapshot = (
            session.get(ParsedSnapshot, snapshot_id)
            if snapshot_id is not None
            else None
        )
        delete_stored_document_file(current.storage_path)
        if snapshot is not None:
            delete_stored_snapshot_file(snapshot.snapshot_storage_path)
        eval_wrap(
            {"step": "FILE_DELETE", "status": "completed"},
            purpose="state",
            name="archive_delete_external_result",
            description="物理删除的原文件和解析快照步骤结果；不记录路径或内容。",
        )
        delete_operation.last_completed_step = "FILE_DELETE"
        delete_operation.updated_at = utc_now()
        session.add(delete_operation)
        session.commit()

        field_ids = session.exec(
            select(ArchiveFieldValue.id).where(
                ArchiveFieldValue.document_id == document_id
            )
        ).all()
        if field_ids:
            session.execute(delete(FieldEvidence).where(FieldEvidence.field_value_id.in_(field_ids)))
        session.execute(delete(ArchiveFieldValue).where(ArchiveFieldValue.document_id == document_id))
        session.execute(delete(ChecklistLink).where(ChecklistLink.document_id == document_id))
        # 注释 6：只有外部清理完成后才删除数据库事实；外部步骤仍可能失败时，
        # 必须保留标识符和恢复标记。
        # 先离开 CONFIRMED，才能在约束下清空当前快照和确认事实。
        archive_document.status = ArchiveDocumentStatus.PENDING_RECONFIRMATION
        archive_document.confirmed_by = None
        archive_document.confirmed_at = None
        archive_document.final_index_snapshot_hash = None
        archive_document.final_chunk_count = 0
        archive_document.current_snapshot_id = None
        session.add(archive_document)
        session.flush()
        if snapshot_id is not None:
            session.execute(delete(ParsedSnapshot).where(ParsedSnapshot.id == snapshot_id))
        session.execute(delete(ArchiveOperation).where(ArchiveOperation.id == operation_id))
        session.add(
            ArchiveAuditLog(
                project_id=project.id,
                actor_id=actor_id,
                operation_type=DOCUMENT_DELETED,
                resource_type=DOCUMENT_RESOURCE_TYPE,
                resource_id=document_id,
                operation_id=operation_id,
                redacted_summary={},
            )
        )
        session.delete(archive_document)
        session.delete(current)
        project.active_document_count = max(0, project.active_document_count - 1)
        project.updated_at = utc_now()
        session.add(project)
        session.commit()
        eval_wrap(
            {"outcome": "deleted"},
            purpose="output",
            name="archive_delete_outcome",
            description="物理删除服务向调用方交付的最终完成结果。",
        )
    except AppError as exc:
        # 注释 7：预期业务失败在服务端保留稳定错误码，客户端对所有未完成删除
        # 只接收一种可重试契约。
        _mark_delete_failed(document_id=document_id, operation_id=operation_id, session=session)
        eval_wrap(
            {"outcome": "incomplete", "failure_code": exc.code},
            purpose="output",
            name="archive_delete_outcome",
            description="外部删除未完成时对调用方可见的保守结果。",
        )
        logger.warning(
            "archive_document_delete_failed document_id=%s code=%s duration_ms=%.2f",
            document_id,
            exc.code,
            (perf_counter() - started_at) * 1000,
        )
        raise AppError(503, "DOCUMENT_DELETE_INCOMPLETE", "文档删除未完成，可稍后重试。") from exc
    except Exception as exc:
        # 注释 8：未知基础设施异常也走同一保守恢复路径，且不向 HTTP 响应暴露
        # 异常文本。
        _mark_delete_failed(document_id=document_id, operation_id=operation_id, session=session)
        eval_wrap(
            {"outcome": "incomplete", "failure_code": "UNEXPECTED_EXTERNAL_FAILURE"},
            purpose="output",
            name="archive_delete_outcome",
            description="外部删除未完成时对调用方可见的保守结果。",
        )
        logger.exception(
            "archive_document_delete_failed document_id=%s duration_ms=%.2f",
            document_id,
            (perf_counter() - started_at) * 1000,
        )
        raise AppError(503, "DOCUMENT_DELETE_INCOMPLETE", "文档删除未完成，可稍后重试。") from exc
