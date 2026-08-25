"""协调原文件上传、SQLite 状态变更和文档向量化入库。"""

import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    Document,
    DocumentStatus,
    KnowledgeBase,
    ParsedSnapshot,
    Project,
    utc_now,
)
from app.schemas import FieldSummaryRead, LastErrorRead, ProcessDocumentRead
from app.services.archive_parser_service import (
    ARCHIVE_NORMALIZATION_VERSION,
    parse_archive_document,
)
from app.services.chunk_service import TextChunk, split_pages
from app.services.embedding_service import EmbeddedChunk, embed_chunks
from app.services.file_service import (
    calculate_upload_file_hash,
    delete_stored_document_file,
    save_upload_file,
)
from app.services.parser_service import ParsedPage, parse_document
from app.services.vector_service import delete_document_chunks, insert_chunks

logger = logging.getLogger(__name__)


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


async def create_uploaded_document(
    upload: UploadFile,
    knowledge_base: KnowledgeBase,
    session: Session,
) -> Document:
    """保存上传文件，并创建状态为 UPLOADED 的文档记录。

    Args:
        upload: FastAPI 接收到的上传文件。
        knowledge_base: 已通过所有权校验的目标知识库。
        session: 当前请求使用的数据库 Session。

    Returns:
        数据库提交并刷新后的文档记录。

    Raises:
        AppError: 原文件保存失败或文档记录无法写入数据库。
    """
    # 预先生成文档 ID，使文件路径和数据库记录使用同一身份。
    document_id = uuid4()
    stored_file = await save_upload_file(
        upload=upload,
        document_id=document_id,
        kb_id=knowledge_base.id,
    )

    # 上传阶段只保存原文件和元数据，不执行解析、切分或向量化。
    document = Document(
        id=document_id,
        kb_id=knowledge_base.id,
        filename=stored_file.filename,
        storage_path=str(stored_file.path),
        file_hash=stored_file.content_hash,
    )
    # 数据库写入失败时同时回滚事务并清理已保存的孤立文件。
    try:
        session.add(document)
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
    return document


async def create_project_uploaded_document(
    *,
    upload: UploadFile,
    project_id: UUID,
    kb_id: UUID,
    session: Session,
) -> ProcessDocumentRead:
    """保存项目原文件，并创建尚未解析的归档文档。"""
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
    return _build_process_document_read(document=document, archive_document=archive_document)


def _build_process_document_read(
    *,
    document: Document,
    archive_document: ArchiveDocument,
) -> ProcessDocumentRead:
    """将原文件和归档状态组合为项目接口的受控响应。"""
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
) -> ProcessDocumentRead:
    """按允许的来源状态解析项目原文件并持久化快照。"""
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
        session.commit()
    except Exception as exc:
        session.rollback()
        snapshot_path.unlink(missing_ok=True)
        raise AppError(500, "PARSED_SNAPSHOT_CREATE_FAILED", "解析快照保存失败。") from exc

    session.refresh(document)
    session.refresh(archive_document)
    return _build_process_document_read(
        document=document,
        archive_document=archive_document,
    )


def parse_project_document(
    *,
    document: Document,
    session: Session,
) -> ProcessDocumentRead:
    """从 ``UPLOADED`` 解析项目原文件并进入 ``PARSED``。"""
    return _parse_project_document(
        document=document,
        session=session,
        expected_status=ArchiveDocumentStatus.UPLOADED,
        action_name="普通解析",
    )


def retry_parse_project_document(
    *,
    document: Document,
    session: Session,
) -> ProcessDocumentRead:
    """从 ``PARSE_FAILED`` 重新解析原文件并进入 ``PARSED``。"""
    return _parse_project_document(
        document=document,
        session=session,
        expected_status=ArchiveDocumentStatus.PARSE_FAILED,
        action_name="解析重试",
    )


def process_document(
    document: Document,
    knowledge_base: KnowledgeBase,
    session: Session,
) -> Document:
    """解析文档、生成向量并同步写入 Chroma。

    Args:
        document: 已通过知识库归属校验的文档。
        knowledge_base: 文档所属且已通过权限校验的知识库。
        session: 当前请求使用的数据库 Session。

    Returns:
        更新为 READY 状态的文档记录。

    Raises:
        AppError: 文档正在处理，或处理链路中的任一步骤失败。
    """
    # 同一文档只能执行一个处理任务，避免重复删除和写入相同 Chunk。
    if document.status == DocumentStatus.PROCESSING:
        raise AppError(
            status_code=409,
            code="DOCUMENT_ALREADY_PROCESSING",
            message="文档正在处理中。",
        )

    # 先持久化 PROCESSING，使后续请求能够看到文档正在处理。
    document.status = DocumentStatus.PROCESSING
    document.chunk_count = 0
    document.error_message = None
    session.add(document)
    session.commit()
    session.refresh(document)

    process_started_at = perf_counter()

    try:
        # 重新解析前按文档范围清理旧 Chunk，避免向量数量不断累积。
        delete_document_chunks(
            user_id=knowledge_base.owner_id,
            kb_id=knowledge_base.id,
            document_id=document.id,
        )

        # 从服务器原文件中提取页面正文，再按页面生成带位置的 Chunk。
        parse_started_at = perf_counter()
        storage_path = Path(document.storage_path)
        parsed_pages: list[ParsedPage] = parse_document(storage_path)
        text_chunks: list[TextChunk] = split_pages(parsed_pages)
        parse_duration_ms = (perf_counter() - parse_started_at) * 1000

        # 没有有效 Chunk 时不能把文档标记为可检索状态。
        if not text_chunks:
            raise AppError(
                status_code=422,
                code="DOCUMENT_CHUNKS_EMPTY",
                message="文档没有生成有效 Chunk。",
            )
        # 批量生成稳定 Chunk ID 和归一化向量。
        embedding_started_at = perf_counter()
        embedded_chunks = embed_chunks(document.id, text_chunks)
        embedding_duration_ms = (
            perf_counter() - embedding_started_at
        ) * 1000

        # 将向量、正文、归属信息和引用位置统一写入 Chroma。
        insert_started_at = perf_counter()
        chunk_count = insert_chunks(
            user_id=knowledge_base.owner_id,
            kb_id=knowledge_base.id,
            document_id=document.id,
            document_name=document.filename,
            embedded_chunks=embedded_chunks,
        )
        insert_duration_ms = (perf_counter() - insert_started_at) * 1000

        # Chroma 写入成功后再提交 READY，保证业务状态与向量库一致。
        document.status = DocumentStatus.READY
        document.chunk_count = chunk_count
        document.error_message = None
        session.add(document)
        session.commit()
        session.refresh(document)

        # 记录各处理阶段耗时，便于区分解析、Embedding 和入库瓶颈。
        logger.info(
            "document_processed user_id=%s kb_id=%s document_id=%s "
            "pages=%s chunks=%s parse_ms=%.2f embedding_ms=%.2f "
            "insert_ms=%.2f total_ms=%.2f",
            knowledge_base.owner_id,
            knowledge_base.id,
            document.id,
            len(parsed_pages),
            chunk_count,
            parse_duration_ms,
            embedding_duration_ms,
            insert_duration_ms,
            (perf_counter() - process_started_at) * 1000,
        )
    except AppError as exc:
        # 已知业务异常可以安全保存其摘要，同时保留原错误代码。
        session.rollback()
        document.status = DocumentStatus.FAILED
        document.chunk_count = 0
        document.error_message = exc.message[:1000]
        session.add(document)
        session.commit()
        session.refresh(document)
        logger.warning(
            "document_process_failed user_id=%s kb_id=%s document_id=%s "
            "code=%s duration_ms=%.2f",
            knowledge_base.owner_id,
            knowledge_base.id,
            document.id,
            exc.code,
            (perf_counter() - process_started_at) * 1000,
        )
        raise
    except Exception as exc:
        # 未知异常记录完整堆栈，但数据库和接口只保留通用信息。
        session.rollback()
        logger.exception("文档处理发生未知异常。")
        document.status = DocumentStatus.FAILED
        document.chunk_count = 0
        document.error_message = "文档处理失败，请查看服务器日志。"
        session.add(document)
        session.commit()
        session.refresh(document)

        raise AppError(
            status_code=500,
            code="DOCUMENT_PROCESS_FAILED",
            message="文档处理失败。",
        ) from exc

    # 返回 refresh 后的记录，其中包含最终状态和实际 Chunk 数量。
    return document


def _mark_document_delete_failed(
    document_id: UUID,
    error_message: str,
    session: Session,
) -> None:
    """尽量把删除失败状态保存到仍然存在的文档记录。

    Args:
        document_id: 删除失败的文档 UUID。
        error_message: 可以安全写入数据库的错误摘要。
        session: 当前请求使用的数据库 Session。
    """
    # 先撤销当前未完成的数据库事务，再重新读取可持久化的文档记录。
    session.rollback()
    document = session.get(Document, document_id)
    if document is None:
        logger.error(
            "document_delete_failure_state_missing document_id=%s",
            document_id,
        )
        return

    document.status = DocumentStatus.DELETE_FAILED
    document.error_message = error_message[:1000]
    document.updated_at = utc_now()

    try:
        session.add(document)
        session.commit()
    except Exception:
        # 状态记录失败不能覆盖最初的删除异常，但必须保留完整堆栈。
        session.rollback()
        logger.exception(
            "document_delete_failure_state_save_failed document_id=%s",
            document_id,
        )


def delete_document(
    document_id: UUID,
    knowledge_base: KnowledgeBase,
    session: Session,
) -> bool:
    """幂等删除文档的 Chroma Chunk、原文件和业务记录。

    Args:
        document_id: 请求路径中的文档 UUID。
        knowledge_base: 已通过当前用户所有权校验的知识库。
        session: 当前请求使用的数据库 Session。

    Returns:
        实际找到并完成删除时返回 ``True``；记录已不存在时返回
        ``False``，两种情况对 HTTP 接口都表示删除成功。

    Raises:
        AppError: 文档正在处理，或任一删除步骤失败。
    """
    # 只在已授权知识库范围内查找文档；找不到表示它已经被删除。
    document = session.get(Document, document_id)
    if document is None or document.kb_id != knowledge_base.id:
        logger.info(
            "document_delete_idempotent_miss user_id=%s kb_id=%s "
            "document_id=%s",
            knowledge_base.owner_id,
            knowledge_base.id,
            document_id,
        )
        return False

    # 正在解析的文档不能同时删除，避免解析任务重新写入 Chunk。
    if document.status == DocumentStatus.PROCESSING:
        raise AppError(
            status_code=409,
            code="DOCUMENT_PROCESSING",
            message="文档正在处理，暂时不能删除。",
        )

    delete_started_at = perf_counter()

    try:
        # 先提交 DELETING，使并发查询可以观察到当前生命周期状态。
        document.status = DocumentStatus.DELETING
        document.error_message = None
        document.updated_at = utc_now()
        session.add(document)
        session.commit()
        session.refresh(document)

        # 精确条件包含用户、知识库和文档，重试时删除 0 条也算成功。
        deleted_chunk_count = delete_document_chunks(
            user_id=knowledge_base.owner_id,
            kb_id=knowledge_base.id,
            document_id=document.id,
        )

        # 原文件删除同样是幂等操作，文件已不存在时继续清理数据库。
        delete_stored_document_file(document.storage_path)

        # 外部资源清理成功后最后删除 SQLite 身份记录。
        session.delete(document)
        session.commit()
    except AppError as exc:
        _mark_document_delete_failed(document_id, exc.message, session)
        logger.warning(
            "document_delete_failed user_id=%s kb_id=%s document_id=%s "
            "code=%s duration_ms=%.2f",
            knowledge_base.owner_id,
            knowledge_base.id,
            document_id,
            exc.code,
            (perf_counter() - delete_started_at) * 1000,
        )
        raise
    except Exception as exc:
        # 未知异常只把通用摘要写入数据库，内部细节保留在服务端日志。
        safe_message = "文档删除失败，请查看服务器日志。"
        _mark_document_delete_failed(document_id, safe_message, session)
        logger.exception(
            "document_delete_failed user_id=%s kb_id=%s document_id=%s "
            "duration_ms=%.2f",
            knowledge_base.owner_id,
            knowledge_base.id,
            document_id,
            (perf_counter() - delete_started_at) * 1000,
        )
        raise AppError(
            status_code=500,
            code="DOCUMENT_DELETE_FAILED",
            message="文档删除失败。",
        ) from exc

    logger.info(
        "document_deleted user_id=%s kb_id=%s document_id=%s "
        "deleted_chunks=%s duration_ms=%.2f",
        knowledge_base.owner_id,
        knowledge_base.id,
        document_id,
        deleted_chunk_count,
        (perf_counter() - delete_started_at) * 1000,
    )
    return True
