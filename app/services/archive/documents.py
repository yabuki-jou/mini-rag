"""协调项目原文件上传、解析快照、解析失败记录和解析重试。"""

import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    Document,
    ParsedSnapshot,
    Project,
    utc_now,
)
from app.schemas.document import FieldSummaryRead, LastErrorRead, ProcessDocumentRead
from app.services.archive.parser import (
    ARCHIVE_NORMALIZATION_VERSION,
    parse_archive_document,
)
from app.services.infrastructure.files import (
    calculate_upload_file_hash,
    save_upload_file,
)

logger = logging.getLogger(__name__)

PARSE_RETRIED = "PARSE_RETRIED"


def _cleanup_saved_file(path: Path) -> None:
    """数据库提交失败时尽量清理已经保存的原文件。

    Args:
        path: 已成功写入、但需要回滚清理的原文件路径。
    """
    # 清理属于补偿操作，失败时只记录日志，避免覆盖原始数据库异常。
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        logger.exception("数据库写入失败后清理原文件失败。")


async def create_project_uploaded_document(
    *,
    upload: UploadFile,
    project_id: UUID,
    kb_id: UUID,
    session: Session,
) -> ProcessDocumentRead:
    """保存项目原文件，并创建尚未解析的归档文档。

    Args:
        upload: FastAPI 接收到的项目上传文件。
        project_id: 已验证的项目身份。
        kb_id: 项目绑定的知识库身份。
        session: 当前数据库会话。
    """
    # 文件类型和大小属于无状态预检；先完成它们，避免无效上传占用项目写锁。
    # 流式读取同时得到查重哈希，并在成功后把上传流复位到开头。
    file_hash = await calculate_upload_file_hash(upload)

    # PostgreSQL 中锁定项目聚合直至上传事务结束，使容量检查和计数递增保持原子。
    # SQLite 测试会忽略 FOR UPDATE，只验证确定性的业务结果。
    project = session.exec(
        select(Project).where(Project.id == project_id).with_for_update()
    ).first()
    if project is None:
        await upload.close()
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问。")
    if project.active_document_count >= 100:
        await upload.close()
        raise AppError(
            409,
            "PROJECT_DOCUMENT_LIMIT_REACHED",
            "当前项目已达到 100 份文档上限。",
        )

    existing_document = session.exec(
        select(Document).where(
            Document.project_id == project_id,
            Document.file_hash == file_hash,
        )
    ).first()
    if existing_document is not None:
        existing_archive_document = session.get(ArchiveDocument, existing_document.id)
        await upload.close()
        if existing_archive_document is None:
            raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
        raise AppError(
            409,
            "DUPLICATE_FILE",
            "当前项目已存在相同原文件。",
            details={
                "id": existing_document.id,
                "filename": existing_document.filename,
                "status": existing_archive_document.status,
            },
        )

    document_id = uuid4()
    stored_file = await save_upload_file(
        upload=upload,
        document_id=document_id,
        kb_id=kb_id,
    )
    document = Document(
        id=document_id,
        kb_id=kb_id,
        project_id=project_id,
        filename=stored_file.filename,
        storage_path=str(stored_file.path),
        file_hash=stored_file.content_hash,
    )
    archive_document = ArchiveDocument(
        document_id=document_id,
        status=ArchiveDocumentStatus.UPLOADED,
    )

    try:
        # ArchiveDocument 依赖 documents；显式 flush 保持 SQLite 与 PostgreSQL 的外键顺序一致。
        session.add(document)
        session.flush()
        session.add(archive_document)
        project.active_document_count += 1
        project.updated_at = utc_now()
        session.add(project)
        session.commit()
    except Exception as exc:
        session.rollback()
        _cleanup_saved_file(stored_file.path)
        raise AppError(
            status_code=500,
            code="DOCUMENT_CREATE_FAILED",
            message="文档记录创建失败。",
        ) from exc

    session.refresh(document)
    session.refresh(archive_document)
    return _build_initial_process_document_read(document=document, archive_document=archive_document)


def _build_initial_process_document_read(
    *,
    document: Document,
    archive_document: ArchiveDocument,
) -> ProcessDocumentRead:
    """将原文件和归档状态组合为项目接口的受控响应。

    Args:
        document: 原始文档记录。
        archive_document: 文档对应的档案生命周期记录。
    """
    return ProcessDocumentRead(
        id=document.id,
        filename=document.filename,
        file_hash=document.file_hash,
        status=archive_document.status,
        last_error=LastErrorRead(
            code=archive_document.last_error_code,
            message=archive_document.last_error_summary,
        ),
        # P05 尚未创建字段草稿，但项目文档始终遵循七字段固定契约。
        field_summary=FieldSummaryRead(checked_count=0, total_count=7),
        confirmed_at=archive_document.confirmed_at,
        version=archive_document.version,
        uploaded_at=document.created_at,
        updated_at=archive_document.updated_at,
    )


def _parse_project_document(
    *,
    document: Document,
    session: Session,
    expected_status: ArchiveDocumentStatus,
    action_name: str,
    actor_id: UUID | None = None,
) -> ProcessDocumentRead:
    """按允许的来源状态解析项目原文件并持久化快照。

    Args:
        document: 待解析且已通过项目范围校验的文档。
        session: 当前数据库会话。
        expected_status: 允许开始本次解析的原档案状态。
        action_name: 写入审计和操作记录的动作名称。
        actor_id: 已认证执行解析的用户身份，可为空。
    """
    archive_document = session.get(ArchiveDocument, document.id)
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.status != expected_status:
        raise AppError(
            409,
            "PARSE_NOT_ALLOWED",
            f"当前文档状态不允许{action_name}。",
        )

    try:
        parsed_document = parse_archive_document(Path(document.storage_path))
    except AppError as exc:
        # 解析失败也要落库，保留原文件和失败分类，供后续专用重试接口使用。
        archive_document.status = ArchiveDocumentStatus.PARSE_FAILED
        archive_document.last_error_code = exc.code
        archive_document.last_error_summary = exc.message
        archive_document.updated_at = utc_now()
        try:
            session.add(archive_document)
            session.commit()
        except Exception as record_exc:
            session.rollback()
            raise AppError(
                500,
                "PARSE_FAILURE_RECORD_FAILED",
                "解析失败记录保存失败。",
            ) from record_exc
        raise

    snapshot_path = Path(document.storage_path).parent / "parsed_snapshot.json"
    snapshot_payload = {
        "document_id": str(document.id),
        "file_hash": document.file_hash,
        "snapshot_hash": parsed_document.snapshot_hash,
        "parser_version": parsed_document.parser_version,
        "normalization_version": ARCHIVE_NORMALIZATION_VERSION,
        "fragments": [
            {
                "location_type": fragment.location_type.value,
                "location_start": fragment.location_start,
                "location_end": fragment.location_end,
                "content": fragment.content,
                "anchor_text": fragment.anchor_text,
            }
            for fragment in parsed_document.fragments
        ],
    }

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_path.write_text(
            json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        snapshot = ParsedSnapshot(
            document_id=document.id,
            snapshot_storage_path=str(snapshot_path),
            snapshot_hash=parsed_document.snapshot_hash,
            parser_name="archive_parser_service",
            parser_version=parsed_document.parser_version,
            normalization_version=ARCHIVE_NORMALIZATION_VERSION,
            text_character_count=parsed_document.effective_text_characters,
            fragment_count=len(parsed_document.fragments),
        )
        session.add(snapshot)
        session.flush()
        archive_document.current_snapshot_id = snapshot.id
        archive_document.status = ArchiveDocumentStatus.PARSED
        archive_document.last_error_code = None
        archive_document.last_error_summary = None
        archive_document.updated_at = utc_now()
        session.add(archive_document)
        if expected_status == ArchiveDocumentStatus.PARSE_FAILED:
            if actor_id is None or document.project_id is None:
                session.rollback()
                raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")
            session.add(
                ArchiveAuditLog(
                    project_id=document.project_id,
                    actor_id=actor_id,
                    operation_type=PARSE_RETRIED,
                    resource_type="ARCHIVE_DOCUMENT",
                    resource_id=document.id,
                    redacted_summary={"status": ArchiveDocumentStatus.PARSED.value},
                )
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        snapshot_path.unlink(missing_ok=True)
        raise AppError(500, "PARSED_SNAPSHOT_CREATE_FAILED", "解析快照保存失败。") from exc

    session.refresh(document)
    session.refresh(archive_document)
    return _build_initial_process_document_read(
        document=document,
        archive_document=archive_document,
    )


def parse_project_document(
    *,
    document: Document,
    session: Session,
) -> ProcessDocumentRead:
    """从 ``UPLOADED`` 解析项目原文件并进入 ``PARSED``。

    Args:
        document: 待解析且已通过项目范围校验的文档。
        session: 当前数据库会话。
    """
    return _parse_project_document(
        document=document,
        session=session,
        expected_status=ArchiveDocumentStatus.UPLOADED,
        action_name="普通解析",
    )


def retry_parse_project_document(
    *,
    document: Document,
    actor_id: UUID,
    session: Session,
) -> ProcessDocumentRead:
    """从 ``PARSE_FAILED`` 重新解析原文件并进入 ``PARSED``。

    Args:
        document: 解析失败且已通过项目范围校验的文档。
        actor_id: 已认证执行重试的用户身份。
        session: 当前数据库会话。
    """
    return _parse_project_document(
        document=document,
        session=session,
        expected_status=ArchiveDocumentStatus.PARSE_FAILED,
        action_name="解析重试",
        actor_id=actor_id,
    )
