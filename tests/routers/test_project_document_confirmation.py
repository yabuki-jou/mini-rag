"""验证 AV1-P09 人工确认前置条件、状态转换和脱敏审计契约。"""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    FieldSource,
)
from app.services import archive_confirmation_service
from app.services import archive_cancel_confirmation_service
from tests.routers.test_project_document_draft import _parsed_document, create_user
from tests.routers.test_project_documents import project_document_api
from tests.support.auth import auth_headers


@pytest.fixture(autouse=True)
def mock_confirm_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离确认路由测试，避免未启动的真实 Chroma 影响状态机断言。"""
    monkeypatch.setattr(
        archive_confirmation_service,
        "index_confirmed_document",
        lambda **_: None,
    )
    monkeypatch.setattr(
        archive_cancel_confirmation_service,
        "delete_final_chunks",
        lambda **_: 0,
    )


def _start_draft(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
) -> tuple[UUID, UUID, int]:
    """创建已解析文档并启动人工草稿。"""
    project_id, document_id = _parsed_document(client, engine, user_id)
    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/manual-draft",
        headers=auth_headers(engine, user_id),
    )
    assert response.status_code == 200
    return project_id, document_id, response.json()["document"]["version"]


def _confirm_all_fields(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
    project_id: UUID,
    document_id: UUID,
    version: int,
) -> int:
    """逐个保存七个已检查字段并返回最新版本。"""
    fields = (
        ("TITLE", {"text_value": "施工方案"}),
        ("DOCUMENT_TYPE", {"text_value": "CONSTRUCTION"}),
        ("DOCUMENT_DATE", {"date_value": "2026-08-26"}),
        ("AUTHORING_ORGANIZATION", {"text_value": "示例建设公司"}),
        ("VERSION_NUMBER", {"text_value": "V1.0"}),
        ("PROJECT_STAGE", {"text_value": "CONSTRUCTION"}),
        ("KEYWORDS", {"json_value": ["施工", "方案"]}),
    )
    for field_name, value in fields:
        response = client.put(
            f"/projects/{project_id}/documents/{document_id}/fields/{field_name}",
            headers=auth_headers(engine, user_id),
            json={
                **value,
                "review_status": "VALUE_CONFIRMED",
                "no_source_evidence": True,
                "expected_version": version,
            },
        )
        assert response.status_code == 200
        version = response.json()["document"]["version"]
    return version


def test_confirm_rejects_unchecked_fields_without_mutating_document(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """存在待检查字段时不能确认，状态和版本保持不变。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFIRM_NOT_ALLOWED"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_CONFIRMATION
    assert archive_document.version == version


def test_confirm_transitions_document_and_writes_redacted_audit(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """七字段检查通过后应确认、递增版本并写入不含字段正文的审计。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "CONFIRMED"
    assert payload["confirmed_at"] is not None
    assert payload["version"] == version + 1
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        audits = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.resource_id == document_id,
                    ArchiveAuditLog.operation_type == "ARCHIVE_CONFIRMED",
                )
            ).all()
        )
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.CONFIRMED
    assert archive_document.confirmed_by == user_id
    assert len(archive_document.final_index_snapshot_hash or "") == 64
    assert len(audits) == 1
    assert audits[0].actor_id == user_id
    assert audits[0].redacted_summary == {"status": "CONFIRMED"}


def test_confirm_triggers_internal_index_after_state_transition(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认成功后必须调用内部 INDEX 编排服务。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )
    calls: dict[str, UUID] = {}

    def fake_index(*, document, session) -> None:
        calls["document_id"] = document.id

    monkeypatch.setattr(
        archive_confirmation_service,
        "index_confirmed_document",
        fake_index,
        raising=False,
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )

    assert response.status_code == 200
    assert calls == {"document_id": document_id}


def test_confirm_returns_index_failure_instead_of_success(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INDEX 失败时确认接口不能伪装成成功响应。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )

    def fail_index(*, document, session) -> None:
        raise AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。")

    monkeypatch.setattr(
        archive_confirmation_service,
        "index_confirmed_document",
        fail_index,
        raising=False,
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VECTOR_UNAVAILABLE"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.CONFIRMED
    assert archive_document.final_chunk_count == 0


def test_confirm_retries_index_for_confirmed_document_without_final_chunks(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认后的 INDEX 失败时，同版本确认可重试而非直接吞掉失败。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )
    calls = 0

    def fail_once(*, document, session) -> None:
        nonlocal calls
        calls += 1
        raise AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。")

    monkeypatch.setattr(archive_confirmation_service, "index_confirmed_document", fail_once)
    first = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )
    assert first.status_code == 503
    confirmed_version = version + 1

    def succeed(*, document, session) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(archive_confirmation_service, "index_confirmed_document", succeed)
    second = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": confirmed_version},
    )

    assert second.status_code == 200
    assert calls == 2


def test_confirm_is_idempotent_for_current_confirmed_version(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """当前版本重复确认只返回当前结果，不重复递增版本或写成功审计。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )
    first = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )
    assert first.status_code == 200
    confirmed_version = first.json()["version"]

    second = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": confirmed_version},
    )

    assert second.status_code == 200
    assert second.json()["version"] == confirmed_version
    with Session(engine) as session:
        audits = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.resource_id == document_id,
                    ArchiveAuditLog.operation_type == "ARCHIVE_CONFIRMED",
                )
            ).all()
        )
    assert len(audits) == 1


def test_confirm_rejects_stale_version_before_state_change(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """确认请求使用旧版本时返回稳定冲突且不写审计。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    current_version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": current_version - 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        audits = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.resource_id == document_id,
                    ArchiveAuditLog.operation_type == "ARCHIVE_CONFIRMED",
                )
            ).all()
        )
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_CONFIRMATION
    assert archive_document.version == current_version
    assert audits == []


def test_confirm_rejects_nonempty_ai_field_without_current_snapshot_evidence(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """AI 非空字段缺少当前快照证据时不能进入 CONFIRMED。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )
    with Session(engine) as session:
        field = session.exec(
            select(ArchiveFieldValue).where(
                ArchiveFieldValue.document_id == document_id,
                ArchiveFieldValue.field_name == ArchiveFieldName.AUTHORING_ORGANIZATION,
            )
        ).one()
        field.source = FieldSource.AI
        field.no_source_evidence = False
        session.add(field)
        session.commit()

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AI_FIELD_EVIDENCE_REQUIRED"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_CONFIRMATION
    assert archive_document.version == version


def test_cancel_confirmation_moves_document_out_of_formal_scope_and_audits(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """已确认档案取消确认后应退出正式范围、清空索引事实并写脱敏审计。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )
    confirm_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )
    assert confirm_response.status_code == 200
    confirmed_version = confirm_response.json()["version"]
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        assert archive_document is not None
        archive_document.final_chunk_count = 1
        session.add(archive_document)
        session.commit()

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/cancel-confirmation",
        headers=auth_headers(engine, user_id),
        json={"expected_version": confirmed_version},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING_RECONFIRMATION"
    assert response.json()["version"] == confirmed_version + 1
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        audits = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.resource_id == document_id,
                    ArchiveAuditLog.operation_type == "ARCHIVE_CONFIRMATION_CANCELLED",
                )
            ).all()
        )
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_RECONFIRMATION
    assert archive_document.confirmed_by is None
    assert archive_document.confirmed_at is None
    assert archive_document.final_index_snapshot_hash is None
    assert archive_document.final_chunk_count == 0
    assert len(audits) == 1
    assert audits[0].redacted_summary == {"status": "PENDING_RECONFIRMATION"}


def test_cancel_confirmation_persists_safe_cleanup_failure(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma 清理异常时应保持非正式状态并返回不泄露内部细节的错误。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, version = _start_draft(client, engine, user_id)
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, version
    )
    confirm_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/confirm",
        headers=auth_headers(engine, user_id),
        json={"expected_version": version},
    )
    assert confirm_response.status_code == 200
    confirmed_version = confirm_response.json()["version"]

    def fail_cleanup(**_: object) -> int:
        raise RuntimeError("internal-chroma-endpoint token=do-not-leak")

    monkeypatch.setattr(
        archive_cancel_confirmation_service,
        "delete_final_chunks",
        fail_cleanup,
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/cancel-confirmation",
        headers=auth_headers(engine, user_id),
        json={"expected_version": confirmed_version},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "VECTOR_UNAVAILABLE",
        "message": "无法连接 Chroma 向量服务。",
    }
    assert "internal-chroma-endpoint" not in response.text
    assert "do-not-leak" not in response.text
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_RECONFIRMATION
    assert archive_document.version == confirmed_version + 1
    assert archive_document.last_error_code == "VECTOR_UNAVAILABLE"
    assert archive_document.last_error_summary == "无法连接 Chroma 向量服务。"
