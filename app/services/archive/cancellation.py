"""实现 AV1-P09 取消确认和 Final Chunk 清理流程。"""

from uuid import UUID

from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    Document,
    Project,
    utc_now,
)
from app.schemas.archive_confirmation import ArchiveConfirmationRequest
from app.schemas.document import ProcessDocumentRead
from app.services.archive.reads import (
    build_process_document_read,
    list_archive_field_values,
)
from app.services.archive.final_chunks import delete_final_chunks


ARCHIVE_CONFIRMATION_CANCELLED = "ARCHIVE_CONFIRMATION_CANCELLED"
ARCHIVE_DOCUMENT_RESOURCE_TYPE = "ARCHIVE_DOCUMENT"


def _record_cleanup_failure(
    *,
    document_id: UUID,
    error: AppError,
    session: Session,
) -> None:
    """记录清理失败，但保留已提交的非正式状态。

    Args:
        document_id: 清理失败的档案文档身份。
        error: 外部向量清理失败的原始异常。
        session: 当前数据库会话。
    """
    # 确认取消已经提交后，Chroma 清理属于另一存储；失败时回滚当前会话中的脏状态，
    # 再只写入可重试错误，避免把未完成清理误标成已完成。
    session.rollback()
    archive_document = session.get(ArchiveDocument, document_id)
    if archive_document is None:
        return
    archive_document.last_error_code = error.code
    archive_document.last_error_summary = error.message[:500]
    archive_document.updated_at = utc_now()
    session.add(archive_document)
    try:
        session.commit()
    except Exception:
        session.rollback()


def cancel_confirmation(
    *,
    document: Document,
    actor_id: UUID,
    payload: ArchiveConfirmationRequest,
    session: Session,
) -> ProcessDocumentRead:
    """取消确认、退出正式范围并清理当前文档的 Final Chunk。

    Args:
        document: 已通过项目范围校验的档案文档。
        actor_id: 已认证执行取消确认的用户身份。
        payload: 包含客户端预期文档版本的请求。
        session: 当前数据库事务会话。

    Returns:
        已退出正式范围、并反映当前数据库状态的文档处理投影。

    Raises:
        AppError: 版本或状态不允许、数据库保存失败或向量清理失败。
    """
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document.id)
        .with_for_update()
    ).first()
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.version != payload.expected_version:
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取档案。")
    if archive_document.status != ArchiveDocumentStatus.CONFIRMED:
        raise AppError(409, "CANCEL_CONFIRMATION_NOT_ALLOWED", "当前文档状态不允许取消确认。")
    if document.project_id is None:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")
    project = session.get(Project, document.project_id)
    if project is None or project.kb_id != document.kb_id:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档的项目归属无效。")

    now = utc_now()
    archive_document.status = ArchiveDocumentStatus.PENDING_RECONFIRMATION
    archive_document.confirmed_by = None
    archive_document.confirmed_at = None
    archive_document.final_index_snapshot_hash = None
    archive_document.final_chunk_count = 0
    archive_document.last_error_code = None
    archive_document.last_error_summary = None
    archive_document.version += 1
    archive_document.updated_at = now
    session.add(archive_document)
    session.add(
        ArchiveAuditLog(
            project_id=document.project_id,
            actor_id=actor_id,
            operation_type=ARCHIVE_CONFIRMATION_CANCELLED,
            resource_type=ARCHIVE_DOCUMENT_RESOURCE_TYPE,
            resource_id=document.id,
            redacted_summary={"status": ArchiveDocumentStatus.PENDING_RECONFIRMATION.value},
        )
    )
    try:
        # 先提交“非正式”状态，再删除 Chroma；这样清理期间 PostgreSQL 已成为可见性
        # 闸门，向量即使暂时残留也不会继续进入正式目录或检索结果。
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(500, "CANCEL_CONFIRMATION_FAILED", "取消确认保存失败。") from exc

    try:
        # Chroma 删除按完整 user/project/kb/document 范围执行；幂等删除使客户端在
        # 记录清理失败后可以安全重试，而不会影响其他项目向量。
        delete_final_chunks(
            user_id=project.owner_id,
            project_id=project.id,
            kb_id=document.kb_id,
            document_id=document.id,
        )
    except AppError as exc:
        _record_cleanup_failure(document_id=document.id, error=exc, session=session)
        raise
    except Exception as exc:
        error = AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。")
        _record_cleanup_failure(document_id=document.id, error=error, session=session)
        raise error from exc

    archive_document = session.get(ArchiveDocument, document.id)
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    return build_process_document_read(
        document=document,
        archive_document=archive_document,
        field_values=list_archive_field_values(document.id, session),
    )
