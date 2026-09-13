"""校验、保存并安全删除本地原文件。"""

import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import AppError


ALLOWED_EXTENSIONS = frozenset({".txt", ".md", ".pdf", ".docx"})
FILE_READ_CHUNK_SIZE = 1024 * 1024

logger = logging.getLogger(__name__)


def validate_filename(filename: str | None) -> str:
    """清理并校验客户端提供的上传文件名。

    Args:
        filename: UploadFile 中可能为空或包含目录部分的文件名。

    Returns:
        只保留末级名称且扩展名受支持的安全文件名。

    Raises:
        AppError: 文件名为空、格式无效、过长或扩展名不受支持。
    """
    # UploadFile 可能没有文件名，空名称不能用于创建存储路径。
    if filename is None or not filename.strip():
        raise AppError(400, "INVALID_FILENAME", "文件名不能为空。")

    # 同时移除 Windows 和 Unix 目录部分，防止客户端控制保存目录。
    safe_name = filename.replace("\\", "/").split("/")[-1].strip()

    # 拒绝特殊目录名、空字节和超过数据库字段上限的名称。
    if (
        not safe_name
        or safe_name in {".", ".."}
        or "\x00" in safe_name
        or len(safe_name) > 255
    ):
        raise AppError(
            400,
            "INVALID_FILENAME",
            "文件名无效或长度超过 255 个字符。",
        )

    # 只允许已有解析器支持的文件扩展名。
    if Path(safe_name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise AppError(
            400,
            "UNSUPPORTED_FILE_TYPE",
            "仅支持 TXT、Markdown、PDF 和 DOCX 文件。",
        )
    return safe_name


def _validate_project_upload_filename(filename: str | None) -> str:
    """校验项目上传文件名，并映射为智慧档案 API 的稳定格式错误。"""
    try:
        return validate_filename(filename)
    except AppError as exc:
        if exc.code == "UNSUPPORTED_FILE_TYPE":
            raise AppError(
                415,
                "FILE_TYPE_UNSUPPORTED",
                "仅支持 PDF、DOCX、TXT 和 MD 文件。",
            ) from exc
        raise


@dataclass(frozen=True)
class StoredFile:
    """表示成功保存的原文件信息。

    Attributes:
        filename: 已清除目录部分的安全文件名。
        path: 原文件在服务器上的绝对存储路径。
        content_hash: 原文件内容的 SHA-256 十六进制摘要。
    """

    filename: str
    path: Path
    content_hash: str


async def calculate_upload_file_hash(upload: UploadFile) -> str:
    """读取上传流的 SHA-256，并将流复位以供后续安全落盘。

    Args:
        upload: FastAPI 接收到的、尚未关闭的上传文件。

    Returns:
        原文件字节的 SHA-256 十六进制摘要。

    Raises:
        AppError: 文件名无效、文件为空、文件过大或读取上传流失败。
    """
    content_hasher = sha256()
    total_size = 0
    try:
        # 项目上传预检使用新的 415 契约，旧知识库落盘仍复用原有底层错误。
        _validate_project_upload_filename(upload.filename)

        while chunk := await upload.read(FILE_READ_CHUNK_SIZE):
            total_size += len(chunk)
            # 在原文件落盘前限制上传大小，避免留下超限文件或业务记录。
            if total_size > settings.max_upload_file_bytes:
                raise AppError(
                    413,
                    "FILE_TOO_LARGE",
                    "上传文件不能超过 20 MiB。",
                )
            content_hasher.update(chunk)
        if total_size == 0:
            raise AppError(400, "EMPTY_FILE", "上传文件不能为空。")
        # 重复校验只读取上传流；命中或未命中后均不能把已读位置传给真正的落盘函数。
        await upload.seek(0)
    except AppError:
        await upload.close()
        raise
    except Exception as exc:
        await upload.close()
        raise AppError(500, "FILE_SAVE_FAILED", "文件保存失败。") from exc
    return content_hasher.hexdigest()


async def save_upload_file(
    upload: UploadFile,
    kb_id: UUID,
    document_id: UUID,
) -> StoredFile:
    """流式保存上传文件，并在写入时计算 SHA-256。

    Args:
        upload: FastAPI 接收到的上传文件。
        kb_id: 文件所属知识库的 UUID。
        document_id: 上传前生成的文档 UUID。

    Returns:
        安全文件名、绝对存储路径和内容摘要。

    Raises:
        AppError: 文件名无效、文件为空或写入文件系统失败。
    """
    # 文档独立目录避免不同知识库或同名文件相互覆盖。
    safe_name = validate_filename(upload.filename)
    target_dir = settings.file_storage_path / str(kb_id) / str(document_id)
    target_path = target_dir / safe_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # 在流式写入的同时累计哈希和大小，避免保存后再次读取整个文件。
    content_hasher = sha256()
    total_size = 0
    try:
        with target_path.open("wb") as output_file:
            while chunk := await upload.read(FILE_READ_CHUNK_SIZE):
                output_file.write(chunk)
                content_hasher.update(chunk)
                total_size += len(chunk)

        # 空文件无法生成有效 Chunk，因此上传阶段直接拒绝并清理。
        if total_size == 0:
            _cleanup_partial_file(target_path)
            raise AppError(400, "EMPTY_FILE", "上传文件不能为空。")
    except AppError:
        # 已知业务错误保留原状态码和错误代码。
        raise
    except Exception as exc:
        # 写入异常执行补偿清理，并隐藏操作系统路径等内部细节。
        _cleanup_partial_file(target_path)
        raise AppError(500, "FILE_SAVE_FAILED", "文件保存失败。") from exc
    finally:
        # 无论成功或失败都关闭上传流，及时释放临时文件句柄。
        await upload.close()

    # 仅在完整写入成功后返回文件身份和绝对路径。
    return StoredFile(
        filename=safe_name,
        path=target_path.resolve(),
        content_hash=content_hasher.hexdigest(),
    )


def _cleanup_partial_file(target_path: Path) -> None:
    """尽量删除写入失败后遗留的文件和空文档目录。

    清理失败不会覆盖最初的上传异常，因此本函数不向外抛出
    ``OSError``。

    Args:
        target_path: 需要清理的目标文件路径。
    """
    # 删除文件后尝试删除空目录；非空或被占用时静默保留。
    try:
        target_path.unlink(missing_ok=True)
        target_path.parent.rmdir()
    except OSError:
        pass


def _remove_empty_document_storage_directories(
    *,
    storage_root: Path,
    relative_path: Path,
    document_directory: Path,
) -> None:
    """在文件删除后依次清理空文档目录和空知识库目录。"""
    try:
        document_directory.rmdir()
    except OSError:
        # 目录非空、已被并发清理或暂时被占用时，文件删除结果仍然有效。
        logger.debug("文档存储目录暂不清理。")
        return

    # 仅对标准 kb_id/document_id/filename 布局清理知识库目录，避免递归触及
    # 旧路径或存储根目录。
    if len(relative_path.parts) != 3:
        return
    knowledge_base_directory = document_directory.parent
    if knowledge_base_directory.parent != storage_root:
        return
    try:
        knowledge_base_directory.rmdir()
    except OSError:
        # 同一知识库仍有其他文档、目录已不存在或被占用时必须保留。
        logger.debug("知识库存储目录暂不清理。")


def delete_stored_document_file(storage_path: str) -> None:
    """在配置的文件目录内幂等删除一个文档原文件。

    Args:
        storage_path: ``Document.storage_path`` 保存的原文件绝对路径。

    Raises:
        AppError: 路径越界、路径层级无效、目标是目录或文件删除失败。
    """
    # 同时规范化存储根目录和目标路径，防止 ``..`` 或符号链接越界。
    storage_root = settings.file_storage_path.resolve()
    target_path = Path(storage_path).resolve()

    try:
        relative_path = target_path.relative_to(storage_root)
    except ValueError as exc:
        raise AppError(
            status_code=500,
            code="DOCUMENT_STORAGE_PATH_INVALID",
            message="文档存储路径无效。",
        ) from exc

    # 上传路径至少应为 kb_id/document_id/filename，不能把存储根目录、
    # 知识库目录或文档目录误当成文件删除。
    if len(relative_path.parts) < 3 or target_path.is_dir():
        raise AppError(
            status_code=500,
            code="DOCUMENT_STORAGE_PATH_INVALID",
            message="文档存储路径无效。",
        )

    try:
        # missing_ok 让重试删除时把“文件已经不存在”视为成功。
        target_path.unlink(missing_ok=True)
    except OSError as exc:
        raise AppError(
            status_code=500,
            code="DOCUMENT_FILE_DELETE_FAILED",
            message="文档原文件删除失败。",
        ) from exc

    _remove_empty_document_storage_directories(
        storage_root=storage_root,
        relative_path=relative_path,
        document_directory=target_path.parent,
    )


def delete_stored_snapshot_file(storage_path: str) -> None:
    """在配置的文件目录内幂等删除解析快照文件。

    Args:
        storage_path: ``ParsedSnapshot.snapshot_storage_path`` 保存的快照绝对路径。

    Raises:
        AppError: 路径越界、路径层级无效、目标不是解析快照或删除失败。
    """
    # 快照与原文件共用文档目录，但必须限制为固定文件名，避免误删其他文件。
    storage_root = settings.file_storage_path.resolve()
    target_path = Path(storage_path).resolve()

    try:
        relative_path = target_path.relative_to(storage_root)
    except ValueError as exc:
        raise AppError(
            status_code=500,
            code="DOCUMENT_STORAGE_PATH_INVALID",
            message="文档存储路径无效。",
        ) from exc

    if (
        len(relative_path.parts) < 3
        or target_path.is_dir()
        or target_path.name != "parsed_snapshot.json"
    ):
        raise AppError(
            status_code=500,
            code="DOCUMENT_STORAGE_PATH_INVALID",
            message="文档存储路径无效。",
        )

    try:
        # missing_ok 让跨存储删除重试时把已清理的快照视为成功。
        target_path.unlink(missing_ok=True)
    except OSError as exc:
        raise AppError(
            status_code=500,
            code="DOCUMENT_SNAPSHOT_DELETE_FAILED",
            message="解析快照删除失败。",
        ) from exc

    # 原文件步骤可能因快照仍存在而未能删除目录，此处会再次清理空文档和知识库目录。
    _remove_empty_document_storage_directories(
        storage_root=storage_root,
        relative_path=relative_path,
        document_directory=target_path.parent,
    )
