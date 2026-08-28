"""实现 AV1-P08 首次 AI 建议的快照、证据和草稿持久化流程。"""

from dataclasses import dataclass
from datetime import date
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveDocumentType,
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
from app.schemas import ArchiveDraftRead
from app.services.archive_draft_service import (
    _build_draft_response,
    _read_field_values,
)
from app.services.model_service import get_chat_model


logger = logging.getLogger(__name__)


SUGGESTION_REGENERATED = "SUGGESTION_REGENERATED"
ARCHIVE_DOCUMENT_RESOURCE_TYPE = "ARCHIVE_DOCUMENT"


@dataclass(frozen=True, slots=True)
class SuggestedEvidence:
    """模型建议的一条、尚未绑定数据库 ID 的证据。"""

    excerpt: str
    location_type: EvidenceLocationType
    location_start: int
    location_end: int
    normalized_anchor: str | None


@dataclass(frozen=True, slots=True)
class SuggestedField:
    """模型建议的一个规范化字段值和证据集合。"""

    field_name: ArchiveFieldName
    text_value: str | None
    date_value: date | None
    json_value: list[str] | None
    evidences: tuple[SuggestedEvidence, ...]


_TEXT_FIELDS = {
    ArchiveFieldName.TITLE,
    ArchiveFieldName.DOCUMENT_TYPE,
    ArchiveFieldName.AUTHORING_ORGANIZATION,
    ArchiveFieldName.VERSION_NUMBER,
    ArchiveFieldName.PROJECT_STAGE,
}


def _suggestion_error(code: str, message: str, status_code: int = 422) -> AppError:
    """构造不暴露模型原始输出的稳定建议错误。"""
    return AppError(status_code, code, message)


def _snapshot_payload(snapshot: ParsedSnapshot) -> dict[str, Any]:
    """读取当前不可变快照，模型只接收快照内容而不是原文件路径。"""
    try:
        payload = json.loads(Path(snapshot.snapshot_storage_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(500, "PARSED_SNAPSHOT_READ_FAILED", "解析快照读取失败。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("fragments"), list):
        raise AppError(500, "PARSED_SNAPSHOT_INVALID", "解析快照结构无效。")
    return payload


def _build_prompt(snapshot_payload: dict[str, Any]) -> str:
    """构造固定、可审计的建议提示，不把模型调用上下文扩展到其他文档。"""
    return (
        "你是工程资料字段建议器。只能依据给定快照输出 JSON，不得补造原文没有的事实。"
        "JSON 顶层必须是 fields，对应七个固定字段；有值字段必须提供 evidences。\n"
        + json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True)
    )


def _response_content(response: Any) -> dict[str, Any]:
    """把 ChatModel 响应转换为 JSON 对象。"""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise _suggestion_error(
                "SUGGESTION_INVALID_OUTPUT",
                "模型输出无法解析为固定字段建议。",
            ) from exc
    if not isinstance(content, dict):
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型输出结构无效。")
    return content


def _validate_evidence(
    *,
    raw: Any,
    snapshot_payload: dict[str, Any],
) -> SuggestedEvidence:
    """校验证据定位属于当前快照中的一个解析片段。"""
    if not isinstance(raw, dict):
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型证据结构无效。")
    try:
        location_type = EvidenceLocationType(raw["location_type"])
        location_start = int(raw["location_start"])
        location_end = int(raw["location_end"])
        excerpt = str(raw["excerpt"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型证据字段不完整。") from exc
    if not excerpt or location_start < 1 or location_end < location_start:
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型证据定位无效。")
    if location_type != EvidenceLocationType.TEXT_LINE_RANGE and location_end != location_start:
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "页码或段落证据只能使用点定位。")
    locations = {
        (
            fragment.get("location_type"),
            fragment.get("location_start"),
            fragment.get("location_end"),
        )
        for fragment in snapshot_payload["fragments"]
        if isinstance(fragment, dict)
    }
    if (location_type.value, location_start, location_end) not in locations:
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型证据不属于当前解析快照。")
    anchor = raw.get("normalized_anchor")
    return SuggestedEvidence(
        excerpt=excerpt,
        location_type=location_type,
        location_start=location_start,
        location_end=location_end,
        normalized_anchor=str(anchor) if anchor is not None else None,
    )


def _parse_suggested_fields(
    *,
    response: Any,
    snapshot_payload: dict[str, Any],
) -> list[SuggestedField]:
    """将模型输出收敛为固定七字段，并验证非空 AI 值都有证据。"""
    raw_fields = _response_content(response).get("fields")
    if not isinstance(raw_fields, dict):
        raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型缺少 fields 对象。")

    suggestions: list[SuggestedField] = []
    for field_name in ArchiveFieldName:
        raw_field = raw_fields.get(field_name.value)
        if raw_field is None:
            continue
        if not isinstance(raw_field, dict):
            raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型字段结构无效。")
        text_value = raw_field.get("text_value")
        date_value = raw_field.get("date_value")
        json_value = raw_field.get("json_value")
        if field_name in _TEXT_FIELDS:
            if date_value is not None or json_value is not None:
                raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型字段值列不匹配。")
            text_value = str(text_value).strip() if text_value is not None else None
            if field_name == ArchiveFieldName.DOCUMENT_TYPE and text_value is not None and text_value not in {
                item.value for item in ArchiveDocumentType
            }:
                raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型资料类型不在固定字典中。")
            if field_name == ArchiveFieldName.PROJECT_STAGE and text_value is not None and text_value not in {
                item.value for item in ProjectStage
            }:
                raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型项目阶段不在固定字典中。")
        elif field_name == ArchiveFieldName.DOCUMENT_DATE:
            if text_value is not None or json_value is not None:
                raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型字段值列不匹配。")
            try:
                date_value = date.fromisoformat(str(date_value)) if date_value is not None else None
            except ValueError as exc:
                raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型日期格式无效。") from exc
        else:
            if text_value is not None or date_value is not None:
                raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型关键词值列不匹配。")
            if json_value is not None:
                if not isinstance(json_value, list):
                    raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型关键词值列不匹配。")
                if any(not isinstance(item, str) or not item.strip() for item in json_value):
                    raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型关键词列表无效。")
                json_value = [item.strip() for item in json_value]

        nonempty = (
            text_value is not None and bool(text_value)
        ) or date_value is not None or bool(json_value)
        raw_evidences = raw_field.get("evidences", [])
        if not isinstance(raw_evidences, list):
            raise _suggestion_error("SUGGESTION_INVALID_OUTPUT", "模型证据列表无效。")
        evidences = tuple(
            _validate_evidence(raw=evidence, snapshot_payload=snapshot_payload)
            for evidence in raw_evidences
        )
        if nonempty and not evidences:
            raise _suggestion_error("AI_FIELD_EVIDENCE_REQUIRED", "AI 非空字段缺少当前快照证据。")
        if nonempty:
            suggestions.append(
                SuggestedField(
                    field_name=field_name,
                    text_value=text_value if field_name in _TEXT_FIELDS else None,
                    date_value=date_value if field_name == ArchiveFieldName.DOCUMENT_DATE else None,
                    json_value=json_value if field_name == ArchiveFieldName.KEYWORDS else None,
                    evidences=evidences,
                )
            )
    return suggestions


def _record_suggestion_failure(
    *,
    archive_document: ArchiveDocument,
    error: AppError,
    session: Session,
) -> None:
    """首次建议失败时保留解析结果并记录受控失败状态。"""
    session.rollback()
    archive_document = session.get(ArchiveDocument, archive_document.document_id)
    if archive_document is None:
        return
    archive_document.status = ArchiveDocumentStatus.SUGGESTION_FAILED
    archive_document.last_error_code = error.code
    archive_document.last_error_summary = error.message
    archive_document.updated_at = utc_now()
    session.add(archive_document)
    session.commit()


def _invoke_model(*, prompt: str, document_id: UUID) -> Any:
    """调用建议模型；只对连接和超时故障自动重试一次。"""
    for attempt in range(2):
        try:
            return get_chat_model().invoke(prompt)
        except (TimeoutError, ConnectionError) as exc:
            if attempt == 0:
                logger.warning(
                    "archive_suggestion_model_retry document_id=%s error=%s",
                    document_id,
                    type(exc).__name__,
                )
                continue
            logger.exception("archive_suggestion_model_failed document_id=%s", document_id)
            raise AppError(503, "ARCHIVE_SUGGESTION_UNAVAILABLE", "DeepSeek 建议不可用。") from exc
        except AppError as exc:
            raise AppError(503, "ARCHIVE_SUGGESTION_UNAVAILABLE", "DeepSeek 建议不可用。") from exc
        except Exception as exc:
            logger.exception("archive_suggestion_model_failed document_id=%s", document_id)
            raise AppError(503, "ARCHIVE_SUGGESTION_UNAVAILABLE", "DeepSeek 建议不可用。") from exc
    raise AssertionError("model invocation loop must return or raise")


def _ensure_regenerate_allowed(field_values: list[ArchiveFieldValue]) -> None:
    """确认七字段仍是未人工编辑的 AI 草稿，才允许覆盖重生成。"""
    if len(field_values) != len(tuple(ArchiveFieldName)) or any(
        value.review_status != FieldReviewStatus.PENDING_CHECK
        or value.source == FieldSource.MANUAL
        or value.no_source_evidence
        for value in field_values
    ):
        raise AppError(
            409,
            "SUGGESTION_WOULD_OVERWRITE_MANUAL_DRAFT",
            "当前草稿已包含人工修改，不能用 AI 重新生成覆盖。",
        )


def _persist_suggestion_draft(
    *,
    document_id: UUID,
    actor_id: UUID,
    archive_document: ArchiveDocument,
    existing_values: list[ArchiveFieldValue],
    suggestions: list[SuggestedField],
    session: Session,
    audit_log: ArchiveAuditLog | None = None,
) -> ArchiveDraftRead:
    """在当前事务中替换 AI 字段、证据、版本和可选审计记录。"""
    now = utc_now()
    suggested_by_name = {suggestion.field_name: suggestion for suggestion in suggestions}
    if existing_values:
        existing_ids = [value.id for value in existing_values]
        session.execute(
            delete(FieldEvidence).where(FieldEvidence.field_value_id.in_(existing_ids))
        )
        session.execute(
            delete(ArchiveFieldValue).where(ArchiveFieldValue.document_id == document_id)
        )
        session.flush()
    field_values = [
        ArchiveFieldValue(
            document_id=document_id,
            field_name=field_name,
            text_value=(suggested_by_name[field_name].text_value if field_name in suggested_by_name else None),
            date_value=(suggested_by_name[field_name].date_value if field_name in suggested_by_name else None),
            json_value=(suggested_by_name[field_name].json_value if field_name in suggested_by_name else None),
            review_status=FieldReviewStatus.PENDING_CHECK,
            source=(FieldSource.AI if field_name in suggested_by_name else None),
            no_source_evidence=False,
            updated_by=(actor_id if field_name in suggested_by_name else None),
            updated_at=now,
        )
        for field_name in ArchiveFieldName
    ]
    session.add_all(field_values)
    session.flush()
    for field_value in field_values:
        suggestion = suggested_by_name.get(field_value.field_name)
        if suggestion is None:
            continue
        for evidence in suggestion.evidences:
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
    archive_document.status = ArchiveDocumentStatus.PENDING_CONFIRMATION
    archive_document.version += 1
    archive_document.last_error_code = None
    archive_document.last_error_summary = None
    archive_document.updated_at = now
    session.add(archive_document)
    if audit_log is not None:
        session.add(audit_log)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AppError(500, "SUGGESTION_SAVE_FAILED", "AI 建议保存失败。") from exc
    session.refresh(archive_document)
    return _build_draft_response(
        document=_document_for_response(document_id, session),
        archive_document=archive_document,
        field_values=_read_field_values(document_id, session),
        session=session,
    )


def _generate_suggestions(
    *,
    document_id: UUID,
    actor_id: UUID,
    expected_status: ArchiveDocumentStatus,
    session: Session,
) -> ArchiveDraftRead:
    """执行一次首次建议或失败重试，并在成功时替换 AI 草稿。"""
    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document_id)
        .with_for_update()
    ).first()
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.status != expected_status:
        code = (
            "SUGGESTION_RETRY_NOT_ALLOWED"
            if expected_status == ArchiveDocumentStatus.SUGGESTION_FAILED
            else "SUGGESTION_NOT_ALLOWED"
        )
        raise AppError(409, code, "当前文档状态不允许生成 AI 建议。")

    existing_values = _read_field_values(document_id, session)
    if expected_status == ArchiveDocumentStatus.PARSED and existing_values:
        raise AppError(409, "SUGGESTION_ALREADY_STARTED", "AI 或人工草稿已经存在。")
    if expected_status == ArchiveDocumentStatus.SUGGESTION_FAILED and any(
        value.source == FieldSource.MANUAL for value in existing_values
    ):
        raise AppError(
            409,
            "SUGGESTION_WOULD_OVERWRITE_MANUAL_DRAFT",
            "当前失败状态已存在人工字段，不能用 AI 重试覆盖。",
        )
    if archive_document.current_snapshot_id is None:
        raise AppError(409, "SUGGESTION_SNAPSHOT_REQUIRED", "生成建议前必须存在解析快照。")
    snapshot = session.get(ParsedSnapshot, archive_document.current_snapshot_id)
    if snapshot is None:
        raise AppError(500, "PARSED_SNAPSHOT_NOT_FOUND", "当前解析快照不存在。")
    snapshot_payload = _snapshot_payload(snapshot)

    try:
        response = _invoke_model(
            prompt=_build_prompt(snapshot_payload),
            document_id=document_id,
        )
        suggestions = _parse_suggested_fields(
            response=response,
            snapshot_payload=snapshot_payload,
        )
    except AppError as exc:
        _record_suggestion_failure(
            archive_document=archive_document,
            error=exc,
            session=session,
        )
        raise

    return _persist_suggestion_draft(
        document_id=document_id,
        actor_id=actor_id,
        archive_document=archive_document,
        existing_values=existing_values,
        suggestions=suggestions,
        session=session,
    )


def create_suggestions(
    *,
    document_id: UUID,
    actor_id: UUID,
    session: Session,
) -> ArchiveDraftRead:
    """从当前解析快照生成首次 AI 草稿。"""
    return _generate_suggestions(
        document_id=document_id,
        actor_id=actor_id,
        expected_status=ArchiveDocumentStatus.PARSED,
        session=session,
    )


def retry_suggestions(
    *,
    document_id: UUID,
    actor_id: UUID,
    session: Session,
) -> ArchiveDraftRead:
    """仅从 ``SUGGESTION_FAILED`` 状态重试 AI 建议。"""
    return _generate_suggestions(
        document_id=document_id,
        actor_id=actor_id,
        expected_status=ArchiveDocumentStatus.SUGGESTION_FAILED,
        session=session,
    )


def regenerate_suggestions(
    *,
    document_id: UUID,
    actor_id: UUID,
    expected_version: int,
    session: Session,
) -> ArchiveDraftRead:
    """安全重新生成未人工编辑的 AI 草稿，并用版本条件原子替换。"""
    document = _document_for_response(document_id, session)
    archive_document = session.get(ArchiveDocument, document_id)
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.status != ArchiveDocumentStatus.PENDING_CONFIRMATION:
        raise AppError(409, "SUGGESTION_REGENERATE_NOT_ALLOWED", "当前文档状态不允许重新生成建议。")
    if archive_document.version != expected_version:
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取草稿。")
    existing_values = _read_field_values(document_id, session)
    _ensure_regenerate_allowed(existing_values)
    if archive_document.current_snapshot_id is None:
        raise AppError(409, "SUGGESTION_SNAPSHOT_REQUIRED", "生成建议前必须存在解析快照。")
    snapshot_id = archive_document.current_snapshot_id
    snapshot = session.get(ParsedSnapshot, snapshot_id)
    if snapshot is None:
        raise AppError(500, "PARSED_SNAPSHOT_NOT_FOUND", "当前解析快照不存在。")
    snapshot_payload = _snapshot_payload(snapshot)

    # 模型调用不在数据库锁事务内；返回后必须再次检查版本和人工状态。
    session.rollback()
    response = _invoke_model(
        prompt=_build_prompt(snapshot_payload),
        document_id=document_id,
    )
    suggestions = _parse_suggested_fields(
        response=response,
        snapshot_payload=snapshot_payload,
    )

    archive_document = session.exec(
        select(ArchiveDocument)
        .where(ArchiveDocument.document_id == document_id)
        .with_for_update()
    ).first()
    if archive_document is None:
        raise AppError(500, "ARCHIVE_DOCUMENT_NOT_FOUND", "归档文档记录不存在。")
    if archive_document.version != expected_version:
        raise AppError(409, "VERSION_CONFLICT", "文档版本已变化，请重新读取草稿。")
    if archive_document.status != ArchiveDocumentStatus.PENDING_CONFIRMATION:
        raise AppError(409, "SUGGESTION_REGENERATE_NOT_ALLOWED", "当前文档状态不允许重新生成建议。")
    if archive_document.current_snapshot_id != snapshot_id:
        raise AppError(409, "VERSION_CONFLICT", "解析快照已变化，请重新读取草稿。")
    existing_values = _read_field_values(document_id, session)
    _ensure_regenerate_allowed(existing_values)
    if document.project_id is None:
        raise AppError(500, "PROJECT_NOT_FOUND", "项目文档缺少项目归属。")

    audit_log = ArchiveAuditLog(
        project_id=document.project_id,
        actor_id=actor_id,
        operation_type=SUGGESTION_REGENERATED,
        resource_type=ARCHIVE_DOCUMENT_RESOURCE_TYPE,
        resource_id=document_id,
        redacted_summary={"version": expected_version + 1},
    )
    return _persist_suggestion_draft(
        document_id=document_id,
        actor_id=actor_id,
        archive_document=archive_document,
        existing_values=existing_values,
        suggestions=suggestions,
        session=session,
        audit_log=audit_log,
    )


def _document_for_response(document_id: UUID, session: Session) -> Document:
    """读取建议响应所需的原文件记录。"""
    document = session.get(Document, document_id)
    if document is None:
        raise AppError(500, "DOCUMENT_NOT_FOUND", "原文件记录不存在。")
    return document
