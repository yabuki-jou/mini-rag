"""验证 Chroma 距离语义、数据隔离、结果转换和安全异常。"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.services import retrieval_service


def make_query_result(
    records: list[tuple[str, UUID, int, str, float]],
) -> dict[str, list[list[object]]]:
    """构造单问题 Chroma 查询的列式响应。"""
    return {
        "ids": [[record[0] for record in records]],
        "documents": [[record[3] for record in records]],
        "metadatas": [
            [
                {
                    "document_id": str(record[1]),
                    "document_name": "policy.pdf",
                    "page": record[2],
                }
                for record in records
            ]
        ],
        "distances": [[record[4] for record in records]],
    }


def test_build_retrieval_filter_contains_user_and_kb() -> None:
    """范围过滤必须同时限制用户和知识库。"""
    user_id = uuid4()
    kb_id = uuid4()

    assert retrieval_service.build_retrieval_filter(user_id, kb_id) == {
        "$and": [
            {"user_id": str(user_id)},
            {"kb_id": str(kb_id)},
        ]
    }


def test_retrieve_chunks_orders_by_distance_and_keeps_api_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma cosine distance 越小越相关，旧 score 字段返回 1-distance。"""
    user_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    collection = Mock()
    collection.query.return_value = make_query_result(
        [
            ("a" * 64, document_id, 1, "较远", 0.50),
            ("b" * 64, document_id, 2, "最近", 0.10),
            ("c" * 64, document_id, 3, "更远", 0.51),
            ("d" * 64, document_id, 4, "第二近", 0.20),
        ]
    )
    embeddings = SimpleNamespace(embed_query=Mock(return_value=[1.0, 0.0]))

    monkeypatch.setattr(settings, "retrieval_top_k", 10)
    monkeypatch.setattr(settings, "retrieval_top_n", 2)
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)
    monkeypatch.setattr(retrieval_service, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(retrieval_service, "get_chunk_collection", lambda: collection)

    results = retrieval_service.retrieve_chunks(user_id, kb_id, "测试问题")

    assert [result.chunk_id for result in results] == ["b" * 64, "d" * 64]
    assert [result.score for result in results] == [0.90, 0.80]
    assert collection.query.call_args.kwargs == {
        "query_embeddings": [[1.0, 0.0]],
        "n_results": 10,
        "where": {
            "$and": [
                {"user_id": str(user_id)},
                {"kb_id": str(kb_id)},
            ]
        },
        "include": ["documents", "metadatas", "distances"],
    }


def test_retrieve_chunks_applies_distance_threshold_when_calibrated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置阈值后，距离等于上限保留，较大的距离必须过滤。"""
    document_id = uuid4()
    collection = Mock()
    collection.query.return_value = make_query_result(
        [
            ("a" * 64, document_id, 1, "阈值边界", 0.50),
            ("b" * 64, document_id, 2, "超过阈值", 0.51),
        ]
    )
    monkeypatch.setattr(settings, "retrieval_distance_threshold", 0.50)
    monkeypatch.setattr(settings, "retrieval_top_n", 3)
    monkeypatch.setattr(
        retrieval_service,
        "get_embeddings",
        lambda: SimpleNamespace(embed_query=lambda _: [1.0, 0.0]),
    )
    monkeypatch.setattr(retrieval_service, "get_chunk_collection", lambda: collection)

    results = retrieval_service.retrieve_chunks(uuid4(), uuid4(), "边界测试")

    assert [result.chunk_id for result in results] == ["a" * 64]
    assert results[0].score == 0.50


def test_retrieve_chunks_maps_chroma_failure_to_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma 断连不能把内部地址或异常直接返回客户端。"""
    collection = Mock()
    collection.query.side_effect = RuntimeError("http://internal-chroma:8000 secret")
    monkeypatch.setattr(
        retrieval_service,
        "get_embeddings",
        lambda: SimpleNamespace(embed_query=lambda _: [1.0, 0.0]),
    )
    monkeypatch.setattr(retrieval_service, "get_chunk_collection", lambda: collection)

    with pytest.raises(AppError) as exc_info:
        retrieval_service.retrieve_chunks(uuid4(), uuid4(), "异常测试")

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "VECTOR_UNAVAILABLE"
    assert "internal-chroma" not in exc_info.value.message


def test_retrieve_chunks_rejects_invalid_result_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma 列式响应长度不一致时不能拼错引用与正文。"""
    collection = Mock()
    collection.query.return_value = {
        "ids": [["a" * 64]],
        "documents": [["正文"]],
        "metadatas": [[]],
        "distances": [[0.1]],
    }
    monkeypatch.setattr(
        retrieval_service,
        "get_embeddings",
        lambda: SimpleNamespace(embed_query=lambda _: [1.0, 0.0]),
    )
    monkeypatch.setattr(retrieval_service, "get_chunk_collection", lambda: collection)

    with pytest.raises(AppError) as exc_info:
        retrieval_service.retrieve_chunks(uuid4(), uuid4(), "结构异常")

    assert exc_info.value.code == "VECTOR_RESULT_INVALID"
