"""验证 AV1-P11 正式检索的授权范围、阈值和结果转换。"""

from uuid import UUID, uuid4

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.core.errors import AppError
from app.models import ArchiveDocumentStatus, Project
from app.services import archive_retrieval_service
from tests.routers.test_project_archive_catalog import (
    _add_pending_document,
    _confirmed_document,
)
from tests.routers.test_project_documents import create_user, project_document_api
from tests.support.auth import auth_headers


class FakeEmbeddings:
    """返回固定查询向量，避免单测加载本地 BGE。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [0.1, 0.2]


class FakeCollection:
    """记录 Chroma 查询参数并返回列式结果。"""

    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _result(document_id: UUID, other_id: UUID) -> dict:
    return {
        "ids": [["a" * 64, "b" * 64]],
        "documents": [["正式证据", "待确认内容"]],
        "metadatas": [[
            {
                "document_id": str(document_id),
                "filename": "正式资料.docx",
                "location_type": "DOCX_PARAGRAPH",
                "location_start": 3,
                "location_end": 3,
            },
            {
                "document_id": str(other_id),
                "filename": "待确认资料.txt",
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 1,
                "location_end": 2,
            },
        ]],
        "distances": [[0.12, 0.05]],
    }


def test_retrieval_uses_formal_scope_and_filters_result_documents(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """检索必须使用正式文档集合、完整四重范围和结果二次范围校验。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, formal_id, _ = _confirmed_document(client, engine, user_id)
    pending_id = _add_pending_document(engine, project_id)
    collection = FakeCollection(_result(formal_id, pending_id))
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="正式证据是什么？",
            top_k=5,
            session=session,
        )

    assert [item.document_id for item in response.items] == [formal_id]
    where = collection.calls[0]["where"]
    assert {key for clause in where["$and"] for key in clause} >= {
        "user_id",
        "project_id",
        "kb_id",
        "document_id",
    }
    document_clause = next(clause for clause in where["$and"] if "document_id" in clause)
    assert document_clause["document_id"]["$in"] == [str(formal_id)]
    assert embeddings.queries == [
        "为这个句子生成表示以用于检索相关文章：正式证据是什么？"
    ]


def test_retrieval_applies_distance_threshold_and_returns_evidence_fields(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """距离超过冻结阈值的候选被过滤，并保留文件名、定位和摘录。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    collection = FakeCollection(
        {
            "ids": [["a" * 64, "b" * 64]],
            "documents": [["命中", "低相关"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 2,
                    "location_end": 2,
                },
                {
                    "document_id": str(document_id),
                    "filename": "资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 8,
                    "location_end": 8,
                },
            ]],
            "distances": [[0.1, 0.8]],
        }
    )
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(settings, "retrieval_distance_threshold", 0.2)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="命中",
            top_k=5,
            session=session,
        )

    assert response.returned_count == 1
    item = response.items[0]
    assert item.excerpt == "命中"
    assert item.location_type == "PDF_PAGE"
    assert item.location_start == item.location_end == 2
    assert item.score == pytest.approx(0.9)


def test_retrieval_returns_empty_without_formal_documents(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有正式档案时返回空结果，且不调用 Chroma。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={"name": "只有待确认资料", "description": None},
    ).json()
    project_id = UUID(project["id"])
    pending_id = _add_pending_document(engine, project_id)
    called = False

    def fail_collection():
        nonlocal called
        called = True
        raise AssertionError("no formal document must not query Chroma")

    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", fail_collection)
    with Session(engine) as session:
        project_row = session.get(Project, project_id)
        assert project_row is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project_row.kb_id,
            query="不存在",
            top_k=5,
            session=session,
        )

    assert response.items == []
    assert response.returned_count == 0
    assert called is False


def test_retrieval_records_eval_scope_and_final_items(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测观测必须保留服务端构造的范围和最终进入问答的候选。"""
    _, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(project_document_api[0], engine, user_id)
    collection = FakeCollection(_result(document_id, uuid4()))
    observed: list[tuple[object, dict[str, object]]] = []

    def capture(value: object, **kwargs: object) -> object:
        observed.append((value, kwargs))
        return value

    monkeypatch.setattr(archive_retrieval_service, "eval_wrap", capture)
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="正式证据是什么？",
            top_k=5,
            session=session,
        )

    observed_names = {str(kwargs["name"]) for _, kwargs in observed}
    assert {"archive_retrieval_scope", "archive_retrieval_result"} <= observed_names
    assert all(kwargs["purpose"] == "state" for _, kwargs in observed)
    observed_by_name = {str(kwargs["name"]): value for value, kwargs in observed}
    assert observed_by_name["archive_retrieval_scope"] == {
        "formal_document_count": 1,
        "scope_keys": ["user_id", "project_id", "kb_id", "document_id"],
    }
    assert observed_by_name["archive_retrieval_result"]["returned_count"] == 1
    assert observed_by_name["archive_retrieval_result"]["items"][0]["document_id"] == str(document_id)
