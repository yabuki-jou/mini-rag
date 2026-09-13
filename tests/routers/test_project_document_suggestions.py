"""验证 AV1-P08 首次 AI 建议的 HTTP 契约。"""

from pathlib import Path
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session
from sqlmodel import select

from app.models import ArchiveAuditLog, ArchiveDocument, ArchiveDocumentStatus
from tests.routers.test_project_document_draft import (
    _parsed_document,
    create_user,
    project_document_api,
)
from tests.support.auth import auth_headers
import app.services.archive.suggestions as suggestion_service_module


class _BindableFakeModel:
    """让路由测试 Fake 与生产建议链路使用同一 bind/invoke 形状。"""

    def bind(self, **_: object):
        return self


def _valid_model_content(title: str = "施工方案") -> str:
    """返回固定的最小有效建议，避免测试依赖真实模型语义。"""
    return json.dumps(
        {
            "fields": {
                "TITLE": {
                    "text_value": title,
                    "evidences": [
                        {
                            "excerpt": "项目施工方案正文",
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 1,
                            "location_end": 1,
                        }
                    ],
                }
            }
        },
        ensure_ascii=False,
    )


def test_suggestion_prompt_declares_the_complete_machine_readable_contract() -> None:
    """提示必须显式声明真实解析器接受的英文键、枚举和值列规则。"""
    prompt = suggestion_service_module._build_prompt({"fragments": []})

    for field_name in (
        "TITLE", "DOCUMENT_TYPE", "DOCUMENT_DATE", "AUTHORING_ORGANIZATION",
        "VERSION_NUMBER", "PROJECT_STAGE", "KEYWORDS",
    ):
        assert f'"{field_name}"' in prompt
    for enum_value in (
        "CONTRACT", "DESIGN", "CONSTRUCTION", "MEETING_MINUTES", "ACCEPTANCE", "OTHER",
        "PREPARATION", "CROSS_STAGE", "OTHER_STAGE",
    ):
        assert enum_value in prompt
    for value_column in ("text_value", "date_value", "json_value"):
        assert f'"{value_column}"' in prompt
    assert "null" in prompt
    for evidence_key in (
        "excerpt", "location_type", "location_start", "location_end", "normalized_anchor",
        "PDF_PAGE", "DOCX_PARAGRAPH", "TEXT_LINE_RANGE",
    ):
        assert evidence_key in prompt
    assert "当前解析快照" in prompt
    assert "只输出裸 JSON" in prompt
    assert "Markdown" in prompt


def test_suggestion_model_is_bound_to_json_object_response_format(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """建议模型必须在调用前绑定 JSON Object 响应格式。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    bind_calls: list[dict[str, object]] = []

    class BindableSuggestionModel:
        def bind(self, **kwargs):
            bind_calls.append(kwargs)
            return self

        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(
        suggestion_service_module,
        "get_chat_model",
        lambda: BindableSuggestionModel(),
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 200
    assert bind_calls == [{"response_format": {"type": "json_object"}}]


def test_first_suggestion_creates_ai_draft_with_current_snapshot_evidence(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """解析成功文档首次建议应生成带当前快照证据的 AI 草稿。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    class FakeSuggestionModel(_BindableFakeModel):
        """返回固定结构的假模型，只验证服务的确定性解析和持久化。"""

        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(
        suggestion_service_module,
        "get_chat_model",
        lambda: FakeSuggestionModel(),
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document"]["status"] == "PENDING_CONFIRMATION"
    assert len(payload["fields"]) == 7
    title = next(field for field in payload["fields"] if field["field_name"] == "TITLE")
    assert title["source"] == "AI"
    assert title["review_status"] == "PENDING_CHECK"
    assert title["evidences"]
    assert title["evidences"][0]["snapshot_id"] == payload["snapshot"]["id"]
    with Session(engine) as session:
        assert session.exec(
            select(ArchiveAuditLog).where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.operation_type == "SUGGESTION_RETRIED",
            )
        ).all() == []


def test_connection_timeout_retries_once_before_success(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """模型连接或超时失败时只自动重试一次，随后成功即可完成建议。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    calls = 0

    class RetryingModel(_BindableFakeModel):
        """第一次模拟超时，第二次返回固定有效结构。"""

        def invoke(self, _: str) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary model timeout")
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(
        suggestion_service_module,
        "get_chat_model",
        lambda: RetryingModel(),
    )

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 200
    assert calls == 2
    assert response.json()["document"]["status"] == "PENDING_CONFIRMATION"


def test_invalid_output_records_failed_and_retry_recovers(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """格式错误不自动重试且进入失败态，专用 retry 可恢复为 AI 草稿。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)
    invalid_calls = 0

    class InvalidModel(_BindableFakeModel):
        """返回不可解析内容，用于验证格式错误不触发自动重试。"""

        def invoke(self, _: str) -> SimpleNamespace:
            nonlocal invalid_calls
            invalid_calls += 1
            return SimpleNamespace(content="not-json")

    monkeypatch.setattr(
        suggestion_service_module,
        "get_chat_model",
        lambda: InvalidModel(),
    )
    failed_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )

    assert failed_response.status_code == 422
    assert failed_response.json()["error"]["code"] == "SUGGESTION_INVALID_OUTPUT"
    assert invalid_calls == 1
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        assert archive_document is not None
        assert archive_document.status == ArchiveDocumentStatus.SUGGESTION_FAILED
        assert archive_document.last_error_code == "SUGGESTION_INVALID_OUTPUT"

    class ValidModel(_BindableFakeModel):
        """重试时返回固定有效结构。"""

        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(
        suggestion_service_module,
        "get_chat_model",
        lambda: ValidModel(),
    )
    retry_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/retry",
        headers=auth_headers(engine, user_id),
    )

    assert retry_response.status_code == 200
    assert retry_response.json()["document"]["status"] == "PENDING_CONFIRMATION"
    title = next(
        field for field in retry_response.json()["fields"] if field["field_name"] == "TITLE"
    )
    assert title["source"] == "AI"
    with Session(engine) as session:
        audit_logs = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.project_id == project_id,
                    ArchiveAuditLog.operation_type == "SUGGESTION_RETRIED",
                )
            ).all()
        )
    assert len(audit_logs) == 1
    assert audit_logs[0].actor_id == user_id
    assert audit_logs[0].redacted_summary == {
        "status": "PENDING_CONFIRMATION",
        "version": retry_response.json()["document"]["version"],
    }


def test_suggestion_retry_is_rejected_before_failure(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """未失败的 PARSED 文档不能绕过首次建议直接调用 retry。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/retry",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SUGGESTION_RETRY_NOT_ALLOWED"


def test_regenerate_replaces_unedited_ai_draft_and_records_audit(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """未人工编辑的 AI 草稿可用新证据原子替换并递增版本。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    class InitialModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: InitialModel())
    initial = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )
    assert initial.status_code == 200
    expected_version = initial.json()["document"]["version"]

    class RegenerateModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content("更新后的施工方案"))

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: RegenerateModel())
    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/regenerate",
        headers=auth_headers(engine, user_id),
        json={"expected_version": expected_version},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document"]["status"] == "PENDING_CONFIRMATION"
    assert payload["document"]["version"] == expected_version + 1
    title = next(field for field in payload["fields"] if field["field_name"] == "TITLE")
    assert title["text_value"] == "更新后的施工方案"
    assert title["source"] == "AI"
    with Session(engine) as session:
        audit_logs = session.exec(
            select(ArchiveAuditLog).where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.operation_type == "SUGGESTION_REGENERATED",
            )
        ).all()
    assert len(audit_logs) == 1


def test_regenerate_rejects_stale_version_without_calling_model(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """regenerate 的陈旧版本在模型调用前被拒绝，原草稿保持不变。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    class InitialModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: InitialModel())
    initial = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )
    assert initial.status_code == 200
    before = initial.json()
    calls = 0

    class ShouldNotRunModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(content=_valid_model_content("不应保存"))

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: ShouldNotRunModel())
    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/regenerate",
        headers=auth_headers(engine, user_id),
        json={"expected_version": before["document"]["version"] - 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert calls == 0
    read_response = client.get(
        f"/projects/{project_id}/documents/{document_id}/draft",
        headers=auth_headers(engine, user_id),
    )
    assert read_response.status_code == 200
    assert read_response.json()["document"]["version"] == before["document"]["version"]


def test_regenerate_rechecks_version_after_model_call(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """模型调用期间版本被并发推进时，返回冲突且不替换原草稿。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    class InitialModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: InitialModel())
    initial = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )
    assert initial.status_code == 200
    before = initial.json()

    class ConcurrentUpdateModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            with Session(engine) as concurrent_session:
                archive_document = concurrent_session.get(ArchiveDocument, document_id)
                assert archive_document is not None
                archive_document.version += 1
                concurrent_session.add(archive_document)
                concurrent_session.commit()
            return SimpleNamespace(content=_valid_model_content("并发期间的新标题"))

    monkeypatch.setattr(
        suggestion_service_module,
        "get_chat_model",
        lambda: ConcurrentUpdateModel(),
    )
    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/regenerate",
        headers=auth_headers(engine, user_id),
        json={"expected_version": before["document"]["version"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    read_response = client.get(
        f"/projects/{project_id}/documents/{document_id}/draft",
        headers=auth_headers(engine, user_id),
    )
    after = read_response.json()
    title = next(field for field in after["fields"] if field["field_name"] == "TITLE")
    assert after["document"]["version"] == before["document"]["version"] + 1
    assert title["text_value"] == "施工方案"


def test_regenerate_rejects_manual_edit_without_calling_model(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """已有人工字段时 regenerate 不调用模型，也不覆盖人工内容。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    class InitialModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: InitialModel())
    initial = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )
    assert initial.status_code == 200
    version = initial.json()["document"]["version"]
    manual = client.put(
        f"/projects/{project_id}/documents/{document_id}/fields/TITLE",
        headers=auth_headers(engine, user_id),
        json={
            "text_value": "人工修订标题",
            "review_status": "VALUE_CONFIRMED",
            "source": "MANUAL",
            "no_source_evidence": True,
            "expected_version": version,
        },
    )
    assert manual.status_code == 200
    manual_version = manual.json()["document"]["version"]
    calls = 0

    class ShouldNotRunModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(content=_valid_model_content("不应覆盖"))

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: ShouldNotRunModel())
    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/regenerate",
        headers=auth_headers(engine, user_id),
        json={"expected_version": manual_version},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SUGGESTION_WOULD_OVERWRITE_MANUAL_DRAFT"
    assert calls == 0
    read_response = client.get(
        f"/projects/{project_id}/documents/{document_id}/draft",
        headers=auth_headers(engine, user_id),
    )
    title = next(field for field in read_response.json()["fields"] if field["field_name"] == "TITLE")
    assert read_response.json()["document"]["version"] == manual_version
    assert title["text_value"] == "人工修订标题"
    assert title["source"] == "MANUAL"


def test_regenerate_failure_preserves_existing_ai_draft(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch,
) -> None:
    """regenerate 的格式错误只返回错误，不删除现有 AI 草稿。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id = _parsed_document(client, engine, user_id)

    class InitialModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content=_valid_model_content())

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: InitialModel())
    initial = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions",
        headers=auth_headers(engine, user_id),
    )
    assert initial.status_code == 200
    before = initial.json()

    class InvalidModel(_BindableFakeModel):
        def invoke(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(content="not-json")

    monkeypatch.setattr(suggestion_service_module, "get_chat_model", lambda: InvalidModel())
    response = client.post(
        f"/projects/{project_id}/documents/{document_id}/suggestions/regenerate",
        headers=auth_headers(engine, user_id),
        json={"expected_version": before["document"]["version"]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SUGGESTION_INVALID_OUTPUT"
    read_response = client.get(
        f"/projects/{project_id}/documents/{document_id}/draft",
        headers=auth_headers(engine, user_id),
    )
    after = read_response.json()
    before_title = next(field for field in before["fields"] if field["field_name"] == "TITLE")
    after_title = next(field for field in after["fields"] if field["field_name"] == "TITLE")
    assert after["document"]["status"] == "PENDING_CONFIRMATION"
    assert after["document"]["version"] == before["document"]["version"]
    assert after_title["text_value"] == before_title["text_value"]
    assert after_title["source"] == "AI"
