"""验证 AV1-P11 正式检索 HTTP 契约和项目授权范围。"""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.core.errors import AppError
from app.models import Project
from app.schemas.archive_retrieval import ArchiveRetrievalResponse
from tests.routers.test_project_archive_catalog import _add_pending_document, _confirmed_document
from tests.routers.test_project_documents import create_user, project_document_api
from tests.support.auth import auth_headers


def test_archive_retrieval_route_passes_authenticated_project_scope(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由只从已验证的项目上下文调用正式检索服务。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)
    captured: dict[str, object] = {}

    def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return ArchiveRetrievalResponse(items=[], requested_top_k=3, returned_count=0)

    monkeypatch.setattr("app.routers.projects.retrieve_archive_chunks", fake_retrieve)
    response = client.post(
        f"/projects/{project_id}/archive-retrieval",
        headers=auth_headers(engine, user_id),
        json={"query": "项目阶段", "top_k": 3},
    )

    assert response.status_code == 200
    assert captured["user_id"] == user_id
    assert captured["project_id"] == project_id
    assert captured["query"] == "项目阶段"
    assert captured["top_k"] == 3
    with Session(engine) as session:
        project = session.get(Project, project_id)
    assert project is not None
    assert captured["kb_id"] == project.kb_id


def test_archive_retrieval_route_rejects_invalid_top_k(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """Top-K 超出约定范围时在调用服务前返回统一校验错误。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)

    response = client.post(
        f"/projects/{project_id}/archive-retrieval",
        headers=auth_headers(engine, user_id),
        json={"query": "项目阶段", "top_k": 11},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_archive_retrieval_route_maps_reranker_failure_to_stable_error(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地 Reranker 故障必须保持服务端稳定错误码，不能静默降级。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)

    def fail_retrieve(**kwargs):
        del kwargs
        raise AppError(503, "RERANKER_UNAVAILABLE", "本地 Reranker 推理失败。")

    monkeypatch.setattr("app.routers.projects.retrieve_archive_chunks", fail_retrieve)
    response = client.post(
        f"/projects/{project_id}/archive-retrieval",
        headers=auth_headers(engine, user_id),
        json={"query": "项目阶段", "top_k": 3},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RERANKER_UNAVAILABLE"
