"""验证 AV1-P13 文档物理删除、失败隐藏和可重入恢复。"""

from pathlib import Path
from uuid import UUID

import pytest
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    ArchiveFieldValue,
    ChecklistLink,
    Document,
    DocumentStatus,
    ParsedSnapshot,
    Project,
)
from app.services import archive_document_delete_service
from tests.routers.test_project_archive_catalog import _confirmed_document, _create_checklist_item
from tests.routers.test_project_documents import create_user, project_document_api
from tests.support.auth import auth_headers


def _load_document(engine, document_id: UUID) -> Document:
    with Session(engine) as session:
        document = session.get(Document, document_id)
        assert document is not None
        return document


def test_delete_removes_archive_file_vector_and_link_but_keeps_redacted_audit(
    project_document_api: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除成功后跨存储记录全部清理，只保留脱敏删除审计。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, document_version = _confirmed_document(client, engine, user_id)
    item_id = _create_checklist_item(client, engine, user_id, project_id)
    link = client.post(
        f"/projects/{project_id}/documents/{document_id}/checklist-links",
        headers=auth_headers(engine, user_id),
        json={
            "checklist_item_id": str(item_id),
            "expected_document_version": document_version,
            "expected_checklist_item_version": 1,
        },
    )
    assert link.status_code == 201
    document = _load_document(engine, document_id)
    monkeypatch.setattr(archive_document_delete_service, "delete_final_chunks", lambda **_: 1)
    monkeypatch.setattr(archive_document_delete_service, "delete_stored_document_file", lambda _: None)

    with Session(engine) as session:
        archive_document_delete_service.delete_archive_document(
            document=document, actor_id=user_id, session=session
        )

    with Session(engine) as session:
        assert session.get(Document, document_id) is None
        assert session.get(ArchiveDocument, document_id) is None
        assert session.exec(select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)).all() == []
        assert session.exec(select(ArchiveFieldValue).where(ArchiveFieldValue.document_id == document_id)).all() == []
        assert session.exec(select(ChecklistLink).where(ChecklistLink.document_id == document_id)).all() == []
        assert session.exec(select(ArchiveOperation).where(ArchiveOperation.document_id == document_id)).all() == []
        audits = session.exec(
            select(ArchiveAuditLog).where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.resource_id == document_id,
            )
        ).all()
        project = session.get(Project, project_id)
    assert project is not None
    assert project.active_document_count == 0
    assert len(audits) == 1
    assert audits[0].operation_type == "DOCUMENT_DELETED"
    assert audits[0].redacted_summary == {}


def test_delete_failure_keeps_document_hidden_and_retry_resumes_cleanup(
    project_document_api: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma 失败返回稳定错误，保留 FAILED 删除操作并可重试完成。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    document = _load_document(engine, document_id)
    monkeypatch.setattr(
        archive_document_delete_service,
        "delete_final_chunks",
        lambda **_: (_ for _ in ()).throw(AppError(503, "VECTOR_UNAVAILABLE", "不可用")),
    )
    monkeypatch.setattr(archive_document_delete_service, "delete_stored_document_file", lambda _: None)

    with pytest.raises(AppError) as exc_info:
        with Session(engine) as session:
            archive_document_delete_service.delete_archive_document(
                document=document, actor_id=user_id, session=session
            )
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "DOCUMENT_DELETE_INCOMPLETE"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        operation = session.exec(
            select(ArchiveOperation).where(ArchiveOperation.document_id == document_id)
        ).one()
        failed_document = session.get(Document, document_id)
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.CONFIRMED
    assert operation.operation_type == ArchiveOperationType.DELETE
    assert operation.operation_status == ArchiveOperationStatus.FAILED
    assert operation.visibility_blocking is True
    assert failed_document is not None
    assert failed_document.status == DocumentStatus.DELETE_FAILED

    monkeypatch.setattr(archive_document_delete_service, "delete_final_chunks", lambda **_: 0)
    with Session(engine) as session:
        retry_document = session.get(Document, document_id)
        assert retry_document is not None
        archive_document_delete_service.delete_archive_document(
            document=retry_document, actor_id=user_id, session=session
        )
    with Session(engine) as session:
        assert session.get(Document, document_id) is None
        assert session.exec(
            select(ArchiveAuditLog).where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.resource_id == document_id,
                ArchiveAuditLog.operation_type == "DOCUMENT_DELETED",
            )
        ).all()


def test_delete_records_external_progress_and_final_outcome(
    project_document_api: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测观测必须区分跨存储外部步骤与最终删除结果。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    _, document_id, _ = _confirmed_document(client, engine, user_id)
    document = _load_document(engine, document_id)
    observed: list[tuple[object, dict[str, object]]] = []

    def capture(value: object, **kwargs: object) -> object:
        observed.append((value, kwargs))
        return value

    monkeypatch.setattr(archive_document_delete_service, "eval_wrap", capture)
    monkeypatch.setattr(archive_document_delete_service, "delete_final_chunks", lambda **_: 1)
    monkeypatch.setattr(archive_document_delete_service, "delete_stored_document_file", lambda _: None)

    with Session(engine) as session:
        archive_document_delete_service.delete_archive_document(
            document=document, actor_id=user_id, session=session
        )

    observed_names = {str(kwargs["name"]) for _, kwargs in observed}
    assert {"archive_delete_external_result", "archive_delete_outcome"} <= observed_names
    assert all(kwargs["purpose"] in {"state", "output"} for _, kwargs in observed)
    observed_by_name = {str(kwargs["name"]): value for value, kwargs in observed}
    assert observed_by_name["archive_delete_external_result"] == {
        "step": "FILE_DELETE",
        "status": "completed",
    }
    assert observed_by_name["archive_delete_outcome"] == {"outcome": "deleted"}
