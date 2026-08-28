"""验证 AV1-P12 档案问答 HTTP 契约。"""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.schemas.archive_question import (
    ArchiveAnswerStatus,
    ArchiveQuestionResponse,
)
from tests.routers.test_project_archive_catalog import _confirmed_document
from tests.routers.test_project_documents import create_user, project_document_api
from tests.support.auth import auth_headers


def test_archive_question_route_forwards_project_context(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由把认证上下文注入问答服务，不接受客户端覆盖范围。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)
    captured: dict[str, object] = {}

    def fake_answer(**kwargs):
        captured.update(kwargs)
        return ArchiveQuestionResponse(
            answer_status=ArchiveAnswerStatus.REFUSED_NO_EVIDENCE,
            answer="正式档案中没有足够依据。",
            citations=[],
        )

    monkeypatch.setattr("app.routers.projects.answer_archive_question", fake_answer)
    response = client.post(
        f"/projects/{project_id}/archive-questions",
        headers=auth_headers(engine, user_id),
        json={"question": "编制单位是什么？"},
    )

    assert response.status_code == 200
    assert response.json()["answer_status"] == "REFUSED_NO_EVIDENCE"
    assert captured["user_id"] == user_id
    assert captured["project_id"] == project_id
    assert captured["question"] == "编制单位是什么？"
    assert captured["session"] is not None


def test_archive_question_route_rejects_blank_question(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """空问题在请求校验层拒绝，不进入问答服务。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)

    response = client.post(
        f"/projects/{project_id}/archive-questions",
        headers=auth_headers(engine, user_id),
        json={"question": "   "},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
