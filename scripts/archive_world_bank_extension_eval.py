"""在隔离运行资源中准备 FR-039 World Bank 扩展来源 Pixie 候选集。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic, sleep
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import ValidationError

from app.schemas.archive_retrieval import ArchiveRetrievalItemRead


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = PROJECT_ROOT / "tests" / "pytest_docs" / "public_projects" / "world_bank_belarus_m6"
REQUIRED_CASE_IDS = (
    "LUSHAN-02",
    "LUSHAN-03",
    "WB-REV-LOAN-01",
    "WB-CONTEXT-NONLOAN-01",
    "WB-ENTITY-NEG-01",
    "WB-UNSUPPORTED-DRAW-01",
)
CONDITION_GROUPS = (
    ("LUSHAN-02", "original"),
    ("LUSHAN-03", "original"),
    ("WB-REV-LOAN-01", "original"),
    ("WB-REV-LOAN-01", "supplementary"),
    ("WB-CONTEXT-NONLOAN-01", "original"),
    ("WB-ENTITY-NEG-01", "original"),
    ("WB-UNSUPPORTED-DRAW-01", "original"),
)
REPETITIONS = 3
EXPECTED_CANDIDATE_COUNT = 8
EXPECTED_ENTRY_COUNT = len(CONDITION_GROUPS) * REPETITIONS
_SAFE_RETRIEVAL_CASE_IDS = {
    "LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04",
    "WB-REV-LOAN-01", "WB-CONTEXT-NONLOAN-01",
    "WB-ENTITY-NEG-01", "WB-UNSUPPORTED-DRAW-01",
    "WB-UNSUPPORTED-DRAW-GATED-01",
}
_DIAGNOSTIC_METHODS = ("original", "supplementary")
_SAFE_RETRIEVAL_OPERATION_CODES = {
    "HTTP_REQUEST", "SEED_CONFIRM", "SEED_FIELD_UPDATE", "SEED_MANUAL_DRAFT",
    "SEED_PARSE", "SEED_PROJECT_CREATE", "SEED_UPLOAD",
}
_SAFE_DIAGNOSTIC_EXCEPTION_TYPES = {
    "AttributeError", "IndexError", "KeyError", "OverflowError",
    "RuntimeError", "TypeError", "ValueError",
}
_SAFE_CODES = {
    "CONFIGURATION_INVALID", "SOURCE_MANIFEST_INVALID", "TARGET_EXISTS",
    "OUTPUT_PATH_NOT_IGNORED", "SCHEMA_BINDING_MISMATCH", "API_START_FAILED",
    "API_NOT_READY", "RETRIEVAL_FAILED", "DATASET_INCOMPLETE", "CLEANUP_FAILED",
    "PREPARATION_FAILED",
}
_RESIDUAL_FLAGS = {
    "postgres_zero", "chroma_zero", "files_zero", "checkpoint_zero",
}
_SAFE_RETRIEVAL_STAGES = {
    "prepare.empty_target_validation", "prepare.source_validation",
    "prepare.empty_target.current_schema",
    "prepare.empty_target.namespace_contract",
    "prepare.empty_target.embedding_mode",
    "prepare.empty_target.chroma_collections",
    "prepare.empty_target.postgres_count",
    "prepare.empty_target.vector_count",
    "prepare.empty_target.file_count",
    "prepare.empty_target.checkpoint_count",
    "prepare.identity_registration", "prepare.document_seeding",
    "prepare.candidate_query_ranking", "cleanup",
}
_SAFE_CLEANUP_STEPS = {
    "BUSINESS_SCOPE_CLEANUP", "API_CLOSE", "CHROMA_DATABASE_DELETE",
    "LOCAL_TARGET_DELETE", "ZERO_RESIDUAL_VERIFY",
}
_SAFE_RESIDUAL_PROBE_STAGES = {
    "residuals.chroma_list", "residuals.chroma_match", "residuals.postgres_count",
    "residuals.files_check", "residuals.checkpoint_check",
    "residuals.validate",
}
_SAFE_FAILURE_CODES = {
    "EXPECTED_REJECTION", "SQLALCHEMY_CONNECTION", "SQLALCHEMY_DRIVER",
    "SQLALCHEMY_QUERY", "FILESYSTEM", "UNKNOWN",
}
_SAFE_FAILURE_STAGES = {
    "prepare.build_run_paths", "prepare.load_settings", "prepare.build_targets",
    "prepare.validate_configuration",
    "prepare.verify_sources", "prepare.inspect_targets", "prepare.create_schema",
    "prepare.verify_search_path", "prepare.create_chroma_database",
    "prepare.create_run_directory", "prepare.configure_isolated_runtime",
    "prepare.start_api", "prepare.wait_api_ready", "prepare.run_retrieval",
    "prepare.sanitize_retrieval_diagnostics", "prepare.select_supported_groups",
    "prepare.build_pixie_dataset", "prepare.write_artifacts",
    "cleanup.stop_api", "cleanup.shutdown_logging", "cleanup.cleanup_retrieval",
    "cleanup.dispose_runtime", "cleanup.drop_schema", "cleanup.delete_chroma_database",
    "cleanup.delete_run_directory", "cleanup.delete_log_directory", "cleanup.verify_cleanup",
}


def _ensure_project_root_on_sys_path() -> None:
    """确保直接执行本脚本时可导入项目内的 app 与 scripts 包。"""
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


class PreparationError(RuntimeError):
    """只携带安全错误码的准备流程错误。"""

    def __init__(self, safe_code: str) -> None:
        self.safe_code = safe_code if safe_code in _SAFE_CODES else "PREPARATION_FAILED"
        super().__init__(self.safe_code)


@dataclass(frozen=True)
class RunPaths:
    """一轮验收拥有的隔离路径。"""

    run_id: str
    root: Path
    runtime_root: Path
    result_dir: Path
    candidate_file: Path
    summary_file: Path
    file_storage: Path
    checkpoint: Path
    log_root: Path
    log_file: Path


@dataclass(frozen=True)
class Targets:
    """一次运行中使用的独占持久资源名。"""

    schema: str
    chroma_tenant: str
    chroma_database: str
    chroma_collection: str
    paths: RunPaths


class CandidateCaptureMap(dict[str, list[dict[str, Any]]]):
    """适配既有验收器键名并截留其生产检索 Top-8。"""

    def __setitem__(self, key: str, value: list[dict[str, Any]]) -> None:
        case_id, separator, method = key.rpartition("/")
        if not separator or method not in {"original", "supplementary"}:
            raise PreparationError("RETRIEVAL_FAILED")
        dict.__setitem__(self, f"{case_id}/{method}", list(value[:EXPECTED_CANDIDATE_COUNT]))


@dataclass(frozen=True)
class PreparationDependencies:
    """可替换的外部资源操作；单测以纯内存替身提供全部实现。"""

    load_settings: Callable[[], Any]
    validate_configuration: Callable[[Any], Any]
    verify_sources: Callable[[], Any]
    inspect_targets: Callable[[Targets], Any]
    create_schema: Callable[[Targets], Any]
    verify_search_path: Callable[[Targets], Any]
    create_chroma_database: Callable[[Targets], Any]
    create_run_directory: Callable[[RunPaths], Any]
    configure_isolated_runtime: Callable[[Any, Targets], Any]
    start_api: Callable[[Any], Any]
    wait_api_ready: Callable[[Any], Any]
    run_retrieval: Callable[[str, CandidateCaptureMap], Mapping[str, Any]]
    stop_api: Callable[[Any], Any]
    dispose_runtime: Callable[[], Any]
    shutdown_logging: Callable[[], Any]
    cleanup_retrieval: Callable[[], Any]
    drop_schema: Callable[[Targets], Any]
    delete_chroma_database: Callable[[Targets], Any]
    delete_run_directory: Callable[[RunPaths], Any]
    delete_log_directory: Callable[[RunPaths], Any]
    verify_cleanup: Callable[[Targets], Any]
    write_artifacts: Callable[[Targets, Mapping[str, Any]], Any]
    emit: Callable[[Mapping[str, Any]], Any]


def is_git_ignored(path: Path) -> bool:
    """通过 Git 确认给定目录确实处于忽略范围。"""
    try:
        relative = path.resolve().relative_to(PROJECT_ROOT.resolve())
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def build_run_paths(run_id: str) -> RunPaths:
    """构造并验证唯一运行目录及 OS 临时日志目录。"""
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise PreparationError("OUTPUT_PATH_NOT_IGNORED")
    corpus_root = PROJECT_ROOT / "tests" / "pytest_docs" / "public_projects" / "world_bank_belarus_m6"
    root = corpus_root / "acceptance" / run_id
    runtime_root = PROJECT_ROOT / "tests" / "pytest_docs" / "acceptance-runs" / run_id
    log_root = Path(tempfile.gettempdir()) / f"mini-rag-wb-eval-{run_id}"
    paths = RunPaths(
        run_id=run_id,
        root=root,
        runtime_root=runtime_root,
        result_dir=root,
        candidate_file=root / "extension-candidates.json",
        summary_file=root / "summary.json",
        file_storage=runtime_root / "files",
        checkpoint=runtime_root / "checkpoints.sqlite",
        log_root=log_root,
        log_file=log_root / "logs" / "app.log",
    )
    if root not in paths.candidate_file.parents or root not in paths.summary_file.parents:
        raise PreparationError("OUTPUT_PATH_NOT_IGNORED")
    if not is_git_ignored(root) or not is_git_ignored(runtime_root):
        raise PreparationError("OUTPUT_PATH_NOT_IGNORED")
    if root in log_root.parents or log_root in root.parents:
        raise PreparationError("OUTPUT_PATH_NOT_IGNORED")
    validate_paths_absent(paths)
    return paths


def validate_paths_absent(paths: RunPaths) -> None:
    """拒绝复用本轮运行、结果或日志目录中的任何既有路径。"""
    if any(path.exists() for path in (paths.runtime_root, paths.result_dir, paths.log_root)):
        raise PreparationError("TARGET_EXISTS")


def build_targets(run_id: str, paths: RunPaths, *, chroma_tenant: str) -> Targets:
    """用同一随机后缀派生 PostgreSQL 与 Chroma 资源名。"""
    if not re.fullmatch(r"[0-9a-f]{16}", run_id) or paths.run_id != run_id:
        raise PreparationError("TARGET_EXISTS")
    return Targets(
        schema=f"fr042_wb_{run_id}",
        chroma_tenant=chroma_tenant,
        chroma_database=f"fr042_wb_db_{run_id}",
        chroma_collection=f"fr042_wb_collection_{run_id}",
        paths=paths,
    )


def _case_specs() -> dict[str, dict[str, Any]]:
    """返回来源标注已确认的问题和 evaluator 预期字段。"""
    return {
        "LUSHAN-02": {
            "question": "项目延期后的关闭日期是什么？请按英文日期格式回答。",
            "expectation": {"answer_status": "ANSWERED", "answer_fragments": ["December 31, 2023"],
                            "frozen_target": {"filename": "2022_restructuring_paper.pdf", "page": 8},
                            "extension_sources": []},
            "status": "ANSWERED", "fragments": ["December 31, 2023"],
            "pages": [("2022_restructuring_paper.pdf", 8)],
        },
        "LUSHAN-03": {
            "question": "独立复核给出的项目总体结果评级是什么？",
            "expectation": {"answer_status": "ANSWERED", "answer_fragments": ["Satisfactory"],
                            "frozen_target": {"filename": "2024_completion_report_review.pdf", "page": 20},
                            "extension_sources": []},
            "status": "ANSWERED", "fragments": ["Satisfactory"],
            "pages": [("2024_completion_report_review.pdf", 20)],
        },
        "WB-REV-LOAN-01": {
            "question": "白俄罗斯 M6 交通走廊改善项目的世界银行贷款金额是多少？",
            "expectation": {"answer_status": "ANSWERED", "answer_fragments": [],
                            "answer_alternatives": [[
                                "US$250 million", "250 million US Dollars", "250百万美元", "2.5亿美元"
                            ]],
                            "frozen_target": None, "extension_sources": [
                                {"filename": "belarus-snapshot.pdf", "page": 16}]},
            "status": "ANSWERED", "fragments": [],
            "pages": [("belarus-snapshot.pdf", 7), ("belarus-snapshot.pdf", 16)],
        },
        "WB-CONTEXT-NONLOAN-01": {
            "question": "世界银行 M6 项目中，Bruzgi 口岸预计每日可处理多少辆卡车？",
            "expectation": {"answer_status": "ANSWERED", "answer_fragments": ["1,700"],
                            "frozen_target": None, "extension_sources": [
                                {"filename": "belarus-snapshot.pdf", "page": 16}]},
            "status": "ANSWERED", "fragments": ["1,700"],
            "pages": [("belarus-snapshot.pdf", 16)],
        },
        "WB-ENTITY-NEG-01": {
            "question": "IFC 为白俄罗斯 M6 项目提供了多少贷款？",
            "expectation": {"answer_status": "REFUSED_NO_EVIDENCE", "answer_fragments": [],
                            "frozen_target": None, "extension_sources": []},
            "status": "REFUSED_NO_EVIDENCE", "fragments": [],
            "pages": [("belarus-snapshot.pdf", 16)],
        },
        "WB-UNSUPPORTED-DRAW-01": {
            "question": "白俄罗斯 M6 项目当前或完工时的实际提款金额是多少？",
            "expectation": {"answer_status": "REFUSED_NO_EVIDENCE", "answer_fragments": [],
                            "frozen_target": None, "extension_sources": []},
            "status": "REFUSED_NO_EVIDENCE", "fragments": [],
            "pages": [("belarus-snapshot.pdf", 16)],
        },
    }


def build_pixie_dataset(
    candidate_capture: Mapping[str, list[dict[str, Any]]],
    *,
    supported_groups: Mapping[str, bool],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """从真实 Top-8 构造七组、三样本 Pixie 条目并列出安全失败码。"""
    specs = _case_specs()
    group_entries: dict[tuple[str, str], dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for case_id, method in CONDITION_GROUPS:
        spec = specs[case_id]
        group_key = f"{case_id}/{method}"
        candidates = candidate_capture.get(group_key)
        if not isinstance(candidates, list) or len(candidates) != EXPECTED_CANDIDATE_COUNT:
            failures.append({"case_id": case_id, "safe_error_code": "TOP8_INCOMPLETE"})
            continue
        normalized: list[dict[str, Any]] = []
        malformed = False
        for candidate in candidates:
            filename = candidate.get("filename")
            excerpt = candidate.get("excerpt")
            start = candidate.get("location_start")
            end = candidate.get("location_end")
            if (
                not isinstance(filename, str) or not filename
                or not isinstance(excerpt, str) or not excerpt.strip()
                or type(start) is not int or type(end) is not int
                or start < 1 or end < start
            ):
                malformed = True
                break
            try:
                normalized.append(
                    ArchiveRetrievalItemRead.model_validate(candidate).model_dump(
                        mode="json"
                    )
                )
            except ValidationError:
                malformed = True
                break
        if malformed:
            failures.append({"case_id": case_id, "safe_error_code": "TOP8_MALFORMED"})
            continue
        is_positive = spec["status"] == "ANSWERED"
        if is_positive and supported_groups.get(group_key) is not True:
            failures.append({"case_id": case_id, "safe_error_code": "SUPPORT_EVIDENCE_MISSING"})
            continue
        if not is_positive and supported_groups.get(group_key) is True:
            failures.append({"case_id": case_id, "safe_error_code": "NEGATIVE_SUPPORT_CONFLICT"})
            continue
        entry: dict[str, Any] = {
            "description": f"{case_id} 扩展来源 {method} 排序",
            "input_data": {"question": spec["question"]},
            "eval_input": [{
                "name": "archive_question_retrieval",
                "value": {
                    "items": normalized,
                    "requested_top_k": EXPECTED_CANDIDATE_COUNT,
                    "returned_count": len(normalized),
                },
            }],
            "expectation": spec["expectation"],
            "eval_metadata": {
                "case_id": case_id,
                "category": "GROUNDED" if is_positive else "NO_EVIDENCE",
                "difficulty": "challenging" if case_id.startswith("WB-") else "routine",
                "source_provenance": "WORLD_BANK_EXTENSION_AND_P153548",
                "capabilities": ["evidence_qa", "citations"] if is_positive else ["evidence_qa", "refusal"],
                "expected_turn_count": 1,
                "expected_answer_status": spec["status"],
                "expected_answer_fragments": spec["fragments"],
                "expected_document_filenames": [filename for filename, _ in spec["pages"]],
                "expected_document_pages": [page for _, page in spec["pages"]],
                "ranking_method": method,
            },
            "evaluators": ["evals/archive/world_bank_evaluators.py:world_bank_answer_evaluator"],
        }
        group_entries[(case_id, method)] = entry
    entries: list[dict[str, Any]] = []
    for sample in range(1, REPETITIONS + 1):
        for case_id, method in CONDITION_GROUPS:
            template = group_entries.get((case_id, method))
            if template is None:
                continue
            entry = json.loads(json.dumps(template, ensure_ascii=False))
            entry["eval_metadata"]["sample"] = sample
            entries.append(entry)
    if len(entries) > EXPECTED_ENTRY_COUNT:
        raise PreparationError("DATASET_INCOMPLETE")
    return {
        "name": "world-bank-extension",
        "runnable": "evals/archive/world_bank_runnable.py:WorldBankQuestionRunnable",
        "entries": entries,
    }, failures


def supported_groups_from_summary(summary: Mapping[str, Any]) -> dict[str, bool]:
    """独立读取 Top-8 排序诊断中的 supports_answer 门控结果。"""
    output: dict[str, bool] = {}
    for case in summary.get("cases", []):
        case_id = str(case.get("case_id", ""))
        rankings = case.get("rankings", {})
        if not isinstance(rankings, Mapping):
            continue
        for method in ("original", "supplementary"):
            rows = rankings.get(method)
            if not isinstance(rows, list) or not rows:
                continue
            top_rows = [row for row in rows[:EXPECTED_CANDIDATE_COUNT] if isinstance(row, Mapping)]
            if case_id == "WB-REV-LOAN-01":
                output[f"{case_id}/{method}"] = any(
                    row.get("source_page") == 16 and row.get("supports_answer") is True
                    for row in top_rows
                )
                continue
            output[f"{case_id}/{method}"] = any(
                row.get("supports_answer") is True
                for row in top_rows
            )
    return output


def prepare_extension_eval(
    dependencies: PreparationDependencies,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """依照先预检、后创建、再运行、最终逐层清理的次序准备候选数据。"""
    identifier = run_id or uuid4().hex[:16]
    paths = build_run_paths(identifier)
    targets: Targets | None = None
    settings: Any = None
    api: Any = None
    schema_created = False
    chroma_created = False
    directory_owned = False
    runtime_configured = False
    api_started = False
    retrieval_started = False
    creation_attempted = False
    cleanup_failed = False
    source_verified = False
    result_dataset: dict[str, Any] = {"name": "world-bank-extension", "entries": []}
    retrieval_diagnostics: dict[str, Any] | None = None
    failures: list[dict[str, str]] = []
    summary: dict[str, Any] = {
        "status": "failed", "planned_group_count": len(CONDITION_GROUPS),
        "source_hash_verified": False, "cleanup_verified": False,
        "run_id": identifier,
    }
    primary_code = "PREPARATION_FAILED"
    current_stage = "prepare.load_settings"
    failure_stage: str | None = None
    primary_error_code: str | None = None
    primary_failure_stage: str | None = None
    cleanup_error_code: str | None = None
    cleanup_failure_stage: str | None = None
    zero_residuals: dict[str, bool] | None = None
    retrieval_stage: str | None = None
    cleanup_failed_steps: list[str] | None = None
    residual_probe_stage: str | None = None
    preflight_error_code: str | None = None
    residual_probe_error_code: str | None = None
    retrieval_operation_code: str | None = None
    retrieval_http_status: int | None = None
    diagnostic_exception_type: str | None = None
    diagnostic_error_line: int | None = None

    def mark_cleanup_failure(stage: str, error: BaseException | None = None) -> None:
        nonlocal failure_stage, cleanup_error_code, cleanup_failure_stage
        nonlocal zero_residuals, retrieval_stage, cleanup_failed_steps, residual_probe_stage
        nonlocal residual_probe_error_code
        if not failure_stage or not failure_stage.startswith("cleanup."):
            failure_stage = stage
        cleanup_error_code = "CLEANUP_FAILED"
        if cleanup_failure_stage is None:
            cleanup_failure_stage = stage
        if error is not None:
            zero_residuals = _safe_residual_map(getattr(error, "zero_residuals", None)) or zero_residuals
            retrieval_stage = _safe_retrieval_stage(
                getattr(error, "retrieval_stage", None)
            ) or retrieval_stage
            cleanup_failed_steps = _safe_cleanup_steps(
                getattr(error, "cleanup_failed_steps", None)
            ) or cleanup_failed_steps
            residual_probe_stage = _safe_residual_probe_stage(
                getattr(error, "residual_probe_stage", None)
            ) or residual_probe_stage
            if stage == "cleanup.cleanup_retrieval":
                residual_probe_error_code = _safe_failure_code_value(
                    getattr(error, "residual_probe_error_code", None)
                )

    try:
        settings = dependencies.load_settings()
        current_stage = "prepare.build_targets"
        targets = build_targets(identifier, paths, chroma_tenant=settings.chroma_tenant)
        current_stage = "prepare.validate_configuration"
        dependencies.validate_configuration(settings)
        current_stage = "prepare.verify_sources"
        dependencies.verify_sources()
        source_verified = True
        summary["source_hash_verified"] = True
        current_stage = "prepare.inspect_targets"
        dependencies.inspect_targets(targets)
        creation_attempted = True
        current_stage = "prepare.create_schema"
        dependencies.create_schema(targets)
        schema_created = True
        # 此门必须先于 Chroma 建库、runtime 配置和 app.main 导入。
        current_stage = "prepare.verify_search_path"
        dependencies.verify_search_path(targets)
        current_stage = "prepare.create_chroma_database"
        dependencies.create_chroma_database(targets)
        chroma_created = True
        current_stage = "prepare.create_run_directory"
        dependencies.create_run_directory(paths)
        directory_owned = True
        runtime_configured = True
        current_stage = "prepare.configure_isolated_runtime"
        dependencies.configure_isolated_runtime(settings, targets)
        current_stage = "prepare.start_api"
        api = dependencies.start_api(settings)
        api_started = True
        current_stage = "prepare.wait_api_ready"
        if not dependencies.wait_api_ready(api):
            raise PreparationError("API_NOT_READY")
        base_url = _loopback_url(api)
        capture = CandidateCaptureMap()
        retrieval_started = True
        current_stage = "prepare.run_retrieval"
        retrieval_summary = dependencies.run_retrieval(base_url, capture)
        current_stage = "prepare.sanitize_retrieval_diagnostics"
        retrieval_diagnostics = _safe_retrieval_diagnostics(retrieval_summary)
        if retrieval_diagnostics is None:
            raise PreparationError("RETRIEVAL_FAILED")
        current_stage = "prepare.select_supported_groups"
        supported_groups = supported_groups_from_summary(retrieval_summary)
        current_stage = "prepare.build_pixie_dataset"
        result_dataset, failures = build_pixie_dataset(capture, supported_groups=supported_groups)
        candidate_entries = result_dataset["entries"]
        summary.update({
            "status": "completed" if len(candidate_entries) == EXPECTED_ENTRY_COUNT else "partial",
            "generated_entry_count": len(candidate_entries),
            "failures": failures,
            "source_manifest_verified": bool(source_verified),
        })
        if not candidate_entries:
            primary_code = "DATASET_INCOMPLETE"
            failure_stage = current_stage
            primary_error_code = primary_code
            primary_failure_stage = current_stage
    except PreparationError as exc:
        primary_code = exc.safe_code
        failure_stage = current_stage
        primary_error_code = primary_code
        primary_failure_stage = current_stage
        retrieval_stage = _safe_retrieval_stage(getattr(exc, "retrieval_stage", None))
        cleanup_failed_steps = _safe_cleanup_steps(
            getattr(exc, "cleanup_failed_steps", None)
        )
        residual_probe_stage = _safe_residual_probe_stage(
            getattr(exc, "residual_probe_stage", None)
        )
        zero_residuals = _safe_residual_map(getattr(exc, "zero_residuals", None))
        preflight_error_code = _safe_failure_code_value(
            getattr(exc, "preflight_error_code", None)
        )
        residual_probe_error_code = _safe_failure_code_value(
            getattr(exc, "residual_probe_error_code", None)
        )
        retrieval_operation_code = _safe_retrieval_operation_code(
            getattr(exc, "retrieval_operation_code", None)
        )
        retrieval_http_status = _safe_http_status(
            getattr(exc, "retrieval_http_status", None)
        )
    except BaseException as exc:
        primary_code = "PREPARATION_FAILED"
        failure_stage = current_stage
        primary_error_code = primary_code
        primary_failure_stage = current_stage
        if current_stage == "prepare.sanitize_retrieval_diagnostics":
            diagnostic_exception_type = _safe_diagnostic_exception_type(
                type(exc).__name__
            )
            diagnostic_error_line = _safe_diagnostic_error_line(exc)
    finally:
        cleanup_ok = False
        if creation_attempted:
            cleanup_ok = True
            api_stopped = not api_started
            if api_started:
                try:
                    dependencies.stop_api(api)
                    api_stopped = True
                except BaseException as exc:
                    cleanup_ok = False
                    mark_cleanup_failure("cleanup.stop_api", exc)

            runtime_disposed = not runtime_configured
            retrieval_cleaned = not retrieval_started
            logging_stopped = False
            if api_stopped:
                try:
                    dependencies.shutdown_logging()
                    logging_stopped = True
                except BaseException as exc:
                    cleanup_ok = False
                    mark_cleanup_failure("cleanup.shutdown_logging", exc)
                if retrieval_started:
                    try:
                        dependencies.cleanup_retrieval()
                        retrieval_cleaned = True
                    except BaseException as exc:
                        cleanup_ok = False
                        mark_cleanup_failure("cleanup.cleanup_retrieval", exc)
                if runtime_configured:
                    try:
                        dependencies.dispose_runtime()
                        runtime_disposed = True
                    except BaseException as exc:
                        cleanup_ok = False
                        mark_cleanup_failure("cleanup.dispose_runtime", exc)

            if schema_created and targets is not None and api_stopped and runtime_disposed:
                try:
                    dependencies.drop_schema(targets)
                except BaseException as exc:
                    cleanup_ok = False
                    mark_cleanup_failure("cleanup.drop_schema", exc)
            elif schema_created and api_stopped:
                cleanup_ok = False

            # 检索清理回调成功代表内层已核验四层归零，避免再次删除已不存在的 Chroma Database。
            if (
                chroma_created
                and targets is not None
                and api_stopped
                and not (retrieval_started and retrieval_cleaned)
            ):
                try:
                    dependencies.delete_chroma_database(targets)
                except BaseException as exc:
                    cleanup_ok = False
                    mark_cleanup_failure("cleanup.delete_chroma_database", exc)

            # 检索器清理失败可能仍有 Checkpoint 句柄，因此保留运行目录供恢复排查。
            if directory_owned and api_stopped and retrieval_cleaned:
                try:
                    dependencies.delete_run_directory(paths)
                except BaseException as exc:
                    cleanup_ok = False
                    mark_cleanup_failure("cleanup.delete_run_directory", exc)
            if api_stopped and logging_stopped:
                try:
                    dependencies.delete_log_directory(paths)
                except BaseException as exc:
                    cleanup_ok = False
                    mark_cleanup_failure("cleanup.delete_log_directory", exc)
            try:
                if targets is not None:
                    cleanup_verified = dependencies.verify_cleanup(targets) is True
                    cleanup_ok = cleanup_verified and cleanup_ok
                    if not cleanup_verified:
                        mark_cleanup_failure("cleanup.verify_cleanup")
            except BaseException as exc:
                cleanup_ok = False
                mark_cleanup_failure("cleanup.verify_cleanup", exc)
            cleanup_failed = not cleanup_ok
            summary["cleanup_verified"] = cleanup_ok
            if not cleanup_ok:
                summary["status"] = "failed"
                primary_code = "CLEANUP_FAILED"
            elif primary_code == "DATASET_INCOMPLETE":
                summary["status"] = "failed"
        if targets is not None:
            try:
                current_stage = "prepare.write_artifacts"
                dependencies.write_artifacts(targets, {
                    "dataset": result_dataset,
                    "summary": _safe_summary(
                        summary,
                        error_code=primary_code if summary["status"] == "failed" else None,
                        failure_stage=failure_stage,
                        primary_error_code=primary_error_code,
                        primary_failure_stage=primary_failure_stage,
                        cleanup_error_code=cleanup_error_code,
                        cleanup_failure_stage=cleanup_failure_stage,
                        zero_residuals=zero_residuals,
                        retrieval_stage=retrieval_stage,
                        cleanup_failed_steps=cleanup_failed_steps,
                        residual_probe_stage=residual_probe_stage,
                        preflight_error_code=preflight_error_code,
                        residual_probe_error_code=residual_probe_error_code,
                        retrieval_operation_code=retrieval_operation_code,
                        retrieval_http_status=retrieval_http_status,
                        diagnostic_exception_type=diagnostic_exception_type,
                        diagnostic_error_line=diagnostic_error_line,
                        retrieval_diagnostics=retrieval_diagnostics,
                    ),
                })
            except BaseException:
                summary["status"] = "failed"
                primary_code = "PREPARATION_FAILED"
                failure_stage = "prepare.write_artifacts"
        summary["failure_stage"] = failure_stage
        dependencies.emit(_safe_summary(
            summary,
            error_code=primary_code if summary["status"] == "failed" else None,
            failure_stage=failure_stage,
            primary_error_code=primary_error_code,
            primary_failure_stage=primary_failure_stage,
            cleanup_error_code=cleanup_error_code,
            cleanup_failure_stage=cleanup_failure_stage,
            zero_residuals=zero_residuals,
            retrieval_stage=retrieval_stage,
            cleanup_failed_steps=cleanup_failed_steps,
            residual_probe_stage=residual_probe_stage,
            preflight_error_code=preflight_error_code,
            residual_probe_error_code=residual_probe_error_code,
            retrieval_operation_code=retrieval_operation_code,
            retrieval_http_status=retrieval_http_status,
            diagnostic_exception_type=diagnostic_exception_type,
            diagnostic_error_line=diagnostic_error_line,
        ))
    if cleanup_failed or primary_code not in {"PREPARATION_FAILED", "DATASET_INCOMPLETE"}:
        if summary["status"] == "failed":
            raise PreparationError(primary_code)
    if summary["status"] == "failed" or summary.get("generated_entry_count", 0) == 0:
        raise PreparationError(primary_code)
    return _safe_summary(summary)


def _safe_summary(
    summary: Mapping[str, Any],
    *,
    error_code: str | None = None,
    failure_stage: str | None = None,
    primary_error_code: str | None = None,
    primary_failure_stage: str | None = None,
    cleanup_error_code: str | None = None,
    cleanup_failure_stage: str | None = None,
    zero_residuals: Mapping[str, Any] | None = None,
    retrieval_stage: str | None = None,
    cleanup_failed_steps: Any = None,
    residual_probe_stage: str | None = None,
    preflight_error_code: str | None = None,
    residual_probe_error_code: str | None = None,
    retrieval_operation_code: str | None = None,
    retrieval_http_status: int | None = None,
    diagnostic_exception_type: str | None = None,
    diagnostic_error_line: int | None = None,
    retrieval_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """保留 CLI 状态字段及可选的脱敏检索诊断。

    Args:
        summary: 当前准备或清理状态。
        error_code: 允许公开的主错误码。
        failure_stage: 允许公开的主失败阶段。
        primary_error_code: 允许公开的操作错误码。
        primary_failure_stage: 允许公开的操作失败阶段。
        cleanup_error_code: 允许公开的清理错误码。
        cleanup_failure_stage: 允许公开的清理失败阶段。
        zero_residuals: 四层资源是否归零。
        retrieval_stage: 检索验收器的安全阶段。
        cleanup_failed_steps: 发生失败的清理步骤码。
        residual_probe_stage: 残留探针的安全阶段。
        preflight_error_code: 隔离预检的安全错误码。
        residual_probe_error_code: 残留探针的安全错误码。
        retrieval_operation_code: 检索准备阶段的固定操作码。
        retrieval_http_status: 检索准备请求的 HTTP 状态码。
        diagnostic_exception_type: 脱敏诊断异常的固定内置类型名。
        diagnostic_error_line: 仅当堆栈命中本脱敏器函数时保留其源码行号。
        retrieval_diagnostics: 仅写入结果文件的脱敏检索观测。
    """
    output = {
        "status": summary.get("status"),
        "planned_group_count": len(CONDITION_GROUPS),
        "generated_entry_count": summary.get("generated_entry_count", 0),
        "source_hash_verified": bool(summary.get("source_hash_verified")),
        "cleanup_verified": bool(summary.get("cleanup_verified")),
        "failure_count": len(summary.get("failures", [])),
        "skipped_group_count": len(summary.get("failures", [])),
    }
    if error_code is not None:
        output["error_code"] = error_code
    safe_stage = failure_stage if failure_stage in _SAFE_FAILURE_STAGES else summary.get("failure_stage")
    if safe_stage in _SAFE_FAILURE_STAGES:
        output["failure_stage"] = safe_stage
    if primary_error_code in _SAFE_CODES:
        output["primary_error_code"] = primary_error_code
    if primary_failure_stage in _SAFE_FAILURE_STAGES:
        output["primary_failure_stage"] = primary_failure_stage
    if cleanup_error_code in _SAFE_CODES:
        output["cleanup_error_code"] = cleanup_error_code
    if cleanup_failure_stage in _SAFE_FAILURE_STAGES:
        output["cleanup_failure_stage"] = cleanup_failure_stage
    safe_residuals = _safe_residual_map(zero_residuals)
    if safe_residuals is not None:
        output["zero_residuals"] = safe_residuals
    safe_retrieval_stage = _safe_retrieval_stage(retrieval_stage)
    if safe_retrieval_stage is not None:
        output["retrieval_stage"] = safe_retrieval_stage
    safe_cleanup_steps = _safe_cleanup_steps(cleanup_failed_steps)
    if safe_cleanup_steps:
        output["cleanup_failed_steps"] = safe_cleanup_steps
    safe_probe_stage = _safe_residual_probe_stage(residual_probe_stage)
    if safe_probe_stage is not None:
        output["residual_probe_stage"] = safe_probe_stage
    safe_preflight_code = _safe_failure_code_value(preflight_error_code)
    if safe_preflight_code is not None:
        output["preflight_error_code"] = safe_preflight_code
    safe_probe_code = _safe_failure_code_value(residual_probe_error_code)
    if safe_probe_code is not None:
        output["residual_probe_error_code"] = safe_probe_code
    safe_operation_code = _safe_retrieval_operation_code(retrieval_operation_code)
    if safe_operation_code is not None:
        output["retrieval_operation_code"] = safe_operation_code
    safe_http_status = _safe_http_status(retrieval_http_status)
    if safe_http_status is not None:
        output["retrieval_http_status"] = safe_http_status
    safe_exception_type = _safe_diagnostic_exception_type(diagnostic_exception_type)
    if safe_exception_type is not None:
        output["diagnostic_exception_type"] = safe_exception_type
    safe_error_line = _safe_diagnostic_error_line_value(diagnostic_error_line)
    if safe_error_line is not None:
        output["diagnostic_error_line"] = safe_error_line
    safe_retrieval = _safe_retrieval_diagnostics(retrieval_diagnostics)
    if safe_retrieval is not None:
        output["retrieval_diagnostics"] = safe_retrieval
    return output


def _safe_retrieval_diagnostics(value: Any) -> dict[str, Any] | None:
    """只保留检索诊断所需的白名单字段，不复制问题、摘录或原始资源标识。

    Args:
        value: 检索验收器返回的内部诊断摘要。
    """
    if not isinstance(value, Mapping) or not isinstance(value.get("cases"), list):
        return None

    def count(raw: Any) -> int | None:
        return raw if type(raw) is int and 0 <= raw <= 100_000 else None

    def rank(raw: Any) -> int | None:
        return raw if type(raw) is int and 1 <= raw <= 100_000 else None

    def metric(raw: Any) -> float | None:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
            return round(float(raw), 3)
        return None

    output: dict[str, Any] = {}
    if value.get("evaluation") == "retrieval-only":
        output["evaluation"] = "retrieval-only"
    if value.get("model_calls") == 0:
        output["model_calls"] = 0
    for field in ("project_count",):
        clean_count = count(value.get(field))
        if clean_count is not None:
            output[field] = clean_count
    document_counts = value.get("documents_by_project")
    if isinstance(document_counts, Mapping):
        clean_documents = {
            key: count(document_counts.get(key))
            for key in ("lushan", "belarus")
            if count(document_counts.get(key)) is not None
        }
        if clean_documents:
            output["documents_by_project"] = clean_documents

    clean_cases: list[dict[str, Any]] = []
    for case in value["cases"][:32]:
        if not isinstance(case, Mapping):
            return None
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or case_id not in _SAFE_RETRIEVAL_CASE_IDS:
            return None
        clean: dict[str, Any] = {"case_id": case_id}
        for field, allowed in (
            ("project_key", {"lushan", "belarus"}),
            ("evidence_basis", {"frozen_target", "extension_source"}),
        ):
            if isinstance(case.get(field), str) and case[field] in allowed:
                clean[field] = case[field]
        for field in (
            "query_route_count", "original_dense_count", "supplementary_dense_count",
            "candidate_union_count", "candidate_intersection_count",
        ):
            clean_count = count(case.get(field))
            if clean_count is not None:
                clean[field] = clean_count

        dense_targets = case.get("dense_target_rank")
        if isinstance(dense_targets, Mapping):
            clean_dense: dict[str, Any] = {}
            for method in _DIAGNOSTIC_METHODS:
                dimensions = dense_targets.get(method)
                if not isinstance(dimensions, Mapping):
                    continue
                clean_dimensions = {}
                for dimension in (
                    "exact", "public", "retrieved_relevant_context", "supports_answer",
                ):
                    if dimension in dimensions:
                        clean_dimensions[dimension] = rank(dimensions[dimension])
                if clean_dimensions:
                    clean_dense[method] = clean_dimensions
            if clean_dense:
                clean["dense_target_rank"] = clean_dense

        page_ranks = case.get("target_page_ranks")
        if isinstance(page_ranks, Mapping):
            clean_pages: dict[str, Any] = {}
            for method in _DIAGNOSTIC_METHODS:
                pages = page_ranks.get(method)
                if not isinstance(pages, Mapping):
                    continue
                clean_page_ranks = {}
                for page, page_rank in pages.items():
                    if isinstance(page, str) and page.isdecimal() and 1 <= int(page) <= 10_000:
                        clean_page_ranks[page] = rank(page_rank)
                if clean_page_ranks:
                    clean_pages[method] = clean_page_ranks
            if clean_pages:
                clean["target_page_ranks"] = clean_pages

        coverage = case.get("verified_amount_page_coverage")
        if isinstance(coverage, Mapping):
            clean_coverage: dict[str, Any] = {}
            for method in _DIAGNOSTIC_METHODS:
                method_coverage = coverage.get(method)
                if not isinstance(method_coverage, Mapping):
                    continue
                pages = method_coverage.get("pages")
                if not isinstance(pages, list):
                    continue
                clean_rows = []
                for page_row in pages[:16]:
                    if not isinstance(page_row, Mapping):
                        continue
                    filename = page_row.get("file")
                    page_number = count(page_row.get("page"))
                    if (
                        not isinstance(filename, str)
                        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", filename) is None
                        or page_number is None or page_number < 1
                        or type(page_row.get("in_top8")) is not bool
                    ):
                        continue
                    clean_rows.append({
                        "file": filename,
                        "page": page_number,
                        "rank": rank(page_row.get("rank")),
                        "in_top8": page_row["in_top8"],
                    })
                clean_coverage[method] = {
                    "pages": clean_rows,
                    "top8_count": count(method_coverage.get("top8_count")),
                    "page_count": count(method_coverage.get("page_count")),
                }
            if clean_coverage:
                clean["verified_amount_page_coverage"] = clean_coverage

        raw_rankings = case.get("rankings")
        if isinstance(raw_rankings, Mapping):
            clean_rankings: dict[str, Any] = {}
            for method in _DIAGNOSTIC_METHODS:
                rows = raw_rankings.get(method)
                if not isinstance(rows, list):
                    continue
                clean_rows = []
                for row in rows[:60]:
                    if not isinstance(row, Mapping):
                        continue
                    clean_row: dict[str, Any] = {}
                    candidate_key = row.get("candidate_key")
                    if isinstance(candidate_key, str) and re.fullmatch(r"[0-9a-f]{64}", candidate_key):
                        clean_row["candidate_key"] = candidate_key
                    for field in ("rank", "dense_rank_original", "dense_rank_supplementary"):
                        if field in row:
                            clean_row[field] = rank(row[field])
                    score = metric(row.get("reranker_score"))
                    if score is not None:
                        clean_row["reranker_score"] = score
                    if "source_page" in row:
                        clean_row["source_page"] = rank(row["source_page"])
                    for field in (
                        "exact", "public", "retrieved_relevant_context", "supports_answer",
                    ):
                        if type(row.get(field)) is bool:
                            clean_row[field] = row[field]
                    clean_rows.append(clean_row)
                clean_rankings[method] = clean_rows
            if clean_rankings:
                clean["rankings"] = clean_rankings

        ranking_summary = case.get("ranking_summary")
        if isinstance(ranking_summary, Mapping) and isinstance(ranking_summary.get("rankings"), Mapping):
            clean_summary: dict[str, Any] = {}
            for method in _DIAGNOSTIC_METHODS:
                method_summary = ranking_summary["rankings"].get(method)
                if not isinstance(method_summary, Mapping):
                    continue
                clean_method: dict[str, Any] = {}
                candidate_count = count(method_summary.get("candidate_count"))
                if candidate_count is not None:
                    clean_method["candidate_count"] = candidate_count
                target_evidence = method_summary.get("target_evidence")
                if isinstance(target_evidence, Mapping):
                    clean_evidence: dict[str, Any] = {}
                    for evidence_id, metrics in target_evidence.items():
                        if (
                            not isinstance(evidence_id, str)
                            or evidence_id not in _SAFE_RETRIEVAL_CASE_IDS
                            or not isinstance(metrics, Mapping)
                        ):
                            continue
                        clean_metrics = {}
                        for field in ("exact_rank", "public_rank"):
                            if field in metrics:
                                clean_metrics[field] = rank(metrics[field])
                        for field in ("exact_in_top8", "public_in_top8"):
                            if type(metrics.get(field)) is bool:
                                clean_metrics[field] = metrics[field]
                        clean_evidence[evidence_id] = clean_metrics
                    if clean_evidence:
                        clean_method["target_evidence"] = clean_evidence
                for field in ("retrieved_relevant_context_ranks", "answer_support_ranks"):
                    values = method_summary.get(field)
                    if isinstance(values, list):
                        # 海象变量会绑定到外层函数作用域，不能复用输入摘要变量名。
                        clean_method[field] = [
                            clean_rank for item in values[:60]
                            if (clean_rank := rank(item)) is not None
                        ]
                clean_summary[method] = clean_method
            if clean_summary:
                clean["ranking_summary"] = {"rankings": clean_summary}

        production_top8 = case.get("production_top8")
        if isinstance(production_top8, Mapping):
            clean_top8 = {
                "candidate_count": count(production_top8.get("candidate_count")),
                "matches_reconstructed": production_top8.get("matches_reconstructed")
                if type(production_top8.get("matches_reconstructed")) is bool else None,
            }
            if clean_top8["candidate_count"] is not None:
                clean["production_top8"] = clean_top8

        production_rerank = case.get("production_rerank")
        if isinstance(production_rerank, Mapping):
            clean_production = {}
            for field in ("candidate_count", "reranker_calls"):
                clean_count = count(production_rerank.get(field))
                if clean_count is not None:
                    clean_production[field] = clean_count
            for field in ("query_expression", "ranking"):
                allowed_values = {"IBRD IDA", "original_query"} if field == "query_expression" else set(_DIAGNOSTIC_METHODS)
                if isinstance(production_rerank.get(field), str) and production_rerank[field] in allowed_values:
                    clean_production[field] = production_rerank[field]
            if clean_production:
                clean["production_rerank"] = clean_production

        latency = case.get("latency_ms")
        if isinstance(latency, Mapping):
            clean_latency: dict[str, Any] = {}
            diagnostic_latency = latency.get("reconstructed_diagnostic")
            if isinstance(diagnostic_latency, Mapping):
                clean_diagnostic_latency = {}
                for field in ("total", "original_dense", "supplementary_dense"):
                    value_ms = metric(diagnostic_latency.get(field))
                    if value_ms is not None:
                        clean_diagnostic_latency[field] = value_ms
                reranker = diagnostic_latency.get("reranker")
                if isinstance(reranker, Mapping):
                    clean_reranker = {
                        method: value_ms
                        for method in _DIAGNOSTIC_METHODS
                        if (value_ms := metric(reranker.get(method))) is not None
                    }
                    if clean_reranker:
                        clean_diagnostic_latency["reranker"] = clean_reranker
                if clean_diagnostic_latency:
                    clean_latency["reconstructed_diagnostic"] = clean_diagnostic_latency
            production_latency = latency.get("production_service")
            if isinstance(production_latency, Mapping):
                elapsed = metric(production_latency.get("elapsed"))
                if elapsed is not None:
                    clean_latency["production_service"] = {"elapsed": elapsed}
            if clean_latency:
                clean["latency_ms"] = clean_latency
        clean_cases.append(clean)

    if not clean_cases:
        return None
    output["case_count"] = len(clean_cases)
    output["cases"] = clean_cases
    graph_probe = value.get("fr042_graph_probe")
    if isinstance(graph_probe, Mapping):
        clean_graph = {}
        for field in (
            "external_model_calls", "scripted_agent_invocations",
            "scripted_judge_invocations", "tool_call_count", "top8_count",
        ):
            clean_count = count(graph_probe.get(field))
            if clean_count is not None:
                clean_graph[field] = clean_count
        target_ranks = graph_probe.get("target_evidence_ranks")
        if isinstance(target_ranks, list):
            clean_graph["target_evidence_ranks"] = [
                clean_rank for item in target_ranks[:8]
                if (clean_rank := rank(item)) is not None
            ]
        for field in ("target_in_top8", "answer_quality_evaluated"):
            if type(graph_probe.get(field)) is bool:
                clean_graph[field] = graph_probe[field]
        if isinstance(graph_probe.get("tool_status"), str) and graph_probe["tool_status"] in {"COMPLETED", "FAILED"}:
            clean_graph["tool_status"] = graph_probe["tool_status"]
        if clean_graph:
            output["fr042_graph_probe"] = clean_graph
    return output


def _safe_failure_code_value(value: Any) -> str | None:
    """只允许透传固定异常类别，不接受异常类名或消息文本。

    Args:
        value: 待透传的类别值；仅白名单中的字符串可被保留。
    """
    return value if isinstance(value, str) and value in _SAFE_FAILURE_CODES else None


def _safe_residual_map(value: Any) -> dict[str, bool] | None:
    """只复制完整且严格为布尔值的四层残留摘要。"""
    if not isinstance(value, Mapping) or set(value) != _RESIDUAL_FLAGS:
        return None
    if any(type(flag) is not bool for flag in value.values()):
        return None
    return {key: value[key] for key in sorted(_RESIDUAL_FLAGS)}


def _safe_retrieval_stage(value: Any) -> str | None:
    """仅接受检索验收准备和清理阶段的固定名称。"""
    return value if isinstance(value, str) and value in _SAFE_RETRIEVAL_STAGES else None


def _safe_retrieval_operation_code(value: Any) -> str | None:
    """只允许真实检索准备流程使用的固定操作码。"""
    return value if isinstance(value, str) and value in _SAFE_RETRIEVAL_OPERATION_CODES else None


def _safe_http_status(value: Any) -> int | None:
    """只保留合法 HTTP 状态码，不接受布尔值。"""
    return value if type(value) is int and 100 <= value <= 599 else None


def _safe_diagnostic_exception_type(value: Any) -> str | None:
    """只保留脱敏器阶段的内置异常类型名，不传播错误消息。"""
    return (
        value
        if isinstance(value, str) and value in _SAFE_DIAGNOSTIC_EXCEPTION_TYPES
        else None
    )


def _safe_diagnostic_error_line(error: BaseException) -> int | None:
    """只返回脱敏函数自身的堆栈行号，不保留文件路径或异常文本。"""
    frame = error.__traceback__
    target_code = _safe_retrieval_diagnostics.__code__
    while frame is not None:
        if frame.tb_frame.f_code is target_code:
            return _safe_diagnostic_error_line_value(frame.tb_lineno)
        frame = frame.tb_next
    return None


def _safe_diagnostic_error_line_value(value: Any) -> int | None:
    """限制诊断行号为正整数，拒绝布尔值与异常输入。"""
    return value if type(value) is int and 1 <= value <= 5000 else None


def _safe_residual_probe_stage(value: Any) -> str | None:
    """仅接受四层归零探针的固定阶段名称。"""
    return value if isinstance(value, str) and value in _SAFE_RESIDUAL_PROBE_STAGES else None


def _safe_cleanup_steps(value: Any) -> list[str] | None:
    """只保留已登记的清理步骤码，并按首次出现顺序去重。"""
    if not isinstance(value, (tuple, list)):
        return None
    return list(dict.fromkeys(step for step in value if isinstance(step, str) and step in _SAFE_CLEANUP_STEPS))


def _loopback_url(api: Any) -> str:
    """仅从运行器的本地监听端口生成 API 地址。"""
    return f"http://127.0.0.1:{api.port}"


def _load_settings() -> Any:
    """读取项目 .env 到 Settings 对象；配置值只留在进程内。"""
    from app.core.config import Settings

    return Settings(_env_file=PROJECT_ROOT / ".env", _env_file_encoding="utf-8")


def _validate_configuration(settings: Any) -> None:
    """确认运行所需凭据、服务配置和本地模型文件存在。"""
    if not settings.deepseek_api_key or not settings.deepseek_api_key.get_secret_value().strip():
        raise PreparationError("CONFIGURATION_INVALID")
    if not settings.auth_jwt_secret or not settings.auth_jwt_secret.get_secret_value().strip():
        raise PreparationError("CONFIGURATION_INVALID")
    if not settings.embedding_path.is_dir() or not settings.archive_reranker_path.is_dir():
        raise PreparationError("CONFIGURATION_INVALID")


def _verify_sources() -> Mapping[str, Any]:
    """委托已有人工作用来源清单与哈希验证器。"""
    from scripts.archive_world_bank_retrieval_acceptance import verify_source_manifests

    try:
        return verify_source_manifests()
    except BaseException as exc:
        raise PreparationError("SOURCE_MANIFEST_INVALID") from exc


def _admin_client(settings: Any) -> Any:
    """创建只供预检/清理使用的 Chroma 管理客户端。"""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.AdminClient(ChromaSettings(
        chroma_api_impl="chromadb.api.fastapi.FastAPI",
        chroma_server_host=settings.chroma_host,
        chroma_server_http_port=settings.chroma_port,
    ))


def _inspect_targets(targets: Targets) -> None:
    """确认 PostgreSQL 目标 Schema 不存在、Chroma Tenant 存在且 Database 不存在。"""
    from sqlalchemy import create_engine, text
    import chromadb
    from chromadb.errors import NotFoundError

    validate_paths_absent(targets.paths)
    current = __import__("app.core.config", fromlist=["settings"]).settings
    engine = create_engine(current.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = :name)"),
                {"name": targets.schema},
            ).scalar_one()
        if exists:
            raise PreparationError("TARGET_EXISTS")
    finally:
        engine.dispose()
    admin = _admin_client(current)
    try:
        admin.get_tenant(name=targets.chroma_tenant)
    except NotFoundError as exc:
        raise PreparationError("TARGET_EXISTS") from exc
    try:
        admin.get_database(name=targets.chroma_database, tenant=targets.chroma_tenant)
    except NotFoundError:
        return
    raise PreparationError("TARGET_EXISTS")


def _derived_database_url(database_url: str, schema: str) -> str:
    """为 Psycopg URL 设置单一 search_path 参数。"""
    from sqlalchemy.engine import make_url

    if not re.fullmatch(r"fr042_wb_[0-9a-f]{16}", schema):
        raise PreparationError("SCHEMA_BINDING_MISMATCH")
    url = make_url(database_url)
    query = dict(url.query)
    query["options"] = f"-csearch_path={schema}"
    return url.set(query=query).render_as_string(hide_password=False)


def _create_schema(targets: Targets) -> None:
    """用原始连接建立一个新建且精确命名的空 Schema。"""
    from sqlalchemy import create_engine, text
    from app.core.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{targets.schema}"'))
    except BaseException as exc:
        raise PreparationError("TARGET_EXISTS") from exc
    finally:
        engine.dispose()


def _verify_search_path(targets: Targets) -> None:
    """在导入 app.main 前，独立 Engine 确认连接落在本轮 Schema。"""
    from sqlalchemy import create_engine, text
    from app.core.config import settings

    url = _derived_database_url(settings.database_url, targets.schema)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        if schema != targets.schema:
            raise PreparationError("SCHEMA_BINDING_MISMATCH")
    except PreparationError:
        raise
    except BaseException as exc:
        raise PreparationError("SCHEMA_BINDING_MISMATCH") from exc
    finally:
        engine.dispose()


def _create_chroma_database(targets: Targets) -> None:
    """只在预检确认不存在后创建本轮 Chroma Database。"""
    from app.core.config import settings
    from chromadb.errors import NotFoundError

    admin = _admin_client(settings)
    try:
        admin.create_database(name=targets.chroma_database, tenant=targets.chroma_tenant)
    except BaseException as exc:
        raise PreparationError("TARGET_EXISTS") from exc


def _create_run_directory(paths: RunPaths) -> None:
    """创建已由 Git 忽略检查保护的运行目录。"""
    paths.runtime_root.mkdir(parents=True, exist_ok=False)
    paths.result_dir.mkdir(parents=True, exist_ok=False)


def _isolated_settings(settings: Any, targets: Targets) -> Any:
    """生成仅在内存中含隔离目标的设置副本。"""
    return settings.model_copy(update={
        "database_url": _derived_database_url(settings.database_url, targets.schema),
        "chroma_tenant": targets.chroma_tenant,
        "chroma_database": targets.chroma_database,
        "chroma_final_collection": targets.chroma_collection,
        "file_storage_dir": targets.paths.file_storage,
        "agent_checkpoint_file": targets.paths.checkpoint,
        "log_file": targets.paths.log_file,
    })


def _configure_isolated_runtime(settings: Any, targets: Targets) -> Any:
    """在内存中替换应用共享 Settings 和 Engine，再导入 FastAPI 模块。"""
    from sqlalchemy import create_engine
    import app.core.config as config
    import app.db as db

    isolated = _isolated_settings(settings, targets)
    config.settings = isolated
    config.get_settings.cache_clear()
    db.engine.dispose()
    db.engine = create_engine(isolated.database_url, pool_pre_ping=True)
    db.database_url = isolated.database_url
    for name, module in tuple(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "settings"):
            module.settings = isolated
        if name == "scripts.archive_lushan_persistence_acceptance":
            module.settings = isolated
            module.engine = db.engine
        if name == "scripts.archive_world_bank_retrieval_acceptance":
            module.settings = isolated
            module.engine = db.engine
    from app.main import app

    return app


class _UvicornHandle:
    """持有本轮 Uvicorn 服务线程和本机端口。"""

    def __init__(self, app: Any, port: int, server: Any, thread: Thread) -> None:
        self.app = app
        self.port = port
        self.server = server
        self.thread = thread


def _start_api(app: Any) -> _UvicornHandle:
    """在动态分配的回环端口启动 Uvicorn。"""
    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = Thread(target=server.run, name="archive-world-bank-extension-api", daemon=True)
    thread.start()
    return _UvicornHandle(app, port, server, thread)


def _wait_api_ready(handle: _UvicornHandle) -> bool:
    """等待 FastAPI 启动完成；健康检查仅访问本机回环地址。"""
    import urllib.error
    import urllib.request

    deadline = monotonic() + 45
    while monotonic() < deadline and handle.thread.is_alive():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/health", timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            sleep(0.2)
    return False


def _run_retrieval(base_url: str, capture: CandidateCaptureMap) -> Mapping[str, Any]:
    """运行既有只检索验收器并收集实际生产 Top-8。"""
    from scripts.archive_world_bank_retrieval_acceptance import run_world_bank_retrieval_acceptance

    try:
        return run_world_bank_retrieval_acceptance(base_url=base_url, candidate_capture=capture)
    except BaseException as exc:
        error = PreparationError("RETRIEVAL_FAILED")
        error.retrieval_stage = _safe_retrieval_stage(
            getattr(exc, "retrieval_stage", None)
        )
        error.cleanup_failed_steps = _safe_cleanup_steps(
            getattr(exc, "cleanup_failed_steps", None)
        )
        error.residual_probe_stage = _safe_residual_probe_stage(
            getattr(exc, "residual_probe_stage", None)
        )
        error.zero_residuals = _safe_residual_map(getattr(exc, "zero_residuals", None))
        error.preflight_error_code = _safe_failure_code_value(
            getattr(exc, "preflight_error_code", None)
        )
        error.residual_probe_error_code = _safe_failure_code_value(
            getattr(exc, "residual_probe_error_code", None)
        )
        error.retrieval_operation_code = _safe_retrieval_operation_code(
            getattr(exc, "retrieval_operation_code", None)
        )
        error.retrieval_http_status = _safe_http_status(
            getattr(exc, "retrieval_http_status", None)
        )
        raise error from None


def _stop_api(handle: _UvicornHandle) -> None:
    """请求 Uvicorn 结束并等待服务线程退出。"""
    handle.server.should_exit = True
    handle.thread.join(timeout=20)
    if handle.thread.is_alive():
        raise PreparationError("CLEANUP_FAILED")


def _cleanup_retrieval() -> None:
    """复用检索验收器定义的业务范围与四层资源清理实现。"""
    from scripts.archive_world_bank_retrieval_acceptance import _verify_zero_residuals

    residuals = _verify_zero_residuals()
    if not residuals or not all(value is True for value in residuals.values()):
        error = PreparationError("CLEANUP_FAILED")
        error.zero_residuals = _safe_residual_map(residuals)
        raise error


def _drop_schema(targets: Targets) -> None:
    """仅删除经本轮预检创建的目标 Schema。"""
    from sqlalchemy import create_engine, text
    from app.core.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{targets.schema}" CASCADE'))
    finally:
        engine.dispose()


def _dispose_runtime() -> None:
    """释放隔离 API 使用的应用级数据库连接池。"""
    import app.db as db

    db.engine.dispose()


def _delete_chroma_database(targets: Targets) -> None:
    """幂等删除本轮专用 Chroma Database，并先精确删除本轮 Collection。"""
    import chromadb
    from chromadb.errors import NotFoundError
    from app.core.config import settings

    admin = _admin_client(settings)
    try:
        admin.get_database(name=targets.chroma_database, tenant=targets.chroma_tenant)
    except NotFoundError:
        return

    ordinary = chromadb.HttpClient(
        host=settings.chroma_host, port=settings.chroma_port,
        tenant=targets.chroma_tenant, database=targets.chroma_database,
    )
    try:
        ordinary.delete_collection(name=targets.chroma_collection)
    except NotFoundError:
        pass
    try:
        admin.delete_database(name=targets.chroma_database, tenant=targets.chroma_tenant)
    except NotFoundError:
        pass


def _delete_run_directory(paths: RunPaths) -> None:
    """仅删除临时文件与 Checkpoint 所在的运行目录，保留评测产物。"""
    if paths.runtime_root.exists():
        shutil.rmtree(paths.runtime_root)


def _delete_log_directory(paths: RunPaths) -> None:
    """删除本轮专属 OS 临时日志目录。"""
    if paths.log_root.exists():
        shutil.rmtree(paths.log_root)


def _verify_cleanup(targets: Targets) -> bool:
    """验证目标 Schema、Chroma Database、运行目录及临时日志均已移除。"""
    from sqlalchemy import create_engine, text
    from app.core.config import settings
    from chromadb.errors import NotFoundError

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            schema_exists = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = :name)"),
                {"name": targets.schema},
            ).scalar_one()
    finally:
        engine.dispose()
    admin = _admin_client(settings)
    database_exists = True
    try:
        admin.get_database(name=targets.chroma_database, tenant=targets.chroma_tenant)
    except NotFoundError:
        database_exists = False
    return (
        not schema_exists
        and not database_exists
        and not targets.paths.runtime_root.exists()
        and not targets.paths.log_root.exists()
    )


def _write_artifacts(targets: Targets, artifacts: Mapping[str, Any]) -> None:
    """将本轮候选数据集和安全摘要写入 Git 忽略目录。"""
    targets.paths.result_dir.mkdir(parents=True, exist_ok=True)
    targets.paths.candidate_file.write_text(
        json.dumps(artifacts["dataset"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    targets.paths.summary_file.write_text(
        json.dumps(artifacts["summary"], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _emit(summary: Mapping[str, Any]) -> None:
    """只输出白名单状态、阶段、计数和布尔值摘要。"""
    print(json.dumps(_safe_summary(
        summary,
        error_code=summary.get("error_code"),
        failure_stage=summary.get("failure_stage"),
        primary_error_code=summary.get("primary_error_code"),
        primary_failure_stage=summary.get("primary_failure_stage"),
        cleanup_error_code=summary.get("cleanup_error_code"),
        cleanup_failure_stage=summary.get("cleanup_failure_stage"),
        zero_residuals=summary.get("zero_residuals"),
        retrieval_stage=summary.get("retrieval_stage"),
        cleanup_failed_steps=summary.get("cleanup_failed_steps"),
        residual_probe_stage=summary.get("residual_probe_stage"),
        preflight_error_code=summary.get("preflight_error_code"),
        residual_probe_error_code=summary.get("residual_probe_error_code"),
        retrieval_operation_code=summary.get("retrieval_operation_code"),
        retrieval_http_status=summary.get("retrieval_http_status"),
        diagnostic_exception_type=summary.get("diagnostic_exception_type"),
        diagnostic_error_line=summary.get("diagnostic_error_line"),
    ), ensure_ascii=False))


def _default_dependencies(*, emit: Callable[[Mapping[str, Any]], Any] = _emit) -> PreparationDependencies:
    """装配正式手动运行所需的资源操作。"""
    runtime: dict[str, Any] = {}
    settings_box: dict[str, Any] = {}

    def load() -> Any:
        value = _load_settings()
        settings_box["source"] = value
        return value

    def configure(settings: Any, targets: Targets) -> Any:
        app = _configure_isolated_runtime(settings, targets)
        runtime["app"] = app
        runtime["settings"] = app
        return app

    def start(_: Any) -> _UvicornHandle:
        return _start_api(runtime["app"])

    deps = PreparationDependencies(
        load_settings=load,
        validate_configuration=_validate_configuration,
        verify_sources=_verify_sources,
        inspect_targets=_inspect_targets,
        create_schema=_create_schema,
        verify_search_path=_verify_search_path,
        create_chroma_database=_create_chroma_database,
        create_run_directory=_create_run_directory,
        configure_isolated_runtime=configure,
        start_api=start,
        wait_api_ready=_wait_api_ready,
        run_retrieval=_run_retrieval,
        stop_api=_stop_api,
        dispose_runtime=_dispose_runtime,
        shutdown_logging=logging.shutdown,
        cleanup_retrieval=_cleanup_retrieval,
        drop_schema=_drop_schema,
        delete_chroma_database=_delete_chroma_database,
        delete_run_directory=_delete_run_directory,
        delete_log_directory=_delete_log_directory,
        verify_cleanup=_verify_cleanup,
        write_artifacts=_write_artifacts,
        emit=emit,
    )
    return deps


def main() -> int:
    """执行手动候选准备并只显示安全摘要。"""
    _ensure_project_root_on_sys_path()
    parser = argparse.ArgumentParser(description="准备 FR-039 World Bank 扩展来源评测候选集。")
    parser.add_argument("--run-id", help="可选的十六位小写十六进制运行标识。")
    args = parser.parse_args()
    emitted = {"value": False}

    def emit_once(summary: Mapping[str, Any]) -> None:
        emitted["value"] = True
        _emit(summary)

    try:
        prepare_extension_eval(_default_dependencies(emit=emit_once), run_id=args.run_id)
    except PreparationError as exc:
        if not emitted["value"]:
            _emit({
                "status": "failed", "planned_group_count": len(CONDITION_GROUPS),
                "source_hash_verified": False, "cleanup_verified": False,
                "error_code": exc.safe_code,
                "failure_stage": "prepare.build_run_paths",
            })
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
