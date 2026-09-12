"""验证 AV1-P10 的文档处理列表、正式目录、清单关联和审计查询契约。"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    ChecklistItem,
    ChecklistLink,
    Document,
    DocumentStatus,
    ParsedSnapshot,
    Project,
    utc_now,
)
from tests.routers.test_project_document_confirmation import _confirm_all_fields
from tests.routers.test_project_document_draft import _parsed_document
from tests.routers.test_project_documents import create_project, create_user, project_document_api
from tests.support.auth import auth_headers


def _confirmed_document(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
) -> tuple[UUID, UUID, int]:
    """创建一份七字段已检查、但不触发真实 INDEX 的确认档案。"""
    project_id, document_id = _parsed_document(client, engine, user_id)
    draft_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/manual-draft",
        headers=auth_headers(engine, user_id),
    )
    assert draft_response.status_code == 200
    version = _confirm_all_fields(
        client, engine, user_id, project_id, document_id, draft_response.json()["document"]["version"]
    )
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        assert archive_document is not None
        snapshot = session.get(ParsedSnapshot, archive_document.current_snapshot_id)
        assert snapshot is not None
        archive_document.status = ArchiveDocumentStatus.CONFIRMED
        archive_document.confirmed_by = user_id
        archive_document.confirmed_at = utc_now()
        archive_document.final_index_snapshot_hash = snapshot.snapshot_hash
        archive_document.final_chunk_count = 1
        session.add(archive_document)
        session.commit()
    return project_id, document_id, version


def _add_pending_document(engine: Engine, project_id: UUID) -> UUID:
    """向同一项目加入一份待处理文档，覆盖处理列表与目录隔离。"""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        document = Document(
            kb_id=project.kb_id,
            project_id=project.id,
            filename="待确认资料.txt",
            storage_path="tests/pytest_docs/pending.txt",
            file_hash=uuid4().hex + "0" * 32,
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        session.flush()
        session.add(
            ArchiveDocument(
                document_id=document.id,
                status=ArchiveDocumentStatus.UPLOADED,
            )
        )
        session.commit()
        return document.id


def _create_checklist_item(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
    project_id: UUID,
) -> UUID:
    """通过公开 API 创建与测试档案类型/阶段匹配的清单项。"""
    response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, user_id),
        json={
            "name": "施工方案",
            "document_type": "CONSTRUCTION",
            "is_required": True,
            "project_stage": "CONSTRUCTION",
            "expected_project_version": 1,
        },
    )
    assert response.status_code == 201
    return UUID(response.json()["item"]["id"])


def test_processing_list_and_formal_archive_detail_are_separate(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """处理列表包含待确认文档，而正式目录和详情只暴露 CONFIRMED。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, confirmed_document_id, _ = _confirmed_document(client, engine, user_id)
    pending_document_id = _add_pending_document(engine, project_id)

    processing = client.get(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
    )
    archives = client.get(
        f"/projects/{project_id}/archives",
        headers=auth_headers(engine, user_id),
    )
    pending_detail = client.get(
        f"/projects/{project_id}/archives/{pending_document_id}",
        headers=auth_headers(engine, user_id),
    )
    formal_detail = client.get(
        f"/projects/{project_id}/archives/{confirmed_document_id}",
        headers=auth_headers(engine, user_id),
    )

    assert processing.status_code == 200
    assert {item["id"] for item in processing.json()["items"]} == {
        str(confirmed_document_id),
        str(pending_document_id),
    }
    assert archives.status_code == 200
    assert [item["id"] for item in archives.json()["items"]] == [str(confirmed_document_id)]
    assert pending_detail.status_code == 404
    assert pending_detail.json()["error"]["code"] == "ARCHIVE_NOT_FORMAL"
    assert formal_detail.status_code == 200
    assert formal_detail.json()["status"] == "CONFIRMED"
    assert len(formal_detail.json()["fields"]) == 7


def test_formal_archive_excludes_visibility_blocked_document_and_supports_filters(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """删除操作的可见性阻断和结构化筛选必须立即生效。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        assert archive_document is not None
        project = session.get(Project, project_id)
        assert project is not None
        session.add(
            ArchiveOperation(
                document_id=document_id,
                operation_type=ArchiveOperationType.DELETE,
                operation_status=ArchiveOperationStatus.RUNNING,
                visibility_blocking=True,
            )
        )
        session.commit()

    archives = client.get(
        f"/projects/{project_id}/archives?document_type=CONSTRUCTION&project_stage=CONSTRUCTION",
        headers=auth_headers(engine, user_id),
    )
    assert archives.status_code == 200
    assert archives.json()["items"] == []


def test_checklist_link_suggestions_confirm_delete_and_audit(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """建议不自动满足，人工确认关联后满足，删除关联后恢复缺失并审计。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, document_version = _confirmed_document(client, engine, user_id)
    item_id = _create_checklist_item(client, engine, user_id, project_id)

    suggestions = client.get(
        f"/projects/{project_id}/documents/{document_id}/checklist-link-suggestions",
        headers=auth_headers(engine, user_id),
    )
    assert suggestions.status_code == 200
    assert suggestions.json()["items"][0]["checklist_item_id"] == str(item_id)

    create_link = client.post(
        f"/projects/{project_id}/documents/{document_id}/checklist-links",
        headers=auth_headers(engine, user_id),
        json={
            "checklist_item_id": str(item_id),
            "expected_document_version": document_version,
            "expected_checklist_item_version": 1,
        },
    )
    assert create_link.status_code == 201
    link_id = UUID(create_link.json()["id"])

    checklist_after_create = client.get(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, user_id),
    )
    assert checklist_after_create.json()["items"][0]["fulfillment_status"] == "SATISFIED"

    links = client.get(
        f"/projects/{project_id}/documents/{document_id}/checklist-links",
        headers=auth_headers(engine, user_id),
    )
    assert links.status_code == 200
    assert links.json()["items"][0]["status"] == "CONFIRMED"

    delete_link = client.delete(
        f"/projects/{project_id}/documents/{document_id}/checklist-links/{link_id}",
        headers=auth_headers(engine, user_id),
    )
    assert delete_link.status_code == 204
    checklist_after_delete = client.get(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, user_id),
    )
    assert checklist_after_delete.json()["items"][0]["fulfillment_status"] == "MISSING"
    with Session(engine) as session:
        logs = list(
            session.exec(
                select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project_id)
            ).all()
        )
    assert {log.operation_type for log in logs} >= {
        "CHECKLIST_ITEM_CREATED",
        "CHECKLIST_LINK_CONFIRMED",
        "CHECKLIST_LINK_DELETED",
    }
    assert all("施工方案" not in str(log.redacted_summary) for log in logs)


def test_checklist_link_rejects_nonconfirmed_document_and_cross_project_item(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """待确认档案和其他项目清单项都不能建立关联。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)
    other_project = create_project(client, engine, user_id, name="另一个关联项目")
    other_project_id = UUID(str(other_project["id"]))
    item_id = _create_checklist_item(client, engine, user_id, other_project_id)
    pending_document_id = _add_pending_document(engine, project_id)

    response = client.post(
        f"/projects/{project_id}/documents/{pending_document_id}/checklist-links",
        headers=auth_headers(engine, user_id),
        json={
            "checklist_item_id": str(item_id),
            "expected_document_version": 1,
            "expected_checklist_item_version": 2,
        },
    )
    assert response.status_code in {404, 409}
    assert response.json()["error"]["code"] in {
        "CHECKLIST_ITEM_NOT_FOUND",
        "CHECKLIST_LINK_NOT_ALLOWED",
    }


def test_audit_log_list_is_project_scoped_and_redacted(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """审计查询只返回当前项目的脱敏记录。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)
    with Session(engine) as session:
        session.add(
            ArchiveAuditLog(
                project_id=project_id,
                actor_id=user_id,
                operation_type="ARCHIVE_CONFIRMED",
                resource_type="ARCHIVE_DOCUMENT",
                resource_id=uuid4(),
                redacted_summary={"status": "CONFIRMED"},
            )
        )
        session.commit()

    response = client.get(
        f"/projects/{project_id}/audit-logs",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 200
    assert response.json()["total"] >= 1
    assert all(item["actor_id"] == str(user_id) for item in response.json()["items"])
    assert all(item["created_at"] for item in response.json()["items"])
    assert all(item["resource_type"] and item["resource_id"] for item in response.json()["items"])
    assert all("施工方案" not in str(item["redacted_summary"]) for item in response.json()["items"])


def test_audit_log_list_rejects_other_users_without_leaking_records(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """项目审计必须沿用项目归属校验，拒绝其他用户并不返回摘要。"""
    client, engine, _ = project_document_api
    owner_id = create_user(engine, "audit-owner")
    other_user_id = create_user(engine, "audit-other")
    project_id, _, _ = _confirmed_document(client, engine, owner_id)

    response = client.get(
        f"/projects/{project_id}/audit-logs",
        headers=auth_headers(engine, other_user_id),
    )

    assert response.status_code == 403
    assert "redacted_summary" not in response.text


def test_audit_log_filter_rejects_unknown_operation_type(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """审计筛选只接受 API 契约中声明的受控操作类型。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, _, _ = _confirmed_document(client, engine, user_id)

    response = client.get(
        f"/projects/{project_id}/audit-logs?operation_type=NOT_A_REAL_OPERATION",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 422
