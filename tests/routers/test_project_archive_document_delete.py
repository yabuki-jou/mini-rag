"""验证 AV1-P13 物理删除路由和稳定失败码。"""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.core.errors import AppError
from app.models import ArchiveDocument, Document, ParsedSnapshot
from tests.routers.test_project_archive_catalog import _confirmed_document
from tests.routers.test_project_documents import create_user, project_document_api
from tests.support.auth import auth_headers


def test_delete_document_route_returns_no_content(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除路由将认证用户和项目文档交给物理删除服务。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    captured: dict[str, object] = {}

    def fake_delete(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.routers.projects.delete_archive_document", fake_delete)
    response = client.delete(
        f"/projects/{project_id}/documents/{document_id}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 204
    assert captured["actor_id"] == user_id
    assert captured["document"].id == document_id


def test_delete_document_route_preserves_stable_incomplete_error(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨存储失败只返回 DOCUMENT_DELETE_INCOMPLETE，不暴露内部细节。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)

    def fail_delete(**_):
        raise AppError(503, "DOCUMENT_DELETE_INCOMPLETE", "文档删除未完成，可稍后重试。")

    monkeypatch.setattr("app.routers.projects.delete_archive_document", fail_delete)
    response = client.delete(
        f"/projects/{project_id}/documents/{document_id}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DOCUMENT_DELETE_INCOMPLETE"
    assert "VECTOR_UNAVAILABLE" not in response.text


def test_delete_document_route_rejects_other_user_without_mutation(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """其他用户不能删除项目文档，且删除服务和持久化事实均保持不变。"""
    client, engine, _ = project_document_api
    owner_id = create_user(engine)
    other_user_id = create_user(engine, "delete-other")
    project_id, document_id, _ = _confirmed_document(client, engine, owner_id)

    with Session(engine) as session:
        document = session.get(Document, document_id)
        archive_document = session.get(ArchiveDocument, document_id)
        assert document is not None
        assert archive_document is not None
        assert archive_document.current_snapshot_id is not None
        snapshot = session.get(ParsedSnapshot, archive_document.current_snapshot_id)
        assert snapshot is not None
        original_path = Path(document.storage_path)
        snapshot_path = Path(snapshot.snapshot_storage_path)
        assert original_path.is_file()
        assert snapshot_path.is_file()

    delete_called = False

    def fail_if_called(**_: object) -> None:
        nonlocal delete_called
        delete_called = True

    monkeypatch.setattr("app.routers.projects.delete_archive_document", fail_if_called)
    response = client.delete(
        f"/projects/{project_id}/documents/{document_id}",
        headers=auth_headers(engine, other_user_id),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"
    assert delete_called is False

    with Session(engine) as session:
        assert session.get(Document, document_id) is not None
        assert session.get(ArchiveDocument, document_id) is not None
        assert session.get(ParsedSnapshot, archive_document.current_snapshot_id) is not None
    assert original_path.is_file()
    assert snapshot_path.is_file()
