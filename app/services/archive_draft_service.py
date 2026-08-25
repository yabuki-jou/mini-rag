"""实现 AV1-P07 的人工草稿、字段检查和证据保存流程。"""

from collections.abc import Sequence
import logging
from uuid import UUID

from sqlalchemy import delete, update
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentType,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    Document,
    EvidenceLocationType,
    FieldEvidence,
    FieldReviewStatus,
    FieldSource,
    ParsedSnapshot,
    ProjectStage,
    utc_now,
)
from app.schemas import (
    ArchiveDraftRead,
    ArchiveFieldUpdate,
    FieldDraftRead,
    FieldEvidenceRead,
    ParsedSnapshotRead,
)
from app.schemas.document import FieldSummaryRead, LastErrorRead, ProcessDocumentRead


logger = logging.getLogger(__name__)


_FIELD_NAMES: tuple[ArchiveFieldName, ...] = tuple(ArchiveFieldName)
_EDITABLE_STATUSES = {
    ArchiveDocumentStatus.PENDING_CONFIRMATION,
    ArchiveDocumentStatus.PENDING_RECONFIRMATION,
}


def _build_process_document_read(
    *,
    document: Document,
    archive_document: ArchiveDocument,
    field_values: Sequence[ArchiveFieldValue],
) -> ProcessDocumentRead:
    """把草稿字段检查事实组合成项目文档响应。"""
    return ProcessDocumentRead(
        id=document.id,
        filename=document.filename,
        file_hash=document.file_hash,
        status=archive_document.status,
        last_error=LastErrorRead(
            code=archive_document.last_error_code,
            message=archive_document.last_error_summary,
        ),
        field_summary=FieldSummaryRead(
            checked_count=sum(
                value.review_status != FieldReviewStatus.PENDING_CHECK
                for value in field_values
            ),
            total_count=len(_FIELD_NAMES),
        ),
        confirmed_at=archive_document.confirmed_at,
        version=archive_document.version,
        uploaded_at=document.created_at,
        updated_at=document.updated_at,
    )


def _read_field_values(document_id: UUID, session: Session) -> list[ArchiveFieldValue]:
    """按固定字段顺序读取草稿字段。"""
    values = list(
        session.exec(
            select(ArchiveFieldValue).where(
                ArchiveFieldValue.document_id == document_id
            )
        ).all()
    )
    by_name = {value.field_name: value for value in values}
    return [by_name[name] for name in _FIELD_NAMES if name in by_name]


def _build_draft_response(
    *,
    document: Document,
    archive_document: ArchiveDocument,
    field_values: Sequence[ArchiveFieldValue],
    session: Session,
) -> ArchiveDraftRead:
    """构造人工草稿详情，并把证据作为字段的嵌套资源返回。"""
    evidence_by_field: dict[UUID, list[FieldEvidence]] = {}
    if field_values:
        evidences = session.exec(
            select(FieldEvidence).where(
                FieldEvidence.field_value_id.in_([value.id for value in field_values])
            )
        ).all()
        for evidence in evidences:
            evidence_by_field.setdefault(evidence.field_value_id, []).append(evidence)

    snapshot = (
        session.get(ParsedSnapshot, archive_document.current_snapshot_id)
        if archive_document.current_snapshot_id is not None
        else None
    )
    return ArchiveDraftRead(
        document=_build_process_document_read(
            document=document,
            archive_document=archive_document,
            field_values=field_values,
        ),
        fields=[
            FieldDraftRead(
                id=value.id,
                field_name=value.field_name,
                text_value=value.text_value,
                date_value=value.date_value,
                json_value=value.json_value,
                review_status=value.review_status,
                source=value.source,
                no_source_evidence=value.no_source_evidence,
                updated_by=value.updated_by,
                updated_at=value.updated_at,
                evidences=[
                    FieldEvidenceRead.model_validate(evidence)
                    for evidence in evidence_by_field.get(value.id, [])
                ],
            )
            for value in field_values
        ],
        snapshot=(ParsedSnapshotRead.model_validate(snapshot) if snapshot else None),
        next_actions=(
            ["UPDATE_FIELDS"]
            if archive_document.status in _EDITABLE_STATUSES
            else []
        ),
    )


def _get_archive_document(document_id: UUID, session: Session) -> ArchiveDocument:
    """读取归档扩展记录，统一处理内部数据不完整。"""
    archive_document = session.get(ArchiveDocument, document_id)
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    return archive_document


def create_manual_draft(
    *,
    document: Document,
    actor_id: UUID,
    session: Session,
) -> ArchiveDraftRead:
    """从已解析文档创建七字段空白人工草稿。"""
    # PostgreSQL 行锁使“状态检查 + 七字段创建 + 版本递增”成为一个原子操作。
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document.id)
        .with_for_update()
    ).first()
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.status not in {
        ArchiveDocumentStatus.PARSED,
        ArchiveDocumentStatus.SUGGESTION_FAILED,
    }:
        raise AppError(
            409,
            "MANUAL_DRAFT_NOT_ALLOWED",
            "当前文档状态不允许启动人工草稿。",
        )
    existing_field_values = _read_field_values(document.id, session)
    if existing_field_values and archive_document.status == ArchiveDocumentStatus.PARSED:
        raise AppError(409, "MANUAL_DRAFT_ALREADY_STARTED", "人工草稿已经启动。")
    if existing_field_values:
        # 建议失败降级必须得到真正的空白手工草稿，不能保留半成品 AI 值或证据。
        session.execute(
            delete(ArchiveFieldValue).where(ArchiveFieldValue.document_id == document.id)
        )
        session.flush()

    now = utc_now()
    field_values = [
        ArchiveFieldValue(
            document_id=document.id,
            field_name=field_name,
            review_status=FieldReviewStatus.PENDING_CHECK,
            source=None,
            no_source_evidence=False,
            updated_by=actor_id,
            updated_at=now,
        )
        for field_name in _FIELD_NAMES
    ]
    session.add_all(field_values)
    archive_document.status = ArchiveDocumentStatus.PENDING_CONFIRMATION
    archive_document.version += 1
    archive_document.updated_at = now
    session.add(archive_document)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("manual_draft_create_failed")
        raise AppError(500, "MANUAL_DRAFT_CREATE_FAILED", "人工草稿创建失败。") from exc
    session.refresh(archive_document)
    for value in field_values:
        session.refresh(value)
    return _build_draft_response(
        document=document,
        archive_document=archive_document,
        field_values=_read_field_values(document.id, session),
        session=session,
    )


def read_document_draft(*, document: Document, session: Session) -> ArchiveDraftRead:
    """读取已解析文档的字段草稿和当前快照。"""
    archive_document = _get_archive_document(document.id, session)
    if archive_document.status in {
        ArchiveDocumentStatus.UPLOADED,
        ArchiveDocumentStatus.PARSE_FAILED,
    }:
        raise AppError(409, "DRAFT_NOT_AVAILABLE", "文档尚未成功解析。")
    field_values = _read_field_values(document.id, session)
    if not field_values:
        raise AppError(404, "DRAFT_NOT_STARTED", "人工草稿尚未启动。")
    return _build_draft_response(
        document=document,
        archive_document=archive_document,
        field_values=field_values,
        session=session,
    )


def _canonical_value(payload: ArchiveFieldUpdate, field_name: ArchiveFieldName):
    """校验字段只使用其对应的值列，并返回规范化值。"""
    if field_name == ArchiveFieldName.DOCUMENT_DATE:
        if payload.text_value is not None or payload.json_value is not None:
            raise AppError(422, "FIELD_VALUE_SHAPE_INVALID", "资料日期只能使用 date_value。")
        return payload.date_value
    if field_name == ArchiveFieldName.KEYWORDS:
        if payload.text_value is not None or payload.date_value is not None:
            raise AppError(422, "FIELD_VALUE_SHAPE_INVALID", "关键词只能使用 json_value。")
        if payload.json_value is not None and any(not item.strip() for item in payload.json_value):
            raise AppError(422, "FIELD_VALUE_SHAPE_INVALID", "关键词不能包含空字符串。")
        # `EMPTY_ACCEPTED` 的数据库约束要求真正的 SQL NULL，而不是空 JSON 数组。
        return None if payload.json_value == [] else payload.json_value
    if payload.date_value is not None or payload.json_value is not None:
        raise AppError(422, "FIELD_VALUE_SHAPE_INVALID", "该字段只能使用 text_value。")
    value = payload.text_value.strip() if payload.text_value is not None else None
    if field_name == ArchiveFieldName.DOCUMENT_TYPE and value is not None:
        if value not in {item.value for item in ArchiveDocumentType}:
            raise AppError(422, "INVALID_FIELD_VALUE", "资料类型不在固定字典中。")
    if field_name == ArchiveFieldName.PROJECT_STAGE and value is not None:
        if value not in {item.value for item in ProjectStage}:
            raise AppError(422, "INVALID_FIELD_VALUE", "项目阶段不在固定字典中。")
    return value


def _validate_review_value(
    *,
    field_name: ArchiveFieldName,
    review_status: FieldReviewStatus,
    value,
) -> None:
    """执行数据库约束之外的人工检查业务规则。"""
    nonempty = value is not None and (not isinstance(value, str) or bool(value.strip()))
    if review_status == FieldReviewStatus.EMPTY_ACCEPTED:
        if nonempty and not (isinstance(value, list) and len(value) == 0):
            raise AppError(422, "EMPTY_ACCEPTED_REQUIRES_EMPTY", "接受为空时字段值必须为空。")
        if field_name in {ArchiveFieldName.TITLE, ArchiveFieldName.DOCUMENT_TYPE}:
            raise AppError(422, "REQUIRED_FIELD_EMPTY", "标题和资料类型不能接受为空。")
    elif review_status == FieldReviewStatus.VALUE_CONFIRMED:
        if not nonempty or (isinstance(value, list) and not value):
            raise AppError(422, "VALUE_REQUIRED", "确认有值时必须提供字段值。")


def update_field(
    *,
    document: Document,
    actor_id: UUID,
    field_name: ArchiveFieldName,
    payload: ArchiveFieldUpdate,
    session: Session,
) -> ArchiveDraftRead:
    """保存单字段人工值、检查状态和快照证据，并执行乐观锁。"""
    archive_document = _get_archive_document(document.id, session)
    if not _read_field_values(document.id, session) and archive_document.status in {
        ArchiveDocumentStatus.PARSED,
        ArchiveDocumentStatus.SUGGESTION_FAILED,
    }:
        raise AppError(409, "DRAFT_NOT_STARTED", "人工草稿尚未启动。")
    if archive_document.status not in _EDITABLE_STATUSES and archive_document.status != ArchiveDocumentStatus.CONFIRMED:
        raise AppError(409, "FIELD_EDIT_NOT_ALLOWED", "当前文档状态不允许编辑字段。")
    if payload.source == FieldSource.AI:
        raise AppError(409, "AI_SOURCE_FORBIDDEN", "客户端不能声明 AI 字段来源。")
    if archive_document.version != payload.expected_version:
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取草稿。")

    field_value = session.exec(
        select(ArchiveFieldValue).where(
            ArchiveFieldValue.document_id == document.id,
            ArchiveFieldValue.field_name == field_name,
        )
    ).first()
    if field_value is None:
        raise AppError(409, "DRAFT_NOT_STARTED", "人工草稿尚未启动。")

    value = _canonical_value(payload, field_name)
    _validate_review_value(
        field_name=field_name,
        review_status=payload.review_status,
        value=value,
    )
    if payload.evidences and archive_document.current_snapshot_id is None:
        raise AppError(422, "FIELD_EVIDENCE_SNAPSHOT_REQUIRED", "字段证据必须关联当前解析快照。")
    if payload.no_source_evidence and payload.evidences:
        raise AppError(422, "NO_SOURCE_EVIDENCE_CONFLICT", "无原文证据标记不能同时保存证据。")
    if value is not None and not payload.evidences and not payload.no_source_evidence:
        raise AppError(422, "MANUAL_EVIDENCE_REQUIRED", "人工字段无证据时必须显式标记。")
    for evidence in payload.evidences:
        if evidence.location_end < evidence.location_start:
            raise AppError(422, "FIELD_EVIDENCE_LOCATION_INVALID", "证据定位范围无效。")
        if evidence.location_type != EvidenceLocationType.TEXT_LINE_RANGE and evidence.location_end != evidence.location_start:
            raise AppError(422, "FIELD_EVIDENCE_LOCATION_INVALID", "页码或段落证据只能使用点定位。")

    field_value.text_value = value if field_name not in {
        ArchiveFieldName.DOCUMENT_DATE,
        ArchiveFieldName.KEYWORDS,
    } else None
    field_value.date_value = value if field_name == ArchiveFieldName.DOCUMENT_DATE else None
    field_value.json_value = value if field_name == ArchiveFieldName.KEYWORDS else None
    field_value.review_status = payload.review_status
    field_value.source = FieldSource.MANUAL
    field_value.no_source_evidence = payload.no_source_evidence
    field_value.updated_by = actor_id
    field_value.updated_at = utc_now()
    session.add(field_value)
    session.execute(delete(FieldEvidence).where(FieldEvidence.field_value_id == field_value.id))
    for evidence in payload.evidences:
        session.add(
            FieldEvidence(
                field_value_id=field_value.id,
                snapshot_id=archive_document.current_snapshot_id,
                excerpt=evidence.excerpt,
                location_type=evidence.location_type,
                location_start=evidence.location_start,
                location_end=evidence.location_end,
                normalized_anchor=evidence.normalized_anchor,
            )
        )

    next_status = archive_document.status
    if archive_document.status == ArchiveDocumentStatus.CONFIRMED:
        next_status = ArchiveDocumentStatus.PENDING_RECONFIRMATION
    now = utc_now()
    values = {
        "version": ArchiveDocument.version + 1,
        "status": next_status,
        "updated_at": now,
    }
    if next_status == ArchiveDocumentStatus.PENDING_RECONFIRMATION:
        values.update(
            {
                "confirmed_by": None,
                "confirmed_at": None,
                "final_index_snapshot_hash": None,
                "final_chunk_count": 0,
            }
        )
    result = session.execute(
        update(ArchiveDocument)
        .where(
            ArchiveDocument.document_id == document.id,
            ArchiveDocument.version == payload.expected_version,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        session.rollback()
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取草稿。")
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(500, "FIELD_UPDATE_FAILED", "字段保存失败。") from exc

    archive_document = _get_archive_document(document.id, session)
    field_values = _read_field_values(document.id, session)
    return _build_draft_response(
        document=document,
        archive_document=archive_document,
        field_values=field_values,
        session=session,
    )
