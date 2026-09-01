"""实现 AV1-P09 人工确认前置条件、状态转换和脱敏审计。"""

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    Document,
    FieldEvidence,
    FieldReviewStatus,
    FieldSource,
    ParsedSnapshot,
    utc_now,
)
from app.schemas.archive_confirmation import ArchiveConfirmationRequest
from app.schemas.document import ProcessDocumentRead
from app.services.archive_draft_service import (
    _build_process_document_read,
    _read_field_values,
)
from app.services.archive_index_service import index_confirmed_document


ARCHIVE_CONFIRMED = "ARCHIVE_CONFIRMED"
ARCHIVE_DOCUMENT_RESOURCE_TYPE = "ARCHIVE_DOCUMENT"
_CONFIRMABLE_STATUSES = {
    ArchiveDocumentStatus.PENDING_CONFIRMATION,
    ArchiveDocumentStatus.PENDING_RECONFIRMATION,
}
_REQUIRED_FIELDS = {ArchiveFieldName.TITLE, ArchiveFieldName.DOCUMENT_TYPE}


def _field_value(value: ArchiveFieldValue):
    """读取字段实际使用的值列。"""
    if value.field_name == ArchiveFieldName.DOCUMENT_DATE:
        return value.date_value
    if value.field_name == ArchiveFieldName.KEYWORDS:
        return value.json_value
    return value.text_value


def _is_nonempty(value) -> bool:
    """判断字段是否有可确认的非空值。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _raise_confirmation_not_allowed(field_names: Sequence[ArchiveFieldName]) -> None:
    """返回不暴露字段正文的确认前置条件错误。"""
    details = None
    if field_names:
        details = {"pending_fields": [field_name.value for field_name in field_names]}
    raise AppError(409, "CONFIRM_NOT_ALLOWED", "人工确认前置条件尚未满足。", details)


def _validate_confirmation_fields(
    *,
    fields: Sequence[ArchiveFieldValue],
    snapshot: ParsedSnapshot,
    session: Session,
) -> None:
    """校验七字段检查、必要字段值和 AI 当前快照证据。"""
    by_name = {field.field_name: field for field in fields}
    missing = [field_name for field_name in ArchiveFieldName if field_name not in by_name]
    if missing:
        _raise_confirmation_not_allowed(missing)

    pending = [
        field.field_name
        for field in fields
        if field.review_status == FieldReviewStatus.PENDING_CHECK
    ]
    if pending:
        _raise_confirmation_not_allowed(pending)

    invalid_required = [
        field.field_name
        for field_name in _REQUIRED_FIELDS
        if (
            (field := by_name[field_name]).review_status != FieldReviewStatus.VALUE_CONFIRMED
            or not _is_nonempty(_field_value(field))
        )
    ]
    if invalid_required:
        _raise_confirmation_not_allowed(sorted(invalid_required, key=lambda item: item.value))

    for field in fields:
        value = _field_value(field)
        if field.source != FieldSource.AI or not _is_nonempty(value):
            continue
        evidence = session.exec(
            select(FieldEvidence).where(
                FieldEvidence.field_value_id == field.id,
                FieldEvidence.snapshot_id == snapshot.id,
            )
        ).first()
        if evidence is None:
            raise AppError(
                422,
                "AI_FIELD_EVIDENCE_REQUIRED",
                "AI 非空字段缺少当前快照证据。",
            )


def confirm_document(
    *,
    document: Document,
    actor_id: UUID,
    payload: ArchiveConfirmationRequest,
    session: Session,
) -> ProcessDocumentRead:
    """校验人工前置条件，提交确认事实后触发内部 INDEX 编排。

    确认事实和脱敏审计先提交到 PostgreSQL；随后由 INDEX 服务生成 Final Chunk 并写入
    向量集合。外部依赖失败会透传稳定错误，保留已确认但待恢复索引的状态。
    """
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document.id)
        .with_for_update()
    ).first()
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")

    if archive_document.version != payload.expected_version:
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取草稿。")

    field_values = _read_field_values(document.id, session)
    if archive_document.status == ArchiveDocumentStatus.CONFIRMED:
        index_result = None
        # 已确认但尚无 Final Chunk 表示上次 INDEX 失败或尚未完成；重放同一版本时
        # 允许恢复索引。已有正式 Chunk 的确认请求仍保持真正幂等，不重复调用外部服务。
        if archive_document.final_chunk_count == 0:
            index_result = index_confirmed_document(document=document, session=session)
        return _build_process_document_read(
            document=document,
            archive_document=archive_document,
            field_values=field_values,
            index_context_chunk_count=(
                index_result.contextual_chunk_count if index_result is not None else None
            ),
        )
    if archive_document.status not in _CONFIRMABLE_STATUSES:
        raise AppError(409, "CONFIRM_NOT_ALLOWED", "当前文档状态不允许人工确认。")

    if archive_document.current_snapshot_id is None:
        raise AppError(409, "CONFIRM_NOT_ALLOWED", "文档缺少当前解析快照。")
    snapshot = session.get(ParsedSnapshot, archive_document.current_snapshot_id)
    if snapshot is None or snapshot.document_id != document.id:
        raise AppError(409, "CONFIRM_NOT_ALLOWED", "文档当前解析快照无效。")

    _validate_confirmation_fields(
        fields=field_values,
        snapshot=snapshot,
        session=session,
    )
    if document.project_id is None:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")

    now = utc_now()
    archive_document.status = ArchiveDocumentStatus.CONFIRMED
    archive_document.confirmed_by = actor_id
    archive_document.confirmed_at = now
    # 当前切片尚未写入 Final Collection；先记录确认所依据的不可变快照指纹，
    # 后续索引切片会以同一快照生成 Chunk 并更新正式索引计数。
    archive_document.final_index_snapshot_hash = snapshot.snapshot_hash
    archive_document.final_chunk_count = 0
    archive_document.last_error_code = None
    archive_document.last_error_summary = None
    archive_document.version += 1
    archive_document.updated_at = now
    session.add(archive_document)
    session.add(
        ArchiveAuditLog(
            project_id=document.project_id,
            actor_id=actor_id,
            operation_type=ARCHIVE_CONFIRMED,
            resource_type=ARCHIVE_DOCUMENT_RESOURCE_TYPE,
            resource_id=document.id,
            redacted_summary={"status": ArchiveDocumentStatus.CONFIRMED.value},
        )
    )
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(500, "CONFIRM_FAILED", "人工确认保存失败。") from exc

    session.refresh(archive_document)
    index_result = index_confirmed_document(document=document, session=session)
    return _build_process_document_read(
        document=document,
        archive_document=archive_document,
        field_values=field_values,
        index_context_chunk_count=getattr(index_result, "contextual_chunk_count", None),
    )
