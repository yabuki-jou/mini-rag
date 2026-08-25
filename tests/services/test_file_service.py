"""验证原文件删除的路径边界、幂等性和目录清理规则。"""

from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.services.file_service import delete_stored_document_file


def test_delete_stored_document_file_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复删除同一原文件应成功，并只清理空文档目录。"""
    storage_root = tmp_path / "files"
    target_path = storage_root / "kb-id" / "document-id" / "policy.txt"
    target_path.parent.mkdir(parents=True)
    target_path.write_text("制度正文", encoding="utf-8")
    monkeypatch.setattr(settings, "file_storage_dir", storage_root)

    delete_stored_document_file(str(target_path))
    delete_stored_document_file(str(target_path))

    assert not target_path.exists()
    assert not target_path.parent.exists()
    assert (storage_root / "kb-id").exists()


def test_delete_stored_document_file_rejects_path_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存储根目录外的路径不能被文档删除逻辑处理。"""
    storage_root = tmp_path / "files"
    storage_root.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("不能删除", encoding="utf-8")
    monkeypatch.setattr(settings, "file_storage_dir", storage_root)

    with pytest.raises(AppError) as exc_info:
        delete_stored_document_file(str(outside_file))

    assert exc_info.value.code == "DOCUMENT_STORAGE_PATH_INVALID"
    assert outside_file.exists()


def test_delete_stored_document_file_rejects_directory_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使目录层级足够，也不能把目录当成原文件删除。"""
    storage_root = tmp_path / "files"
    directory_target = storage_root / "kb-id" / "document-id" / "nested"
    directory_target.mkdir(parents=True)
    monkeypatch.setattr(settings, "file_storage_dir", storage_root)

    with pytest.raises(AppError) as exc_info:
        delete_stored_document_file(str(directory_target))

    assert exc_info.value.code == "DOCUMENT_STORAGE_PATH_INVALID"
    assert directory_target.exists()
