"""运行 AV1-P14 真实 Chroma 故障下的物理删除恢复验收。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlmodel import Session, select

from app.db import engine
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    Document,
    DocumentStatus,
    Project,
)
from app.services.archive.final_chunks import get_final_collection
from scripts.archive_v1_p14_acceptance import (
    DOCUMENT_LABEL_PATH,
    AcceptanceError,
    P14Api,
    _as_object_list,
    _cleanup_seeded_scope,
    _read_label,
    _register_and_login,
    _seed_confirmed_documents,
    write_aggregate_result,
    write_safe_diagnostic,
)


_REQUIRED_CHECKS = frozenset(
    {
        "failure_reported",
        "visibility_blocked",
        "file_preserved_before_retry",
        "recovery_delete_succeeded",
        "postgres_zero",
        "chroma_zero",
        "file_zero",
        "audit_retained",
        "cleanup_completed",
    }
)


def is_conservative_incomplete_delete_response(payload: object) -> bool:
    """只接受公开的、不会泄露下游故障细节的删除未完成错误。"""
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return isinstance(error, dict) and error.get("code") == "DOCUMENT_DELETE_INCOMPLETE"


def recovery_passed(checks: dict[str, bool]) -> bool:
    """所有数据库、向量、文件、审计和精确清理核验均成功才算 P13 通过。"""
    return _REQUIRED_CHECKS <= checks.keys() and all(
        checks[name] is True for name in _REQUIRED_CHECKS
    )


def file_cleanup_verified(*, stored_path: Path, document_record_missing: bool) -> bool:
    """要求业务记录和原文件都不存在，才能证明文件层已经清理。"""
    return document_record_missing and not stored_path.exists()


def _failed_delete_state(*, document_id: UUID) -> tuple[bool, bool, Path | None]:
    """读取故障后状态和原文件存在性，不返回任何资源标识或路径。"""
    with Session(engine) as session:
        document = session.get(Document, document_id)
        archive_document = session.get(ArchiveDocument, document_id)
        operation = session.exec(
            select(ArchiveOperation)
            .where(
                ArchiveOperation.document_id == document_id,
                ArchiveOperation.operation_type == ArchiveOperationType.DELETE,
            )
            .order_by(ArchiveOperation.updated_at.desc())
        ).first()
        if document is None or archive_document is None or operation is None:
            return False, False, None
        visibility_blocked = (
            document.status == DocumentStatus.DELETE_FAILED
            and operation.operation_status == ArchiveOperationStatus.FAILED
            and operation.visibility_blocking is True
        )
        stored_path = Path(document.storage_path)
        file_preserved = stored_path.is_file()
    return visibility_blocked, file_preserved, stored_path


def _is_hidden_from_catalog(*, api: P14Api, project_id: str, document_id: str) -> bool:
    """确认删除失败的档案已经不再出现在健康服务的正式目录。"""
    catalog = api.request(
        "GET",
        f"/projects/{project_id}/archives",
        expected_statuses=(200,),
    )
    if not isinstance(catalog, dict):
        return False
    items = catalog.get("items")
    return isinstance(items, list) and all(
        isinstance(item, dict) and str(item.get("document_id")) != document_id
        for item in items
    )


def _successful_delete_state(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    document_id: UUID,
    stored_path: Path,
) -> tuple[bool, bool, bool, bool]:
    """在恢复删除后分别核对 PostgreSQL、Chroma、原文件和脱敏审计。"""
    with Session(engine) as session:
        file_path = session.get(Document, document_id)
        postgres_zero = file_path is None and session.get(ArchiveDocument, document_id) is None
        audit_count = session.exec(
            select(func.count())
            .select_from(ArchiveAuditLog)
            .where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.resource_id == document_id,
                ArchiveAuditLog.operation_type == "DOCUMENT_DELETED",
            )
        ).one()
        file_zero = file_cleanup_verified(
            stored_path=stored_path,
            document_record_missing=file_path is None,
        )
        audit_retained = int(audit_count) == 1

    scope = {
        "$and": [
            {"user_id": str(user_id)},
            {"project_id": str(project_id)},
            {"kb_id": str(kb_id)},
            {"document_id": str(document_id)},
        ]
    }
    remaining = get_final_collection().get(where=scope, include=[])
    chroma_zero = isinstance(remaining, dict) and remaining.get("ids") == []
    return postgres_zero, chroma_zero, file_zero, audit_retained


def run_delete_recovery_acceptance(
    *,
    base_url: str,
    fault_base_url: str,
    result_file: Path | None = None,
    diagnostic_file: Path | None = None,
) -> dict[str, bool]:
    """验证真实 Chroma 断连时的隐藏、重试和跨存储最终清理。"""
    document_label = _read_label(DOCUMENT_LABEL_PATH)
    if not _as_object_list(document_label.get("normal_documents"), name="normal_documents"):
        raise AcceptanceError("P14 恢复验收没有可用的虚构文档。")

    api = P14Api(base_url)
    fault_api = P14Api(fault_base_url)
    user_id: UUID | None = None
    project_ids: dict[str, str] = {}
    seeded: list[tuple[str, str]] = []
    checks = {name: False for name in _REQUIRED_CHECKS}
    primary_error: BaseException | None = None
    stage = "registration"
    try:
        user_id, _ = _register_and_login(api, run_tag=uuid4().hex[:16])
        fault_api.headers = dict(api.headers)
        stage = "seed_confirmation"
        _seed_confirmed_documents(
            api,
            document_label=document_label,
            run_tag=uuid4().hex[:16],
            project_ids=project_ids,
            seeded=seeded,
        )
        if not seeded:
            raise AcceptanceError("P14 恢复验收未创建可删除的虚构文档。")
        target_project_id, target_document_id = seeded[0]
        target_uuid = UUID(target_document_id)
        with Session(engine) as session:
            project = session.get(Project, UUID(target_project_id))
            if project is None:
                raise AcceptanceError("P14 恢复验收缺少目标项目。")
            target_kb_id = project.kb_id

        stage = "fault_delete"
        fault_response = fault_api.request(
            "DELETE",
            f"/projects/{target_project_id}/documents/{target_document_id}",
            expected_statuses=(503,),
        )
        checks["failure_reported"] = is_conservative_incomplete_delete_response(
            fault_response
        )
        if not checks["failure_reported"]:
            raise AcceptanceError("故障删除未返回公开的保守错误。")
        (
            checks["visibility_blocked"],
            checks["file_preserved_before_retry"],
            stored_path,
        ) = _failed_delete_state(document_id=target_uuid)
        if (
            not checks["visibility_blocked"]
            or not checks["file_preserved_before_retry"]
            or stored_path is None
        ):
            raise AcceptanceError("故障删除未同时保留可见性阻断与原文件。")
        if not _is_hidden_from_catalog(
            api=api,
            project_id=target_project_id,
            document_id=target_document_id,
        ):
            raise AcceptanceError("故障删除文档仍出现在正式目录。")

        stage = "recovery_delete"
        api.request(
            "DELETE",
            f"/projects/{target_project_id}/documents/{target_document_id}",
            expected_statuses=(204,),
        )
        checks["recovery_delete_succeeded"] = True
        seeded.remove((target_project_id, target_document_id))
        (
            checks["postgres_zero"],
            checks["chroma_zero"],
            checks["file_zero"],
            checks["audit_retained"],
        ) = _successful_delete_state(
            user_id=user_id,
            project_id=UUID(target_project_id),
            kb_id=target_kb_id,
            document_id=target_uuid,
            stored_path=stored_path,
        )
        if not all(
            checks[name]
            for name in ("postgres_zero", "chroma_zero", "file_zero", "audit_retained")
        ):
            raise AcceptanceError("恢复删除后仍存在跨存储残留或审计缺失。")
    except BaseException as exc:
        primary_error = exc
    finally:
        cleanup_error: BaseException | None = None
        if user_id is not None:
            try:
                _cleanup_seeded_scope(
                    api,
                    seeded=seeded,
                    project_ids=project_ids,
                    user_id=user_id,
                )
                checks["cleanup_completed"] = True
            except BaseException as exc:
                cleanup_error = exc
        api.close()
        fault_api.close()
        if primary_error is None and cleanup_error is not None:
            primary_error = cleanup_error

    if primary_error is not None:
        if diagnostic_file is not None:
            write_safe_diagnostic(
                diagnostic_file,
                stage=stage,
                error=primary_error,
            )
        raise primary_error
    if not recovery_passed(checks):
        error = AcceptanceError("P14 跨存储恢复缺少必要成功核验。")
        if diagnostic_file is not None:
            write_safe_diagnostic(diagnostic_file, stage="verification", error=error)
        raise error
    if result_file is not None:
        write_aggregate_result(result_file, {**checks, "passed": True})
    return {**checks, "passed": True}


def main() -> int:
    """运行本机真实删除恢复验收并只输出聚合布尔结果。"""
    parser = argparse.ArgumentParser(description="运行 AV1-P14 真实删除恢复验收。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fault-base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--diagnostic-file", type=Path)
    args = parser.parse_args()
    try:
        result = run_delete_recovery_acceptance(
            base_url=args.base_url,
            fault_base_url=args.fault_base_url,
            result_file=args.result_file,
            diagnostic_file=args.diagnostic_file,
        )
    except (AcceptanceError, ValueError) as exc:
        print(f"P14 delete recovery evaluation failed: {type(exc).__name__}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
