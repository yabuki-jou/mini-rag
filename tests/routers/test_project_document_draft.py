"""验证 AV1-P07 人工草稿与字段检查 HTTP 契约。"""

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    FieldReviewStatus,
    ParsedSnapshot,
)
from tests.routers.test_project_documents import (
    create_project,
    create_user,
    project_document_api,
)
from tests.support.auth import auth_headers


def _parsed_document(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
) -> tuple[UUID, UUID]:
    """创建一份已解析项目文档，供 P07 路由测试复用。"""
    project_payload = create_project(client, engine, user_id, name="人工草稿测试工程")
    project_id = UUID(str(project_payload["id"]))
    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={
            "file": (
                "施工方案.txt",
                "项目施工方案正文，包含足够的有效文本用于人工草稿测试。".encode(),
                "text/plain",
            )
        },
    )
    assert upload_response.status_code == 201
    document_id = UUID(upload_response.json()["id"])
    parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )
    assert parse_response.status_code == 200
    return project_id, document_id


def test_manual_draft_creates_seven_pending_fields(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """已解析文档启动人工草稿后应生成七个空的待检查字段。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/manual-draft",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document"]["status"] == "PENDING_CONFIRMATION"
    assert payload["document"]["version"] == 2
    assert payload["document"]["field_summary"] == {
        "checked_count": 0,
        "total_count": 7,
    }
    assert len(payload["fields"]) == 7
    assert {field["field_name"] for field in payload["fields"]} == {
        field.value for field in ArchiveFieldName
    }
    assert all(field["review_status"] == "PENDING_CHECK" for field in payload["fields"])
    assert all(field["source"] is None for field in payload["fields"])
    assert all(field["evidences"] == [] for field in payload["fields"])

    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        field_values = list(
            session.exec(
                select(ArchiveFieldValue).where(
                    ArchiveFieldValue.document_id == document_id
                )
            ).all()
        )
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_CONFIRMATION
    assert archive_document.version == 2
    assert len(field_values) == 7
    assert all(value.review_status == FieldReviewStatus.PENDING_CHECK for value in field_values)
    assert all(value.source is None for value in field_values)


def test_manual_draft_can_read_and_save_manual_field_without_evidence(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """人工字段值可显式声明无原文证据，并递增文档版本。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    draft_url = f"/projects/{project_id}/documents/{document_id}/manual-draft"
    start_response = client.post(draft_url, headers=auth_headers(engine, user_id))
    assert start_response.status_code == 200
    version = start_response.json()["document"]["version"]

    response = client.put(
        f"/projects/{project_id}/documents/{document_id}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "施工方案",
            "date_value": None,
            "json_value": None,
            "review_status": "VALUE_CONFIRMED",
            "source": "MANUAL",
            "no_source_evidence": True,
            "evidences": [],
            "reason": "原文未提供标题，人工补录",
            "expected_version": version,
        },
    )

    assert response.status_code == 200
    field = next(
        item for item in response.json()["fields"] if item["field_name"] == "TITLE"
    )
    assert field["field_name"] == "TITLE"
    assert field["text_value"] == "施工方案"
    assert field["review_status"] == "VALUE_CONFIRMED"
    assert field["source"] == "MANUAL"
    assert field["no_source_evidence"] is True
    assert field["evidences"] == []
    assert response.json()["document"]["version"] == version + 1
    with Session(engine) as session:
        audit_logs = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.project_id == project_id,
                    ArchiveAuditLog.operation_type == "ARCHIVE_FIELD_UPDATED",
                )
            ).all()
        )
    assert len(audit_logs) == 1
    assert audit_logs[0].actor_id == user_id
    assert audit_logs[0].redacted_summary == {
        "field_name": "TITLE",
        "review_status": "VALUE_CONFIRMED",
    }


def test_field_update_rejects_forged_ai_source_and_stale_version(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """客户端不能伪造 AI 来源，陈旧版本必须返回稳定冲突。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    draft_url = f"/projects/{project_id}/documents/{document_id}/manual-draft"
    start_response = client.post(draft_url, headers=auth_headers(engine, user_id))
    assert start_response.status_code == 200
    version = start_response.json()["document"]["version"]

    forged_ai = client.put(
        f"/projects/{project_id}/documents/{document_id}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "模型标题",
            "review_status": "VALUE_CONFIRMED",
            "source": "AI",
            "no_source_evidence": False,
            "evidences": [],
            "expected_version": version,
        },
    )
    assert forged_ai.status_code == 409
    assert forged_ai.json()["error"]["code"] == "AI_SOURCE_FORBIDDEN"

    stale = client.put(
        f"/projects/{project_id}/documents/{document_id}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "施工方案",
            "review_status": "VALUE_CONFIRMED",
            "source": "MANUAL",
            "no_source_evidence": True,
            "evidences": [],
            "expected_version": version - 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_read_draft_returns_snapshot_and_current_field_summary(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """草稿读取必须返回当前快照元数据和已检查字段汇总。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    base_url = f"/projects/{project_id}/documents/{document_id}"
    start_response = client.post(
        f"{base_url}/manual-draft", headers=auth_headers(engine, user_id)
    )
    assert start_response.status_code == 200
    version = start_response.json()["document"]["version"]

    update_response = client.put(
        f"{base_url}/fields/DOCUMENT_DATE",
        headers=auth_headers(engine, user_id),
        json={
            "date_value": "2026-08-26",
            "review_status": "VALUE_CONFIRMED",
            "no_source_evidence": True,
            "expected_version": version,
        },
    )
    assert update_response.status_code == 200

    read_response = client.get(f"{base_url}/draft", headers=auth_headers(engine, user_id))
    assert read_response.status_code == 200
    payload = read_response.json()
    assert payload["document"]["field_summary"] == {"checked_count": 1, "total_count": 7}
    assert payload["snapshot"]["snapshot_hash"]
    date_field = next(item for item in payload["fields"] if item["field_name"] == "DOCUMENT_DATE")
    assert date_field["date_value"] == "2026-08-26"


def test_manual_draft_can_fallback_from_suggestion_failed(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """建议失败状态仍可进入不依赖模型的人工草稿。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        assert archive_document is not None
        archive_document.status = ArchiveDocumentStatus.SUGGESTION_FAILED
        session.add(archive_document)
        session.commit()

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/manual-draft",
        headers=auth_headers(engine, user_id),
    )
    assert response.status_code == 200
    assert response.json()["document"]["status"] == "PENDING_CONFIRMATION"


def test_suggestion_failed_fallback_replaces_partial_ai_draft(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """建议失败降级时不能把半成品 AI 字段带入人工草稿。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        assert archive_document is not None
        archive_document.status = ArchiveDocumentStatus.SUGGESTION_FAILED
        session.add(
            ArchiveFieldValue(
                document_id=document_id,
                field_name=ArchiveFieldName.TITLE,
                text_value="模型半成品",
                review_status=FieldReviewStatus.PENDING_CHECK,
                source="AI",
            )
        )
        session.add(archive_document)
        session.commit()

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/manual-draft",
        headers=auth_headers(engine, user_id),
    )
    assert response.status_code == 200
    assert all(field["source"] is None for field in response.json()["fields"])
    assert all(field["text_value"] is None for field in response.json()["fields"])


def test_unstarted_draft_cannot_save_field(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """已解析但未启动草稿的文档不能直接修改字段。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    base_url = f"/projects/{project_id}/documents/{document_id}"
    response = client.put(
        f"{base_url}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "施工方案",
            "review_status": "VALUE_CONFIRMED",
            "no_source_evidence": True,
            "expected_version": 1,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DRAFT_NOT_STARTED"


def test_field_evidence_is_bound_to_current_snapshot(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """人工字段带证据保存时，证据必须落到当前解析快照。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    base_url = f"/projects/{project_id}/documents/{document_id}"
    start_response = client.post(
        f"{base_url}/manual-draft", headers=auth_headers(engine, user_id)
    )
    version = start_response.json()["document"]["version"]

    response = client.put(
        f"{base_url}/fields/AUTHORING_ORGANIZATION",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "示例建设公司",
            "review_status": "VALUE_CONFIRMED",
            "evidences": [
                {
                    "excerpt": "编制单位：示例建设公司",
                    "location_type": "TEXT_LINE_RANGE",
                    "location_start": 2,
                    "location_end": 2,
                    "normalized_anchor": "编制单位",
                }
            ],
            "expected_version": version,
        },
    )
    assert response.status_code == 200
    field = next(
        item for item in response.json()["fields"] if item["field_name"] == "AUTHORING_ORGANIZATION"
    )
    assert field["source"] == "MANUAL"
    assert field["evidences"][0]["snapshot_id"] == response.json()["snapshot"]["id"]
    with Session(engine) as session:
        snapshot = session.exec(
            select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
        ).one()
    assert field["evidences"][0]["snapshot_id"] == str(snapshot.id)


def test_required_fields_cannot_be_accepted_empty_but_optional_date_can(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """标题和资料类型不能为空，日期可明确接受为空。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    base_url = f"/projects/{project_id}/documents/{document_id}"
    start_response = client.post(
        f"{base_url}/manual-draft", headers=auth_headers(engine, user_id)
    )
    version = start_response.json()["document"]["version"]

    title_response = client.put(
        f"{base_url}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "review_status": "EMPTY_ACCEPTED",
            "no_source_evidence": True,
            "expected_version": version,
        },
    )
    assert title_response.status_code == 422
    assert title_response.json()["error"]["code"] == "REQUIRED_FIELD_EMPTY"

    date_response = client.put(
        f"{base_url}/fields/DOCUMENT_DATE",
        headers=auth_headers(engine, user_id),
        json={
            "review_status": "EMPTY_ACCEPTED",
            "expected_version": version,
        },
    )
    assert date_response.status_code == 200
    assert date_response.json()["document"]["field_summary"]["checked_count"] == 1


def test_document_type_must_use_frozen_dictionary(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """资料类型字段只能使用需求冻结的固定字典值。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    base_url = f"/projects/{project_id}/documents/{document_id}"
    start_response = client.post(
        f"{base_url}/manual-draft", headers=auth_headers(engine, user_id)
    )
    version = start_response.json()["document"]["version"]

    response = client.put(
        f"{base_url}/fields/DOCUMENT_TYPE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "未定义类型",
            "review_status": "VALUE_CONFIRMED",
            "no_source_evidence": True,
            "expected_version": version,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_FIELD_VALUE"


def test_editing_confirmed_field_enters_pending_reconfirmation(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """已确认档案修改正式字段后必须立即退出正式范围。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    base_url = f"/projects/{project_id}/documents/{document_id}"
    start_response = client.post(
        f"{base_url}/manual-draft", headers=auth_headers(engine, user_id)
    )
    assert start_response.status_code == 200
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        snapshot = session.exec(
            select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
        ).one()
        assert archive_document is not None
        archive_document.status = ArchiveDocumentStatus.CONFIRMED
        archive_document.current_snapshot_id = snapshot.id
        archive_document.confirmed_by = user_id
        archive_document.confirmed_at = archive_document.updated_at
        archive_document.final_index_snapshot_hash = "a" * 64
        archive_document.final_chunk_count = 1
        session.add(archive_document)
        session.commit()
        version = archive_document.version

    response = client.put(
        f"{base_url}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "修订后的施工方案",
            "review_status": "VALUE_CONFIRMED",
            "no_source_evidence": True,
            "expected_version": version,
        },
    )
    assert response.status_code == 200
    assert response.json()["document"]["status"] == "PENDING_RECONFIRMATION"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PENDING_RECONFIRMATION
    assert archive_document.confirmed_at is None
