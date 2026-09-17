"""验证 FR-042 项目档案助手的独立 HTTP Schema 契约。"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.archive_agent import (
    ArchiveAgentCitationRead,
    ArchiveAgentMessageCreate,
    ArchiveAgentMessageRead,
    ArchiveAgentResponse,
    ArchiveAgentSessionCreate,
    ArchiveAgentSessionRead,
)


def test_archive_session_create_is_strict_empty_object() -> None:
    """创建请求只接受空对象，所有客户端范围字段都必须被拒绝。"""
    assert ArchiveAgentSessionCreate().model_dump() == {}

    for field_name in ("user_id", "project_id", "kb_id", "thread_id", "agent_type"):
        with pytest.raises(ValidationError):
            ArchiveAgentSessionCreate.model_validate({field_name: "forbidden"})


def test_archive_message_normalizes_before_unicode_codepoint_validation() -> None:
    """消息应先统一换行和首尾 Unicode 空白，再按码点限制长度。"""
    normalized = ArchiveAgentMessageCreate.model_validate(
        {"message": "\u3000第一行\r\n第二行\r第三行\u00a0"}
    )
    assert normalized.message == "第一行\n第二行\n第三行"
    assert ArchiveAgentMessageCreate(message="文" * 2000).message == "文" * 2000

    for invalid_payload in (
        {"message": "\u3000\u00a0\t\r\n"},
        {"message": "文" * 2001},
        {"message": 1},
        {"message": "有效", "project_id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            ArchiveAgentMessageCreate.model_validate(invalid_payload)


def test_archive_public_dtos_expose_only_frozen_fields() -> None:
    """六个 DTO 的公开字段不得带出服务端范围和检索内部标识。"""
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    session_read = ArchiveAgentSessionRead(
        id=uuid4(),
        project_id=uuid4(),
        created_at=now,
        updated_at=now,
    )
    citation = ArchiveAgentCitationRead(
        filename="施工方案.docx",
        location_type="DOCX_PARAGRAPH",
        location_start=3,
        location_end=3,
        excerpt="编制单位：示例建设公司",
    )
    message_read = ArchiveAgentMessageRead(
        role="ASSISTANT",
        content="已找到正式档案证据。",
    )
    response = ArchiveAgentResponse(
        session_id=session_read.id,
        answer_status="ANSWERED",
        answer="编制单位为示例建设公司。",
        citations=[citation],
        request_id=uuid4(),
    )

    assert set(session_read.model_dump()) == {
        "id",
        "project_id",
        "created_at",
        "updated_at",
    }
    assert set(citation.model_dump()) == {
        "filename",
        "location_type",
        "location_start",
        "location_end",
        "excerpt",
    }
    assert set(message_read.model_dump()) == {"role", "content", "citations"}
    assert message_read.citations == []
    assert set(response.model_dump()) == {
        "session_id",
        "answer_status",
        "answer",
        "citations",
        "request_id",
    }
    serialized = response.model_dump_json()
    for forbidden in (
        "user_id",
        "project_id",
        "kb_id",
        "thread_id",
        "agent_type",
        "document_id",
        "chunk_id",
        "score",
        "reranker_score",
        "document_ref",
    ):
        assert forbidden not in serialized


def test_archive_citation_rejects_invalid_location_range() -> None:
    """引用位置必须从一开始，结束位置不得早于开始位置。"""
    for location_start, location_end in ((0, 1), (3, 2)):
        with pytest.raises(ValidationError):
            ArchiveAgentCitationRead(
                filename="施工方案.docx",
                location_type="DOCX_PARAGRAPH",
                location_start=location_start,
                location_end=location_end,
                excerpt="证据",
            )
