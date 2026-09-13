"""验证文档删除接口的幂等性、隔离条件和失败状态。"""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.core.errors import AppError
from app.db import get_session
from app.main import app
from app.models import Document, DocumentStatus, KnowledgeBase, User
from app.routers import documents as documents_router
from app.services.rag import documents as document_service
from app.services.rag import vector_store as vector_service
from tests.support.auth import auth_headers


@pytest.fixture
def document_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, Engine, Path], None, None]:
    """创建隔离的文档删除 API、SQLite 和文件目录。

    Yields:
        测试客户端、内存 SQLite Engine 和测试文件存储根目录。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    storage_root = tmp_path / "files"
    storage_root.mkdir()
    monkeypatch.setattr(settings, "file_storage_dir", storage_root)

    def override_get_session() -> Generator[Session, None, None]:
        """为每个 HTTP 请求提供隔离的内存数据库 Session。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    try:
        yield client, engine, storage_root
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_document_records(
    engine: Engine,
    storage_root: Path,
    *,
    user_name: str = "owner",
    document_status: DocumentStatus = DocumentStatus.READY,
) -> tuple[UUID, UUID, UUID, Path]:
    """创建用户、知识库、文档记录和对应原文件。

    Args:
        engine: 测试使用的 SQLite Engine。
        storage_root: 测试原文件存储根目录。
        user_name: 测试用户名称。
        document_status: 文档初始状态。

    Returns:
        用户、知识库、文档 UUID 以及原文件路径。
    """
    user = User(name=user_name)
    knowledge_base = KnowledgeBase(owner_id=user.id, name="测试知识库")
    document = Document(
        kb_id=knowledge_base.id,
        filename="policy.txt",
        storage_path="pending",
        file_hash="a" * 64,
        status=document_status,
        chunk_count=2,
    )
    user_id = user.id
    kb_id = knowledge_base.id
    document_id = document.id
    file_path = (
        storage_root
        / str(knowledge_base.id)
        / str(document.id)
        / document.filename
    )
    file_path.parent.mkdir(parents=True)
    file_path.write_text("测试制度正文", encoding="utf-8")
    document.storage_path = str(file_path.resolve())

    with Session(engine) as session:
        session.add(user)
        session.add(knowledge_base)
        session.add(document)
        session.commit()

    return user_id, kb_id, document_id, file_path


def test_delete_document_endpoint_cleans_all_resources_and_is_idempotent(
    document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次删除应清理三类资源，第二次删除仍返回 204。"""
    client, engine, storage_root = document_api
    user_id, kb_id, document_id, file_path = create_document_records(
        engine,
        storage_root,
    )
    delete_chunks_mock = Mock(return_value=2)
    monkeypatch.setattr(
        document_service,
        "delete_document_chunks",
        delete_chunks_mock,
    )
    url = f"/knowledge-bases/{kb_id}/documents/{document_id}"
    headers = {
        **auth_headers(engine, user_id),
        "X-Request-ID": "delete-test-request",
    }

    first_response = client.delete(url, headers=headers)
    second_response = client.delete(url, headers=headers)

    assert first_response.status_code == 204
    assert first_response.content == b""
    assert first_response.headers["X-Request-ID"] == "delete-test-request"
    assert second_response.status_code == 204
    assert not file_path.exists()
    delete_chunks_mock.assert_called_once_with(
        user_id=user_id,
        kb_id=kb_id,
        document_id=document_id,
    )

    with Session(engine) as session:
        assert session.get(Document, document_id) is None


def test_other_user_cannot_delete_document(
    document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """其他用户不能通过知识库路径删除所有者的文档。"""
    client, engine, storage_root = document_api
    owner_id, kb_id, document_id, file_path = create_document_records(
        engine,
        storage_root,
    )
    del owner_id
    other_user = User(name="other")
    other_user_id = other_user.id
    with Session(engine) as session:
        session.add(other_user)
        session.commit()
    delete_chunks_mock = Mock()
    monkeypatch.setattr(
        document_service,
        "delete_document_chunks",
        delete_chunks_mock,
    )

    response = client.delete(
        f"/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=auth_headers(engine, other_user_id),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_FORBIDDEN"
    assert file_path.exists()
    delete_chunks_mock.assert_not_called()


def test_other_user_cannot_parse_document(
    document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """其他用户也不能调用文档解析接口。"""
    client, engine, storage_root = document_api
    _, kb_id, document_id, _ = create_document_records(
        engine,
        storage_root,
    )
    other_user = User(name="other")
    other_user_id = other_user.id
    with Session(engine) as session:
        session.add(other_user)
        session.commit()
    process_mock = Mock()
    monkeypatch.setattr(
        documents_router,
        "process_document",
        process_mock,
    )

    response = client.post(
        f"/knowledge-bases/{kb_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, other_user_id),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_FORBIDDEN"
    process_mock.assert_not_called()


def test_processing_document_cannot_be_deleted(
    document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PROCESSING 文档应返回 409，避免与解析任务并发删除。"""
    client, engine, storage_root = document_api
    user_id, kb_id, document_id, file_path = create_document_records(
        engine,
        storage_root,
        document_status=DocumentStatus.PROCESSING,
    )
    delete_chunks_mock = Mock()
    monkeypatch.setattr(
        document_service,
        "delete_document_chunks",
        delete_chunks_mock,
    )

    response = client.delete(
        f"/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DOCUMENT_PROCESSING"
    assert file_path.exists()
    delete_chunks_mock.assert_not_called()


def test_file_delete_failure_marks_document_delete_failed(
    document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原文件删除失败时应保留记录并写入 DELETE_FAILED。"""
    client, engine, storage_root = document_api
    user_id, kb_id, document_id, _ = create_document_records(
        engine,
        storage_root,
    )
    monkeypatch.setattr(
        document_service,
        "delete_document_chunks",
        Mock(return_value=2),
    )
    monkeypatch.setattr(
        document_service,
        "delete_stored_document_file",
        Mock(
            side_effect=AppError(
                500,
                "DOCUMENT_FILE_DELETE_FAILED",
                "文档原文件删除失败。",
            )
        ),
    )

    response = client.delete(
        f"/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DOCUMENT_FILE_DELETE_FAILED"
    with Session(engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.DELETE_FAILED
        assert document.error_message == "文档原文件删除失败。"


def test_chroma_delete_failure_marks_document_delete_failed(
    document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma 清理失败时应保留原文件和可重试的文档记录。"""
    client, engine, storage_root = document_api
    user_id, kb_id, document_id, file_path = create_document_records(
        engine,
        storage_root,
    )
    monkeypatch.setattr(
        document_service,
        "delete_document_chunks",
        Mock(
            side_effect=AppError(
                503,
                "VECTOR_UNAVAILABLE",
                "无法连接 Chroma 向量服务。",
            )
        ),
    )

    response = client.delete(
        f"/knowledge-bases/{kb_id}/documents/{document_id}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VECTOR_UNAVAILABLE"
    assert file_path.exists()
    with Session(engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.DELETE_FAILED


def test_chroma_delete_filter_contains_user_kb_and_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底层 Chroma 删除必须同时限制用户、知识库和文档。"""
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    kb_id = UUID("00000000-0000-0000-0000-000000000002")
    document_id = UUID("00000000-0000-0000-0000-000000000003")
    collection = Mock()
    collection.get.return_value = {"ids": ["chunk-a", "chunk-b"]}
    monkeypatch.setattr(vector_service, "get_chunk_collection", lambda: collection)

    deleted_count = vector_service.delete_document_chunks(
        user_id=user_id,
        kb_id=kb_id,
        document_id=document_id,
    )

    assert deleted_count == 2
    delete_filter = {
        "$and": [
            {"user_id": str(user_id)},
            {"kb_id": str(kb_id)},
            {"document_id": str(document_id)},
        ]
    }
    collection.get.assert_called_once_with(where=delete_filter, include=[])
    collection.delete.assert_called_once_with(where=delete_filter)
