"""验证 AV1-P12 仅基于正式证据回答并在无依据时拒答。"""

from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.schemas.archive_retrieval import (
    ArchiveRetrievalItemRead,
    ArchiveRetrievalResponse,
)
from app.services import archive_question_service


def _retrieval_response() -> ArchiveRetrievalResponse:
    return ArchiveRetrievalResponse(
        items=[
            ArchiveRetrievalItemRead(
                chunk_id="a" * 64,
                document_id=uuid4(),
                filename="施工方案.docx",
                location_type="DOCX_PARAGRAPH",
                location_start=3,
                location_end=3,
                excerpt="编制单位：示例建设公司",
                score=0.88,
            )
        ],
        requested_top_k=5,
        returned_count=1,
    )


class FakeModel:
    """捕获提示并返回固定文本的聊天模型。"""

    def __init__(self, content: str = "编制单位是示例建设公司。[S1]"):
        self.content = content
        self.prompts: list[object] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("Response", (), {"content": self.content})()


def test_no_evidence_refuses_without_calling_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """正式检索为空时返回 REFUSED_NO_EVIDENCE，模型不得被调用。"""
    called = False

    def fail_model():
        nonlocal called
        called = True
        raise AssertionError("no evidence must not invoke DeepSeek")

    monkeypatch.setattr(
        archive_question_service,
        "retrieve_archive_chunks",
        lambda **_: ArchiveRetrievalResponse(items=[], requested_top_k=5, returned_count=0),
    )
    monkeypatch.setattr(archive_question_service, "get_chat_model", fail_model)

    response = archive_question_service.answer_archive_question(
        user_id=uuid4(), project_id=uuid4(), kb_id=uuid4(), question="未知问题", session=None
    )

    assert response.answer_status == "REFUSED_NO_EVIDENCE"
    assert response.citations == []
    assert called is False


def test_answer_uses_only_retrieved_evidence_and_returns_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有证据时 Prompt 包含证据，响应返回同一批可追溯引用。"""
    model = FakeModel()
    monkeypatch.setattr(
        archive_question_service,
        "retrieve_archive_chunks",
        lambda **_: _retrieval_response(),
    )
    monkeypatch.setattr(archive_question_service, "get_chat_model", lambda: model)

    response = archive_question_service.answer_archive_question(
        user_id=uuid4(), project_id=uuid4(), kb_id=uuid4(), question="编制单位是什么？", session=None
    )

    assert response.answer_status == "ANSWERED"
    assert response.answer.startswith("编制单位")
    assert len(response.citations) == 1
    assert response.citations[0].excerpt == "编制单位：示例建设公司"
    assert "示例建设公司" in str(model.prompts[0])


def test_answer_records_eval_decision_evidence_and_final_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测观测必须记录问答分支、进入提示的证据和最终脱敏响应。"""
    observed: list[tuple[object, dict[str, object]]] = []

    def capture(value: object, **kwargs: object) -> object:
        observed.append((value, kwargs))
        return value

    monkeypatch.setattr(archive_question_service, "eval_wrap", capture)
    monkeypatch.setattr(
        archive_question_service,
        "retrieve_archive_chunks",
        lambda **_: _retrieval_response(),
    )
    monkeypatch.setattr(archive_question_service, "get_chat_model", lambda: FakeModel())

    archive_question_service.answer_archive_question(
        user_id=uuid4(), project_id=uuid4(), kb_id=uuid4(), question="编制单位是什么？", session=None
    )

    observed_names = {str(kwargs["name"]) for _, kwargs in observed}
    assert {
        "archive_question_decision",
        "archive_question_prompt_evidence",
        "archive_question_response",
    } <= observed_names
    assert all(kwargs["purpose"] in {"state", "output"} for _, kwargs in observed)
    observed_by_name = {str(kwargs["name"]): value for value, kwargs in observed}
    assert observed_by_name["archive_question_decision"] == {
        "has_evidence": True,
        "retrieved_item_count": 1,
    }
    assert observed_by_name["archive_question_prompt_evidence"][0]["excerpt"] == "编制单位：示例建设公司"
    assert observed_by_name["archive_question_response"]["answer_status"] == "ANSWERED"


def test_model_unavailable_maps_to_stable_archive_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型配置或调用失败统一返回 ARCHIVE_ANSWER_UNAVAILABLE。"""
    monkeypatch.setattr(
        archive_question_service,
        "retrieve_archive_chunks",
        lambda **_: _retrieval_response(),
    )
    monkeypatch.setattr(
        archive_question_service,
        "get_chat_model",
        lambda: (_ for _ in ()).throw(AppError(503, "DEEPSEEK_NOT_CONFIGURED", "未配置")),
    )

    with pytest.raises(AppError) as exc_info:
        archive_question_service.answer_archive_question(
            user_id=uuid4(), project_id=uuid4(), kb_id=uuid4(), question="问题", session=None
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "ARCHIVE_ANSWER_UNAVAILABLE"
