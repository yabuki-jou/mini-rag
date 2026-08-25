"""验证 Chroma 客户端、cosine Collection 契约和写入元数据。"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.services.embedding_service import EmbeddedChunk
from app.services import vector_service


@pytest.fixture(autouse=True)
def clear_chroma_caches() -> None:
    """避免缓存的 HTTP 客户端跨用例保留 mock 或真实连接状态。"""
    vector_service.get_chroma_client.cache_clear()
    vector_service.get_chunk_collection.cache_clear()
    yield
    vector_service.get_chroma_client.cache_clear()
    vector_service.get_chunk_collection.cache_clear()


def test_ensure_chunk_collection_creates_cosine_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既有制度检索集合必须固定使用 cosine 距离且禁用默认 Embedding。"""
    collection = SimpleNamespace(
        name=settings.chroma_collection,
        configuration={"hnsw": {"space": "cosine"}},
    )
    client = Mock()
    client.get_or_create_collection.return_value = collection
    monkeypatch.setattr(vector_service, "get_chroma_client", lambda: client)

    assert vector_service.ensure_chunk_collection() == settings.chroma_collection
    client.get_or_create_collection.assert_called_once_with(
        name=settings.chroma_collection,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )


def test_chroma_client_uses_project_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """向量客户端必须显式连接项目 tenant/database，不能回退到服务端默认值。"""
    client = Mock()
    http_client = Mock(return_value=client)
    monkeypatch.setattr(vector_service.chromadb, "HttpClient", http_client)

    assert vector_service.get_chroma_client() is client
    http_client.assert_called_once_with(
        host=settings.chroma_host,
        port=settings.chroma_port,
        tenant=settings.chroma_tenant,
        database=settings.chroma_database,
    )


def test_ensure_chunk_collection_rejects_existing_wrong_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有同名集合不是 cosine 时必须失败，不能静默复用错误数据。"""
    collection = SimpleNamespace(
        name=settings.chroma_collection,
        configuration={"hnsw": {"space": "l2"}},
    )
    client = Mock()
    client.get_or_create_collection.return_value = collection
    monkeypatch.setattr(vector_service, "get_chroma_client", lambda: client)

    with pytest.raises(AppError) as exc_info:
        vector_service.ensure_chunk_collection()

    assert exc_info.value.code == "VECTOR_COLLECTION_CONFIG_INVALID"


def test_insert_chunks_writes_server_generated_scope_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写入元数据必须包含用户、知识库、文档和稳定引用位置。"""
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    kb_id = UUID("00000000-0000-0000-0000-000000000002")
    document_id = UUID("00000000-0000-0000-0000-000000000003")
    collection = Mock()
    monkeypatch.setattr(vector_service, "get_chunk_collection", lambda: collection)
    chunks = [
        EmbeddedChunk(
            chunk_id="a" * 64,
            page=1,
            start_index=0,
            chunk_index=0,
            content="制度正文",
            embedding=[0.1, 0.2],
        )
    ]

    assert vector_service.insert_chunks(
        user_id=user_id,
        kb_id=kb_id,
        document_id=document_id,
        document_name="policy.pdf",
        embedded_chunks=chunks,
    ) == 1

    kwargs = collection.upsert.call_args.kwargs
    assert kwargs["ids"] == ["a" * 64]
    assert kwargs["documents"] == ["制度正文"]
    assert kwargs["embeddings"] == [[0.1, 0.2]]
    assert kwargs["metadatas"] == [
        {
            "user_id": str(user_id),
            "kb_id": str(kb_id),
            "document_id": str(document_id),
            "document_name": "policy.pdf",
            "page": 1,
            "start_index": 0,
            "chunk_index": 0,
            "content_hash": "1ca3f1b3d5f2701dd6b509ba958f155d6b478d585b8b22b559759ec07d3de8fc",
        }
    ]


def test_chroma_heartbeat_maps_connection_failure_to_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """健康检查使用心跳，断连时只暴露稳定业务错误。"""
    client = Mock()
    client.heartbeat.side_effect = RuntimeError("internal host secret")
    monkeypatch.setattr(vector_service, "get_chroma_client", lambda: client)

    with pytest.raises(AppError) as exc_info:
        vector_service.check_chroma_connection()

    assert exc_info.value.code == "VECTOR_UNAVAILABLE"
    assert "internal host" not in exc_info.value.message
