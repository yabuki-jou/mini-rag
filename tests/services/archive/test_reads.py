"""验证智慧档案共享读取投影的确定性行为。"""

from datetime import date, datetime, timezone
from uuid import uuid4

from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    Document,
    FieldReviewStatus,
)
from app.services.archive.reads import (
    archive_field_value,
    build_process_document_read,
    list_archive_field_values,
)


def test_archive_field_value_projects_value_column() -> None:
    """不同字段类型应投影到各自的值列。"""

    assert archive_field_value(None) is None
    assert archive_field_value(
        ArchiveFieldValue(
            document_id=uuid4(),
            field_name=ArchiveFieldName.TITLE,
            text_value="设计说明",
        )
    ) == "设计说明"
    assert archive_field_value(
        ArchiveFieldValue(
            document_id=uuid4(),
            field_name=ArchiveFieldName.DOCUMENT_DATE,
            date_value=date(2026, 9, 12),
        )
    ) == date(2026, 9, 12)
    assert archive_field_value(
        ArchiveFieldValue(
            document_id=uuid4(),
            field_name=ArchiveFieldName.KEYWORDS,
            json_value=["设计", "仓储"],
        )
    ) == ["设计", "仓储"]


def test_build_process_document_read_preserves_summary_and_timestamps() -> None:
    """处理响应应统计检查数并保留受控错误和时间字段。"""

    created_at = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
    document = Document(
        id=uuid4(),
        kb_id=uuid4(),
        project_id=uuid4(),
        filename="design.md",
        storage_path="/tmp/design.md",
        file_hash="a" * 64,
        created_at=created_at,
        updated_at=updated_at,
    )
    archive_document = ArchiveDocument(
        document_id=document.id,
        status=ArchiveDocumentStatus.PARSE_FAILED,
        last_error_code="PARSE_FAILED",
        last_error_summary="解析失败。",
        version=2,
        updated_at=updated_at,
    )
    fields = [
        ArchiveFieldValue(
            document_id=document.id,
            field_name=ArchiveFieldName.TITLE,
            text_value="设计说明",
            review_status=FieldReviewStatus.VALUE_CONFIRMED,
        ),
        ArchiveFieldValue(
            document_id=document.id,
            field_name=ArchiveFieldName.DOCUMENT_TYPE,
            review_status=FieldReviewStatus.PENDING_CHECK,
        ),
    ]

    result = build_process_document_read(
        document=document,
        archive_document=archive_document,
        field_values=fields,
        index_context_chunk_count=3,
    )

    assert result.field_summary.checked_count == 1
    assert result.field_summary.total_count == len(ArchiveFieldName)
    assert result.last_error.code == "PARSE_FAILED"
    assert result.last_error.message == "解析失败。"
    assert result.uploaded_at == created_at
    assert result.updated_at == updated_at
    assert result.index_context_chunk_count == 3


def test_list_archive_field_values_uses_fixed_field_order() -> None:
    """字段读取不依赖数据库返回顺序。"""

    document_id = uuid4()
    values = [
        ArchiveFieldValue(document_id=document_id, field_name=name)
        for name in reversed(tuple(ArchiveFieldName))
    ]

    class FakeResult:
        def all(self):
            return values

    class FakeSession:
        def exec(self, _statement):
            return FakeResult()

    result = list_archive_field_values(document_id, FakeSession())

    assert [field.field_name for field in result] == list(ArchiveFieldName)
