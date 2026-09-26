"""执行 World Bank 双项目、只检索的隔离真实链路验收。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# 直接执行脚本时，Python 默认只搜索 scripts/；先加入仓库根以导入 app 包。
_SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_PROJECT_ROOT))

from sqlmodel import Session

from app.agents.archive.runtime import build_archive_runtime
from app.core.config import PROJECT_ROOT, settings
from app.db import engine
from app.models import Project
from app.services.archive.evidence_matching import item_contains_expected_evidence
from app.services.archive.parser import parse_archive_document
from app.services.archive.reranker import score_archive_candidates
from app.services.archive.retrieval import (
    _build_archive_reranker_query_expression,
    _candidate_matches_expected_evidence,
    _candidate_publicly_covers_expected_evidence,
    _diagnostic_candidate_key,
    _formal_document_ids,
    _query_validated_candidates,
    retrieve_archive_answer_candidates,
)
from app.services.infrastructure.chroma import get_chroma_client
from app.services.infrastructure.chroma_namespace import get_chroma_admin_client
from scripts.archive_lushan_persistence_acceptance import (
    DEFAULT_CORPUS_ROOT,
    _candidate_chunk_id,
    _diagnostic_expected_evidence,
    _entry_map,
    _existing_final_collection_count,
    _merge_candidate_pools,
    _postgres_row_count,
    _question,
    _read_json,
    _safe_candidate_key,
    _stored_file_count,
    _summarize_dual_rankings,
    _write_result,
    _checkpoint_row_count,
    build_document_label,
)
from scripts.archive_v1_p14_acceptance import (
    AcceptanceError,
    P14Api,
    _cleanup_seeded_scope,
    _require_object,
    _seed_confirmed_documents,
    parse_registered_user_id,
)


DATASET_PATH = PROJECT_ROOT / "pixie_qa" / "datasets" / "lushan-p153548-smoke.json"
LUSHAN_CORPUS_ROOT = DEFAULT_CORPUS_ROOT
BELARUS_CORPUS_ROOT = (
    PROJECT_ROOT
    / "tests"
    / "pytest_docs"
    / "public_projects"
    / "world_bank_belarus_m6"
)
BELARUS_MANIFEST_PATH = BELARUS_CORPUS_ROOT / "metadata" / "source-manifest.json"
BELARUS_CASES_PATH = BELARUS_CORPUS_ROOT / "annotations" / "cases.md"
BELARUS_PDF_RELATIVE_PATH = (
    "tests/pytest_docs/public_projects/world_bank_belarus_m6/source/belarus-snapshot.pdf"
)
CLEANUP_FAILURE_STEP_CODES = (
    "BUSINESS_SCOPE_CLEANUP",
    "API_CLOSE",
    "CHROMA_DATABASE_DELETE",
    "LOCAL_TARGET_DELETE",
    "ZERO_RESIDUAL_VERIFY",
)
_SAFE_RETRIEVAL_OPERATION_CODES = {
    "HTTP_REQUEST", "SEED_CONFIRM", "SEED_FIELD_UPDATE", "SEED_MANUAL_DRAFT",
    "SEED_PARSE", "SEED_PROJECT_CREATE", "SEED_UPLOAD",
}
_SAFE_RETRIEVAL_STAGES = {
    "prepare.empty_target_validation",
    "prepare.empty_target.current_schema",
    "prepare.empty_target.namespace_contract",
    "prepare.empty_target.embedding_mode",
    "prepare.empty_target.chroma_collections",
    "prepare.empty_target.postgres_count",
    "prepare.empty_target.vector_count",
    "prepare.empty_target.file_count",
    "prepare.empty_target.checkpoint_count",
    "prepare.source_validation",
    "prepare.identity_registration",
    "prepare.document_seeding",
    "prepare.candidate_query_ranking",
    "prepare.graph_evidence_probe",
    "cleanup",
}
_RESIDUAL_PROBE_STAGES = {
    "residuals.chroma_list",
    "residuals.chroma_match",
    "residuals.postgres_count",
    "residuals.files_check",
    "residuals.checkpoint_check",
    "residuals.validate",
}
_ZERO_RESIDUAL_KEYS = {
    "postgres_zero", "chroma_zero", "files_zero", "checkpoint_zero",
}
_SAFE_FAILURE_CODES = {
    "EXPECTED_REJECTION",
    "SQLALCHEMY_CONNECTION",
    "SQLALCHEMY_DRIVER",
    "SQLALCHEMY_QUERY",
    "FILESYSTEM",
    "UNKNOWN",
}


def _safe_failure_code(error: BaseException) -> str:
    """仅依据固定异常类型返回安全类别码，不读取异常文本或任意类名。

    Args:
        error: 待分类的异常对象；只检查其类型，不读取异常内容。
    """
    from sqlalchemy.exc import (
        DBAPIError,
        DisconnectionError,
        InterfaceError,
        OperationalError,
        SQLAlchemyError,
        TimeoutError as SQLAlchemyTimeoutError,
    )

    if isinstance(error, AcceptanceError):
        return "EXPECTED_REJECTION"
    if isinstance(
        error,
        (OperationalError, InterfaceError, DisconnectionError, SQLAlchemyTimeoutError),
    ):
        return "SQLALCHEMY_CONNECTION"
    if isinstance(error, DBAPIError):
        return "SQLALCHEMY_DRIVER"
    if isinstance(error, SQLAlchemyError):
        return "SQLALCHEMY_QUERY"
    if isinstance(error, OSError):
        return "FILESYSTEM"
    return "UNKNOWN"


def _allowlisted_failure_code(value: Any) -> str | None:
    """只接受固定异常类别枚举，拒绝任意文本。

    Args:
        value: 待验证的类别值；不是白名单字符串时返回 None。
    """
    return value if isinstance(value, str) and value in _SAFE_FAILURE_CODES else None


PIXIE_CANDIDATE_CAPTURE_RANKINGS = {
    "LUSHAN-02": ("original",),
    "LUSHAN-03": ("original",),
    "WB-CONTEXT-NONLOAN-01": ("original",),
    "WB-ENTITY-NEG-01": ("original",),
    "WB-UNSUPPORTED-DRAW-01": ("original",),
    "WB-UNSUPPORTED-DRAW-GATED-01": ("original",),
    "WB-REV-LOAN-01": ("original", "supplementary"),
}
_BELARUS_QUERIES = {
    "WB-REV-LOAN-01": "白俄罗斯 M6 交通走廊改善项目的世界银行贷款金额是多少？",
    "WB-CONTEXT-NONLOAN-01": "世界银行 M6 项目中，Bruzgi 口岸预计每日可处理多少辆卡车？",
    "WB-ENTITY-NEG-01": "IFC 为白俄罗斯 M6 项目提供了多少贷款？",
    "WB-UNSUPPORTED-DRAW-01": "白俄罗斯 M6 项目当前或完工时的实际提款金额是多少？",
    "WB-UNSUPPORTED-DRAW-GATED-01": (
        "白俄罗斯 M6 交通走廊改善项目的世界银行贷款在项目完工时实际提款金额是多少？"
    ),
}
_BELARUS_TARGET_PAGES = {
    "WB-REV-LOAN-01": {7, 16},
    "WB-CONTEXT-NONLOAN-01": {16},
    "WB-ENTITY-NEG-01": {16},
    "WB-UNSUPPORTED-DRAW-01": {16},
    "WB-UNSUPPORTED-DRAW-GATED-01": {16},
}
_LUSHAN_AMOUNT_SUPPORT_PAGES = (
    ("2016_project_appraisal_document.pdf", 1),
    ("2016_project_appraisal_document.pdf", 16),
    ("2022_restructuring_paper.pdf", 4),
    ("2024_completion_report.pdf", 1),
    ("2024_completion_report_review.pdf", 1),
)


def _sha256(path: Path) -> str:
    """以固定区块大小计算来源文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_file_hash(path: Path, expected_hash: str, *, label: str) -> None:
    """拒绝缺失或与人工来源清单不一致的文件。"""
    if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise AcceptanceError(f"{label}来源文件或 SHA-256 清单无效。")
    if _sha256(path) != expected_hash:
        raise AcceptanceError(f"{label}来源文件哈希不匹配。")


def verify_source_manifests(
    *,
    dataset_path: Path = DATASET_PATH,
    lushan_root: Path = LUSHAN_CORPUS_ROOT,
    belarus_manifest_path: Path = BELARUS_MANIFEST_PATH,
) -> dict[str, Any]:
    """校验固定芦山四份 PDF 与 Belarus manifest/PDF 的路径、清单和哈希。"""
    dataset = _read_json(dataset_path, label="P153548 固定集")
    lushan_manifest = _read_json(
        lushan_root / "metadata" / "manifest.json", label="P153548 来源清单"
    )
    entries = _entry_map(dataset)
    label = build_document_label(dataset, lushan_manifest)
    lushan_documents = label["normal_documents"]
    if not {"LUSHAN-01", "LUSHAN-02", "LUSHAN-03"} <= {
        entry["eval_metadata"]["case_id"] for entry in entries.values()
    }:
        raise AcceptanceError("P153548 固定集缺少 LUSHAN-01/02/03。")
    for document in lushan_documents:
        path = lushan_root / document["relative_path"]
        _require_file_hash(path, str(document["file_sha256"]), label="P153548")

    belarus_manifest = _read_json(belarus_manifest_path, label="Belarus 来源清单")
    source = belarus_manifest.get("source")
    if not isinstance(source, dict):
        raise AcceptanceError("Belarus 来源清单缺少来源元数据。")
    belarus_path = PROJECT_ROOT / str(source.get("local_path", ""))
    if source.get("local_path") != BELARUS_PDF_RELATIVE_PATH or source.get("page_count") != 20:
        raise AcceptanceError("Belarus Snapshot 原件路径、页数或哈希不匹配。")
    _require_file_hash(belarus_path, str(source.get("sha256", "")), label="Belarus Snapshot")
    try:
        annotations = BELARUS_CASES_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise AcceptanceError("Belarus 人工标注文件不可读取。") from exc
    if any(
        case_id not in annotations
        for case_id in _BELARUS_QUERIES
        if case_id != "WB-UNSUPPORTED-DRAW-GATED-01"
    ):
        raise AcceptanceError("Belarus 人工标注没有覆盖全部扩展案例。")
    return {
        "dataset": dataset,
        "lushan_manifest": lushan_manifest,
        "lushan_label": label,
        "belarus_manifest": belarus_manifest,
        "belarus_path": belarus_path,
    }


def validate_isolated_targets(
    *,
    postgres_schema: str,
    chroma_tenant: str,
    chroma_database: str,
    chroma_collection: str,
    file_storage_path: Path,
    checkpoint_path: Path,
) -> None:
    """只接受命名专用、路径分离且不指向默认资源的验收目标。"""
    match = re.fullmatch(r"fr042_wb_([0-9a-f]{8,32})", postgres_schema)
    if match is None:
        raise AcceptanceError("PostgreSQL 目标必须是本次专用 fr042_wb_<随机后缀> Schema。")
    suffix = match.group(1)
    names = (chroma_database, chroma_collection)
    if any(not name.startswith("fr042_wb_") or suffix not in name for name in names):
        raise AcceptanceError("Chroma Database/Collection 必须带相同专用运行标识。")
    if chroma_database == "mini_rag_chroma" or chroma_collection == "archive_final_chunks":
        raise AcceptanceError("拒绝默认 Chroma 目标。")
    run_root = (PROJECT_ROOT / "tests" / "pytest_docs" / "acceptance-runs" / suffix).resolve()
    normalized_file_root = Path(file_storage_path).resolve()
    normalized_checkpoint = Path(checkpoint_path).resolve()
    if (
        normalized_file_root == (PROJECT_ROOT / "data" / "files").resolve()
        or normalized_checkpoint == (PROJECT_ROOT / "data" / "agent_checkpoints.db").resolve()
        or normalized_file_root != run_root / "files"
        or normalized_checkpoint != run_root / "checkpoints.sqlite"
        or normalized_file_root == normalized_checkpoint
        or normalized_file_root in normalized_checkpoint.parents
        or normalized_checkpoint in normalized_file_root.parents
    ):
        raise AcceptanceError("文件与 Checkpoint 必须位于本次独立验收运行目录。")


def build_query_plan(
    lushan_queries: dict[str, str], belarus_queries: dict[str, str]
) -> list[dict[str, str | None]]:
    """构造九条原问题检索及门控贷款题的固定补充查询计划。"""
    if set(lushan_queries) != {"LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04"}:
        raise AcceptanceError("芦山查询计划必须且只能包含 LUSHAN-01/02/03/04。")
    if set(belarus_queries) != set(_BELARUS_QUERIES):
        raise AcceptanceError("Belarus 查询计划必须与固定的五条案例完全一致。")
    plan: list[dict[str, str | None]] = []
    for case_id, query in [*lushan_queries.items(), *belarus_queries.items()]:
        supplementary = case_id in {
            "LUSHAN-01",
            "WB-REV-LOAN-01",
            "WB-UNSUPPORTED-DRAW-GATED-01",
        }
        if not isinstance(query, str) or not query.strip():
            raise AcceptanceError("检索计划包含空问题。")
        if supplementary and "世界银行" not in query:
            raise AcceptanceError("贷款补充查询的原问题必须含完整世界银行指称。")
        plan.append(
            {
                "project_key": "lushan" if case_id.startswith("LUSHAN-") else "belarus",
                "query_kind": case_id,
                "original_query": query,
                "supplementary_query": "IBRD IDA" if supplementary else None,
            }
        )
    return plan


def merge_candidate_pools(
    original: list[tuple[float, Any]], supplementary: list[tuple[float, Any]]
) -> tuple[list[tuple[float, Any]], dict[str, int], dict[str, int]]:
    """通过现有验收辅助方法按 Chunk ID 稳定合并去重。"""
    return _merge_candidate_pools(original, supplementary)


def score_evidence_coverage(
    candidates: list[Any], expected_by_chunk: dict[str, bool]
) -> dict[str, Any]:
    """返回候选总数、目标数及 1-based 目标名次，不保留候选身份。"""
    ranks = [
        rank
        for rank, item in enumerate(candidates, 1)
        if expected_by_chunk.get(_candidate_chunk_id(item), False)
    ]
    return {
        "candidate_count": len(candidates),
        "target_count": len(ranks),
        "target_ranks": ranks,
    }


def candidate_evidence_flags(
    *,
    case_id: str,
    item: Any,
    expected_lushan: dict[str, Any] | None,
) -> dict[str, bool]:
    """分离冻结 exact/public、检索上下文相关性与答案事实支持。"""
    if case_id.startswith("LUSHAN-"):
        if case_id == "LUSHAN-04":
            return {
                "exact": False,
                "public": False,
                "retrieved_relevant_context": False,
                "supports_answer": False,
            }
        if expected_lushan is None:
            raise AcceptanceError("冻结集证据评分缺少标准标注。")
        exact = _candidate_matches_expected_evidence(
            item=item, expected_evidence=expected_lushan
        )
        public = _candidate_publicly_covers_expected_evidence(
            item=item, expected_evidence=expected_lushan
        )
        return {
            "exact": exact,
            "public": public,
            "retrieved_relevant_context": exact or public,
            "supports_answer": public,
        }
    page = item.location_start if item.location_type.value == "PDF_PAGE" else None
    relevant = (
        item.filename == "belarus-snapshot.pdf"
        and page in _BELARUS_TARGET_PAGES.get(case_id, set())
    )
    positive_case = case_id in {"WB-REV-LOAN-01", "WB-CONTEXT-NONLOAN-01"}
    return {
        "exact": False,
        "public": False,
        "retrieved_relevant_context": relevant,
        "supports_answer": relevant and positive_case,
    }


def sanitize_retrieval_result(result: dict[str, Any]) -> dict[str, Any]:
    """只输出候选匿名键、排序分数、页码与证据布尔值。"""
    safe_candidates = []
    for candidate in result.get("candidates", []):
        if not isinstance(candidate, dict):
            raise AcceptanceError("检索候选摘要结构无效。")
        safe_candidates.append(
            {
                "candidate_key": _safe_candidate_key(str(candidate["chunk_id"])),
                "dense_rank": int(candidate["dense_rank"]),
                "dense_distance": float(candidate["dense_distance"]),
                "reranker_rank": int(candidate["reranker_rank"]),
                "reranker_score": float(candidate["reranker_score"]),
                "target_evidence": bool(candidate["target_evidence"]),
                "source_page": candidate.get("source_page"),
            }
        )
    return {"candidates": safe_candidates}


def _read_current_schema() -> str:
    """读取已配置连接的当前 Schema，拒绝公共或不合约的目标。"""
    from sqlalchemy import text

    with engine.connect() as connection:
        return str(connection.execute(text("SELECT current_schema()")).scalar_one())


def _assert_empty_targets() -> None:
    """核对当前 Schema 与 Chroma/文件/Checkpoint 目标均为专用空资源。"""
    stage = "prepare.empty_target.current_schema"
    try:
        schema = _read_current_schema()
        stage = "prepare.empty_target.namespace_contract"
        validate_isolated_targets(
            postgres_schema=schema,
            chroma_tenant=settings.chroma_tenant,
            chroma_database=settings.chroma_database,
            chroma_collection=settings.chroma_final_collection,
            file_storage_path=settings.file_storage_path,
            checkpoint_path=settings.agent_checkpoint_path,
        )
        stage = "prepare.empty_target.embedding_mode"
        if settings.archive_embedding_context_mode != "evidence_values":
            raise AcceptanceError("检索-only 验收要求使用 evidence_values 索引上下文。")
        stage = "prepare.empty_target.chroma_collections"
        client = get_chroma_client()
        require_empty_chroma_database(client.list_collections())

        checks = (
            ("prepare.empty_target.postgres_count", _postgres_row_count),
            ("prepare.empty_target.vector_count", _existing_final_collection_count),
            (
                "prepare.empty_target.file_count",
                lambda: _stored_file_count(settings.file_storage_path),
            ),
            (
                "prepare.empty_target.checkpoint_count",
                lambda: _checkpoint_row_count(settings.agent_checkpoint_path),
            ),
        )
        for stage, count_rows in checks:
            if count_rows() != 0:
                raise AcceptanceError("验收目标不是空资源；拒绝覆盖或复用。")
    except Exception as exc:
        error = AcceptanceError("验收目标预检查失败。")
        error.retrieval_stage = stage
        error.preflight_error_code = _safe_failure_code(exc)
        raise error from None


def require_empty_chroma_database(collections: list[Any]) -> None:
    """拒绝隔离 Database 中任何已有 Collection，避免复用非空命名空间。"""
    if collections:
        raise AcceptanceError("隔离 Chroma Database 必须预先存在且无任何 Collection。")


def _validate_result_file(path: Path | None) -> None:
    """仅允许写入忽略目录下的新验收摘要文件，绝不覆盖已有文件。"""
    if path is None:
        return
    normalized = path.resolve()
    allowed_root = (BELARUS_CORPUS_ROOT / "acceptance").resolve()
    if (
        normalized.parent != allowed_root
        or normalized.suffix.lower() != ".json"
        or not normalized.name.startswith("retrieval-only-")
        or normalized.exists()
    ):
        raise AcceptanceError("结果文件必须是 Belarus 忽略验收目录内未存在的新 JSON 文件。")


def _lushan_queries(dataset: dict[str, Any]) -> dict[str, str]:
    entries = _entry_map(dataset)
    return {
        case_id: _question(entries[case_id])
        for case_id in ("LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04")
    }


def _make_combined_label(sources: dict[str, Any]) -> dict[str, Any]:
    """将两组来源放入现有确认索引辅助方法要求的两个独立项目。"""
    lushan_label = sources["lushan_label"]
    belarus_source = sources["belarus_manifest"]["source"]
    belarus_path: Path = sources["belarus_path"]
    parsed = parse_archive_document(belarus_path)
    if not parsed.fragments:
        raise AcceptanceError("Belarus Snapshot 没有可人工确认的文本片段。")
    page_one = next((item for item in parsed.fragments if item.location_start == 1), None)
    if page_one is None:
        raise AcceptanceError("Belarus Snapshot 缺少首页证据，无法安全确认元数据。")
    title_excerpt = page_one.content[: min(len(page_one.content), 180)]
    belarus_document = {
        "id": "B-01",
        "project_id": "belarus",
        "relative_path": BELARUS_PDF_RELATIVE_PATH,
        "source_format": "PDF",
        "scenario": "world-bank-retrieval-only",
        "file_sha256": belarus_source["sha256"],
        "expected_fields": {
            "TITLE": {
                "value": str(belarus_source["title"]),
                "evidence": [{"location_type": "PDF_PAGE", "location_start": 1, "location_end": 1, "excerpt": title_excerpt}],
            },
            "DOCUMENT_TYPE": {
                "value": "OTHER",
                "evidence": [{"location_type": "PDF_PAGE", "location_start": 1, "location_end": 1, "excerpt": title_excerpt}],
            },
        },
    }
    documents = []
    for document in lushan_label["normal_documents"]:
        documents.append(
            {
                **document,
                "relative_path": str(
                    Path("tests/pytest_docs/public_projects/lushan_earthquake_p153548")
                    / document["relative_path"]
                ),
            }
        )
    return {
        "projects": [
            {"id": "lushan", "name": "芦山地震恢复重建项目"},
            {"id": "belarus", "name": "白俄罗斯 M6 交通走廊改善项目"},
        ],
        "normal_documents": [*documents, belarus_document],
    }


def _rank_query(
    *,
    session: Session,
    user_id: UUID,
    project_id: UUID,
    query: str,
    case_id: str,
    expected_lushan: dict[str, Any] | None,
    supplementary_query: str | None,
    candidate_capture: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """执行生产 Top-8，并保留同范围的 dense 与双表达重排诊断。"""
    diagnostic_started = perf_counter()
    project = session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise AcceptanceError("检索项目不属于本次临时验收主体。")
    formal_ids = _formal_document_ids(
        user_id=user_id, project_id=project_id, kb_id=project.kb_id, session=session
    )
    if not formal_ids:
        raise AcceptanceError("项目没有已确认的正式档案文档。")
    dense_started = perf_counter()
    _, original = _query_validated_candidates(
        user_id=user_id,
        project_id=project_id,
        kb_id=project.kb_id,
        query=query,
        formal_ids=formal_ids,
    )
    original_dense_ms = (perf_counter() - dense_started) * 1000
    supplementary: list[tuple[float, Any]] = []
    supplementary_dense_ms = 0.0
    if supplementary_query:
        supplementary_started = perf_counter()
        _, supplementary = _query_validated_candidates(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query=supplementary_query,
            formal_ids=formal_ids,
        )
        supplementary_dense_ms = (perf_counter() - supplementary_started) * 1000
    merged, original_dense_ranks, supplementary_dense_ranks = merge_candidate_pools(
        original, supplementary
    )
    if not merged:
        raise AcceptanceError("检索候选并集为空。")

    flags_by_chunk: dict[str, dict[str, bool]] = {}
    for _, item in merged:
        flags_by_chunk[item.chunk_id] = candidate_evidence_flags(
            case_id=case_id,
            item=item,
            expected_lushan=expected_lushan,
        )

    queries = {"original": query}
    if supplementary_query:
        queries["supplementary"] = supplementary_query
    rankings: dict[str, list[dict[str, Any]]] = {}
    reranker_latency: dict[str, float] = {}
    for label, rerank_query in queries.items():
        started = perf_counter()
        scores = score_archive_candidates(
            query=_build_archive_reranker_query_expression(rerank_query),
            contents=[item.excerpt for _, item in merged],
        )
        reranker_latency[label] = round((perf_counter() - started) * 1000, 3)
        if len(scores) != len(merged):
            raise AcceptanceError("Reranker 分数与候选并集长度不一致。")
        rows = []
        distances = {item.chunk_id: distance for distance, item in merged}
        for score, (_, item) in zip(scores, merged, strict=True):
            rows.append(
                {
                    "chunk_id": item.chunk_id,
                    "score": float(score),
                    "distance": distances[item.chunk_id],
                    "source_file": item.filename,
                    "coverage": {
                        case_id: {
                            "exact": flags_by_chunk[item.chunk_id]["exact"],
                            "public": flags_by_chunk[item.chunk_id]["public"],
                        }
                    },
                    "source_page": item.location_start if item.location_type.value == "PDF_PAGE" else None,
                }
            )
        rows.sort(key=lambda row: (-row["score"], row["distance"], row["chunk_id"]))
        rankings[label] = rows

    if case_id.startswith("LUSHAN-"):
        ranked_summary = _summarize_dual_rankings(rankings)
    else:
        ranked_summary = {
            "rankings": {
                method: {
                    "candidate_count": len(rows),
                    "retrieved_relevant_context_ranks": [
                        rank for rank, row in enumerate(rows, 1)
                        if flags_by_chunk[row["chunk_id"]]["retrieved_relevant_context"]
                    ],
                    "answer_support_ranks": [
                        rank for rank, row in enumerate(rows, 1)
                        if flags_by_chunk[row["chunk_id"]]["supports_answer"]
                    ],
                }
                for method, rows in rankings.items()
            }
        }
    safe_rankings: dict[str, Any] = {}
    original_rank_by_chunk = {
        item.chunk_id: rank
        for rank, (_, item) in enumerate(
            sorted(original, key=lambda pair: (pair[0], pair[1].chunk_id)), 1
        )
    }
    supplementary_rank_by_chunk = {
        candidate.chunk_id: rank
        for rank, (_, candidate) in enumerate(
            sorted(supplementary, key=lambda pair: (pair[0], pair[1].chunk_id)), 1
        )
    }
    for method, rows in rankings.items():
        safe_rankings[method] = [
            {
                "candidate_key": _safe_candidate_key(row["chunk_id"]),
                "rank": rank,
                "dense_rank_original": original_rank_by_chunk.get(row["chunk_id"]),
                "dense_rank_supplementary": supplementary_rank_by_chunk.get(row["chunk_id"]),
                "reranker_score": round(float(row["score"]), 6),
                "source_page": row["source_page"],
                **flags_by_chunk[row["chunk_id"]],
            }
            for rank, row in enumerate(rows, 1)
        ]
    rank_maps = {
        "original": (original, original_dense_ranks),
        "supplementary": (supplementary, supplementary_dense_ranks),
    }
    rank_dimensions = (
        ("exact", "public") if case_id.startswith("LUSHAN-")
        else ("retrieved_relevant_context", "supports_answer")
    )
    dense_target_ranks = {
        route: {
            dimension: min(
                (
                    ranks[item.chunk_id]
                    for _, item in candidates
                    if flags_by_chunk[item.chunk_id][dimension]
                ),
                default=None,
            )
            for dimension in rank_dimensions
        }
        for route, (candidates, ranks) in rank_maps.items()
    }
    page_ranks = {
        method: {
            str(page): next(
                (
                    rank
                    for rank, row in enumerate(rows, 1)
                    if row["source_page"] == page
                    and (
                        case_id != "WB-REV-LOAN-01"
                        or row["source_file"] == "belarus-snapshot.pdf"
                    )
                ),
                None,
            )
            for page in sorted(_BELARUS_TARGET_PAGES[case_id])
        }
        for method, rows in rankings.items()
    } if case_id.startswith("WB-") else {}
    amount_page_coverage = {}
    if case_id == "LUSHAN-01":
        for method, rows in rankings.items():
            keyed_ranks = {
                (filename, page): min(
                    (
                        rank
                        for rank, row in enumerate(rows, 1)
                        if row["source_file"] == filename and row["source_page"] == page
                    ),
                    default=None,
                )
                for filename, page in _LUSHAN_AMOUNT_SUPPORT_PAGES
            }
            page_rows = [
                {
                    "file": filename,
                    "page": page,
                    "rank": keyed_ranks.get((filename, page)),
                    "in_top8": keyed_ranks.get((filename, page)) is not None
                    and keyed_ranks[(filename, page)] <= 8,
                }
                for filename, page in _LUSHAN_AMOUNT_SUPPORT_PAGES
            ]
            amount_page_coverage[method] = {
                "pages": page_rows,
                "top8_count": sum(row["in_top8"] for row in page_rows),
                "page_count": len(page_rows),
            }
    production_method = "supplementary" if supplementary_query else "original"
    production_service_started = perf_counter()
    production_response = retrieve_archive_answer_candidates(
        user_id=user_id,
        project_id=project_id,
        kb_id=project.kb_id,
        query=query,
        session=session,
        observe_result=False,
        gate_question=query,
    )
    production_service_ms = (perf_counter() - production_service_started) * 1000
    actual_top8_keys = [
        _safe_candidate_key(str(item.chunk_id)) for item in production_response.items
    ]
    reconstructed_top8_keys = [
        _safe_candidate_key(str(row["chunk_id"]))
        for row in rankings[production_method][:8]
    ]
    production_top8_matches = actual_top8_keys == reconstructed_top8_keys
    if not production_top8_matches:
        raise AcceptanceError("生产检索 Top-8 与验收重建生产排名不一致。")
    if candidate_capture is not None:
        capture_methods = PIXIE_CANDIDATE_CAPTURE_RANKINGS.get(case_id, ())
        candidates_by_chunk = {item.chunk_id: item for _, item in merged}
        for method in capture_methods:
            candidate_capture[f"{case_id}/{method}"] = [
                candidates_by_chunk[row["chunk_id"]].model_dump(mode="json")
                for row in rankings[method][:8]
            ]
    return {
        "case_id": case_id,
        "project_key": "lushan" if case_id.startswith("LUSHAN-") else "belarus",
        "evidence_basis": "frozen_target" if case_id.startswith("LUSHAN-") else "extension_source",
        "query_route_count": len(queries),
        "original_dense_count": len(original),
        "supplementary_dense_count": len(supplementary),
        "candidate_union_count": len(merged),
        "candidate_intersection_count": len(original_dense_ranks.keys() & supplementary_dense_ranks.keys()),
        "dense_target_rank": dense_target_ranks,
        "target_page_ranks": page_ranks,
        "verified_amount_page_coverage": amount_page_coverage,
        "rankings": safe_rankings,
        "ranking_summary": ranked_summary,
        "production_top8": {
            "candidate_keys": actual_top8_keys,
            "candidate_count": len(actual_top8_keys),
            "reconstructed_candidate_keys": reconstructed_top8_keys,
            "matches_reconstructed": production_top8_matches,
        },
        "production_rerank": {
            "query_expression": "IBRD IDA" if supplementary_query else "original_query",
            "candidate_count": len(merged),
            "reranker_calls": 1,
            "ranking": production_method,
        },
        "latency_ms": {
            "reconstructed_diagnostic": {
                "total": round((production_service_started - diagnostic_started) * 1000, 3),
                "original_dense": round(original_dense_ms, 3),
                "supplementary_dense": round(supplementary_dense_ms, 3),
                "reranker": reranker_latency,
            },
            "production_service": {"elapsed": round(production_service_ms, 3)},
        },
    }


class _GraphEvidenceProbeModel:
    """用固定工具调用驱动真实 Graph，不发送外部模型请求。"""

    def __init__(self, *, query: str) -> None:
        """保存唯一的脚本化检索词。"""
        self.query = query
        self.invocation_count = 0
        self._tool_call_id = f"fr042-retrieval-probe-{uuid4().hex}"
        self.tool_payload: dict[str, Any] | None = None

    def bind_tools(self, tools: list[Any]) -> _GraphEvidenceProbeModel:
        """确认生产 Graph 暴露证据工具并返回自身。"""
        if "search_confirmed_archive_evidence" not in {tool.name for tool in tools}:
            raise AcceptanceError("Graph 未绑定正式档案证据工具。")
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        """先发出一条固定工具调用，再只观察真实 ToolMessage。"""
        self.invocation_count += 1
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            if self.invocation_count != 1:
                raise AcceptanceError("脚本化 Graph 探针收到意外模型调用顺序。")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_confirmed_archive_evidence",
                        "args": {"query": self.query},
                        "id": self._tool_call_id,
                        "type": "tool_call",
                    }
                ]
            )
        if len(tool_messages) != 1 or self.tool_payload is not None:
            raise AcceptanceError("Graph 探针只允许捕获一次证据工具结果。")
        try:
            payload = json.loads(str(tool_messages[0].content))
        except (TypeError, ValueError) as exc:
            raise AcceptanceError("Graph 探针收到的工具结果不是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise AcceptanceError("Graph 探针收到的工具结果结构无效。")
        self.tool_payload = payload
        return AIMessage(content="证据工具调用已完成。")


class _GraphEvidenceProbeJudge:
    """让 Graph 完成受控轮次，但不对答案或引用作质量判断。"""

    def __init__(self) -> None:
        """初始化调用计数。"""
        self.invocation_count = 0

    def invoke(self, _prompt: str) -> AIMessage:
        """返回固定拒答，使本探针只验证证据工具链路。"""
        self.invocation_count += 1
        return AIMessage(
            content=json.dumps(
                {
                    "decision": "REFUSED_NO_EVIDENCE",
                    "answer": "受控探针不评估答案。",
                    "citation_numbers": [],
                },
                ensure_ascii=False,
            )
        )


def _run_graph_evidence_probe(
    *,
    user_id: UUID,
    project_id: UUID,
    kb_id: UUID,
    question: str,
    tool_query: str,
    session_factory: Callable[[], Any],
    checkpoint_path: Path,
    thread_id: str,
) -> dict[str, Any]:
    """经真实 Archive Graph、工具和检索服务执行一次无网络探针。

    Args:
        user_id: 本次隔离验收创建的用户标识。
        project_id: 本次隔离验收创建的项目标识。
        kb_id: 项目绑定的隔离知识库标识。
        question: 需要由当前 HumanMessage 触发补充检索的原始问题。
        tool_query: 脚本化模型发给证据工具的检索词。
        session_factory: 每次真实工具调用创建隔离数据库会话的工厂。
        checkpoint_path: 本次隔离运行专用的 Checkpoint 文件路径。
        thread_id: 当前受控 Graph 调用的隔离线程标识。

    Returns:
        Graph 真实工具结果、工具事件和无网络脚本调用计数。
    """
    model = _GraphEvidenceProbeModel(query=tool_query)
    judge_model = _GraphEvidenceProbeJudge()
    with build_archive_runtime(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        session_factory=session_factory,
        model=model,
        judge_model=judge_model,
        checkpoint_path=checkpoint_path,
    ) as runtime:
        try:
            result = runtime.invoke(
                {
                    "messages": [HumanMessage(content=question)],
                    "user_id": str(user_id),
                    "project_id": str(project_id),
                    "kb_id": str(kb_id),
                    "tool_call_count": 0,
                },
                thread_id=thread_id,
            )
        finally:
            runtime.delete_thread(thread_id)
    if model.tool_payload is None:
        raise AcceptanceError("真实 Graph 没有返回证据工具结果。")
    return {
        "tool_payload": model.tool_payload,
        "turn": result.turn,
        "agent_model_invocations": model.invocation_count,
        "judge_model_invocations": judge_model.invocation_count,
    }


def _register_tracked(
    api: P14Api,
    *,
    run_tag: str,
    on_registered: Callable[[UUID], None],
) -> tuple[UUID, str]:
    """注册后立即保存主体 ID，再登录，保证登录失败时仍可精确清理。"""
    username = f"wb-retrieval-{run_tag}"
    password = f"WB-{uuid4().hex}-only"
    registered = _require_object(
        api.request(
            "POST",
            "/auth/register",
            expected_statuses=(201,),
            payload={"username": username, "name": "World Bank 检索验收", "password": password},
            authenticated=False,
        ),
        step="临时用户注册",
    )
    try:
        user_id = parse_registered_user_id(registered["id"])
    except (KeyError, ValueError) as exc:
        raise AcceptanceError("用户注册没有返回可追踪身份。") from exc
    on_registered(user_id)
    login = _require_object(
        api.request(
            "POST",
            "/auth/login",
            expected_statuses=(200,),
            payload={"username": username, "password": password},
            authenticated=False,
        ),
        step="临时用户登录",
    )
    token = login.get("access_token")
    if not isinstance(token, str) or not token:
        raise AcceptanceError("临时用户登录没有返回 Access Token。")
    api.headers = {"Authorization": f"Bearer {token}"}
    return user_id, username


def _verify_zero_residuals() -> dict[str, bool]:
    """在 finally 清理后重新读取四层隔离资源并要求全部为零。"""
    stage = "residuals.chroma_list"
    try:
        database_names = {
            str(item.get("name") if isinstance(item, dict) else getattr(item, "name", ""))
            for item in get_chroma_admin_client().list_databases(tenant=settings.chroma_tenant)
        }
    except BaseException as exc:
        error = AcceptanceError("检索-only 验收残留核验失败。")
        error.residual_probe_stage = stage
        error.residual_probe_error_code = _safe_failure_code(exc)
        raise error from None

    stage = "residuals.chroma_match"
    try:
        chroma_zero = settings.chroma_database not in database_names
    except BaseException as exc:
        error = AcceptanceError("检索-only 验收残留核验失败。")
        error.residual_probe_stage = stage
        error.residual_probe_error_code = _safe_failure_code(exc)
        raise error from None

    stage = "residuals.postgres_count"
    try:
        postgres_zero = _postgres_row_count() == 0
    except BaseException as exc:
        error = AcceptanceError("检索-only 验收残留核验失败。")
        error.residual_probe_stage = stage
        error.residual_probe_error_code = _safe_failure_code(exc)
        raise error from None

    stage = "residuals.files_check"
    try:
        files_zero = not settings.file_storage_path.exists()
    except BaseException as exc:
        error = AcceptanceError("检索-only 验收残留核验失败。")
        error.residual_probe_stage = stage
        error.residual_probe_error_code = _safe_failure_code(exc)
        raise error from None

    stage = "residuals.checkpoint_check"
    try:
        checkpoint_zero = not settings.agent_checkpoint_path.exists()
    except BaseException as exc:
        error = AcceptanceError("检索-only 验收残留核验失败。")
        error.residual_probe_stage = stage
        error.residual_probe_error_code = _safe_failure_code(exc)
        raise error from None

    counts = {
        "postgres_zero": postgres_zero,
        "chroma_zero": chroma_zero,
        "files_zero": files_zero,
        "checkpoint_zero": checkpoint_zero,
    }
    if not all(counts.values()):
        error = AcceptanceError("检索-only 验收清理后仍有持久化残留。")
        error.zero_residuals = counts.copy()
        error.residual_probe_stage = "residuals.validate"
        error.residual_probe_error_code = _safe_failure_code(error)
        raise error
    return counts


def _delete_isolated_chroma_database() -> None:
    """删除本次专用 Collection 和 Database，不触碰其他 Chroma 命名空间。"""
    import chromadb

    client = get_chroma_client()
    try:
        client.delete_collection(name=settings.chroma_final_collection)
    except Exception as exc:
        from chromadb.errors import NotFoundError

        if not isinstance(exc, NotFoundError):
            raise AcceptanceError("专用 Chroma Collection 清理失败。") from exc
    try:
        admin_client = chromadb.AdminClient(
            settings=chromadb.config.Settings(
                chroma_api_impl="chromadb.api.fastapi.FastAPI",
                chroma_server_host=settings.chroma_host,
                chroma_server_http_port=settings.chroma_port,
            )
        )
        admin_client.delete_database(
            name=settings.chroma_database,
            tenant=settings.chroma_tenant,
        )
    except Exception as exc:
        from chromadb.errors import NotFoundError

        if not isinstance(exc, NotFoundError):
            raise AcceptanceError("专用 Chroma Database 清理失败。") from exc


def _remove_local_run_targets() -> None:
    """确认无数据并截断 WAL 后，删除本次 Checkpoint、上传目录和运行目录。"""
    file_root = settings.file_storage_path
    checkpoint = settings.agent_checkpoint_path
    if _stored_file_count(file_root) != 0 or _checkpoint_row_count(checkpoint) != 0:
        raise AcceptanceError("本地隔离目录或 Checkpoint 仍含数据，拒绝删除。")
    if checkpoint.exists():
        try:
            connection = sqlite3.connect(checkpoint, timeout=0)
            try:
                journal_mode = str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower()
                if journal_mode == "wal":
                    checkpoint_state = connection.execute(
                        "PRAGMA wal_checkpoint(TRUNCATE)"
                    ).fetchone()
                    if checkpoint_state is None or int(checkpoint_state[0]) != 0:
                        raise AcceptanceError(
                            "隔离 Checkpoint 仍被占用，拒绝删除运行目录。"
                        )
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise AcceptanceError(
                "隔离 Checkpoint 无法安全关闭，拒绝删除运行目录。"
            ) from exc
        for suffix in ("wal", "shm"):
            sidecar = Path(f"{checkpoint}-{suffix}")
            if sidecar.exists():
                sidecar.unlink()
        checkpoint.unlink()
    if file_root.exists():
        for child in sorted(file_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file():
                raise AcceptanceError("隔离文件目录仍含文件，拒绝递归删除。")
            child.rmdir()
        file_root.rmdir()
    if checkpoint.parent.exists():
        checkpoint.parent.rmdir()


def _close_api(api: P14Api) -> None:
    api.close()


def _cleanup_and_verify(
    *,
    api: P14Api,
    user_id: UUID | None,
    seeded: list[tuple[str, str]],
    project_ids: dict[str, str],
) -> dict[str, bool]:
    """执行项目清理、清除专用资源并验证四层归零。

    Args:
        api: 用于项目范围清理的验收 API 客户端。
        user_id: 本次验收用户；为空时跳过业务范围清理。
        seeded: 本次验收创建的项目和文档范围。
        project_ids: 本次验收的项目标识映射。
    """
    cleanup_error: BaseException | None = None
    residual_probe_error: BaseException | None = None
    cleanup_failed_steps: list[str] = []
    if user_id is not None:
        try:
            _cleanup_seeded_scope(api, seeded=seeded, project_ids=project_ids, user_id=user_id)
        except BaseException as exc:
            cleanup_error = exc
            cleanup_failed_steps.append("BUSINESS_SCOPE_CLEANUP")
    cleanup_steps = (
        (_close_api, "API_CLOSE"),
        (lambda _api: _delete_isolated_chroma_database(), "CHROMA_DATABASE_DELETE"),
        (lambda _api: _remove_local_run_targets(), "LOCAL_TARGET_DELETE"),
    )
    for cleanup, step_code in cleanup_steps:
        try:
            cleanup(api)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
            cleanup_failed_steps.append(step_code)
    try:
        residuals = _verify_zero_residuals()
    except BaseException as exc:
        residual_probe_error = exc
        cleanup_error = cleanup_error or exc
        cleanup_failed_steps.append("ZERO_RESIDUAL_VERIFY")
        residuals = {}
    if cleanup_error is not None:
        error = AcceptanceError("检索-only 验收清理或四层归零核验失败。")
        error.cleanup_failed_steps = tuple(
            step for step in cleanup_failed_steps if step in CLEANUP_FAILURE_STEP_CODES
        )
        probe_error = residual_probe_error or cleanup_error
        probe_stage = getattr(probe_error, "residual_probe_stage", None)
        if probe_stage in _RESIDUAL_PROBE_STAGES:
            error.residual_probe_stage = probe_stage
        zero_residuals = getattr(probe_error, "zero_residuals", None)
        if (
            isinstance(zero_residuals, dict)
            and set(zero_residuals) == _ZERO_RESIDUAL_KEYS
            and all(type(value) is bool for value in zero_residuals.values())
        ):
            error.zero_residuals = zero_residuals.copy()
        residual_probe_error_code = _allowlisted_failure_code(
            getattr(probe_error, "residual_probe_error_code", None)
        )
        if residual_probe_error_code is not None:
            error.residual_probe_error_code = residual_probe_error_code
        raise error from None
    return residuals


def _copy_safe_failure_diagnostics(
    destination: BaseException,
    source: BaseException,
    *,
    retrieval_stage: str | None = None,
) -> None:
    """只复制验收阶段、清理步骤和残留探针的白名单诊断字段。"""
    safe_stage = getattr(source, "retrieval_stage", None)
    if safe_stage not in _SAFE_RETRIEVAL_STAGES:
        safe_stage = retrieval_stage
    if safe_stage in _SAFE_RETRIEVAL_STAGES:
        destination.retrieval_stage = safe_stage
    operation_code = getattr(source, "retrieval_operation_code", None)
    if operation_code not in _SAFE_RETRIEVAL_OPERATION_CODES:
        operation_code = getattr(source, "safe_code", None)
    if operation_code in _SAFE_RETRIEVAL_OPERATION_CODES:
        destination.retrieval_operation_code = operation_code
    http_status = getattr(source, "retrieval_http_status", None)
    if type(http_status) is not int:
        http_status = getattr(source, "http_status", None)
    if type(http_status) is int and 100 <= http_status <= 599:
        destination.retrieval_http_status = http_status
    preflight_error_code = _allowlisted_failure_code(
        getattr(source, "preflight_error_code", None)
    )
    if preflight_error_code is not None:
        destination.preflight_error_code = preflight_error_code
    residual_probe_error_code = _allowlisted_failure_code(
        getattr(source, "residual_probe_error_code", None)
    )
    if residual_probe_error_code is not None:
        destination.residual_probe_error_code = residual_probe_error_code
    cleanup_steps = getattr(source, "cleanup_failed_steps", ())
    if isinstance(cleanup_steps, (tuple, list)):
        safe_steps = tuple(step for step in cleanup_steps if step in CLEANUP_FAILURE_STEP_CODES)
        if safe_steps:
            destination.cleanup_failed_steps = safe_steps
    probe_stage = getattr(source, "residual_probe_stage", None)
    if probe_stage in _RESIDUAL_PROBE_STAGES:
        destination.residual_probe_stage = probe_stage
    zero_residuals = getattr(source, "zero_residuals", None)
    if (
        isinstance(zero_residuals, dict)
        and set(zero_residuals) == _ZERO_RESIDUAL_KEYS
        and all(type(value) is bool for value in zero_residuals.values())
    ):
        destination.zero_residuals = zero_residuals.copy()


def run_with_cleanup(
    *,
    api: P14Api,
    user_id: UUID | str,
    seeded: list[tuple[str, str]],
    project_ids: dict[str, str],
    operation: Callable[[], Any],
) -> Any:
    """保证操作失败后仍按记录的主体/项目/文档范围清理并验证四层归零。"""
    primary: BaseException | None = None
    value: Any = None
    cleanup_error: BaseException | None = None
    try:
        value = operation()
    except BaseException as exc:
        primary = exc
    finally:
        try:
            _cleanup_and_verify(
                api=api,
                user_id=UUID(str(user_id)),
                seeded=seeded,
                project_ids=project_ids,
            )
        except BaseException as exc:
            cleanup_error = exc
    if cleanup_error is not None:
        raise AcceptanceError("检索-only 验收清理或归零核验失败。") from cleanup_error
    if primary is not None:
        raise primary
    return value


def run_world_bank_retrieval_acceptance(
    *,
    base_url: str,
    result_file: Path | None = None,
    candidate_capture: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """在调用方已启动的隔离 API 上运行双项目 Top-30 检索验收，不调用问答模型。"""
    parsed_url = urlparse(base_url)
    if parsed_url.scheme != "http" or parsed_url.hostname not in {"127.0.0.1", "localhost"}:
        raise AcceptanceError("验收 API 只能使用本机回环 HTTP 地址。")
    retrieval_stage = "prepare.empty_target_validation"
    try:
        _validate_result_file(result_file)
        _assert_empty_targets()
        retrieval_stage = "prepare.source_validation"
        sources = verify_source_manifests()
        dataset = sources["dataset"]
        query_plan = build_query_plan(_lushan_queries(dataset), _BELARUS_QUERIES)
        combined_label = _make_combined_label(sources)
        api = P14Api(base_url)
    except BaseException as exc:
        error = AcceptanceError("检索-only 验收准备失败。")
        _copy_safe_failure_diagnostics(error, exc, retrieval_stage=retrieval_stage)
        raise error from None
    user_id: UUID | None = None
    registered_identity: dict[str, UUID] = {}
    project_ids: dict[str, str] = {}
    seeded: list[tuple[str, str]] = []
    try:
        retrieval_stage = "prepare.identity_registration"
        user_id, username = _register_tracked(
            api,
            run_tag=uuid4().hex[:12],
            on_registered=lambda identity: registered_identity.__setitem__("user_id", identity),
        )
        retrieval_stage = "prepare.document_seeding"
        filenames_by_project, _ = _seed_confirmed_documents(
            api,
            document_label=combined_label,
            run_tag="world-bank-retrieval",
            project_ids=project_ids,
            seeded=seeded,
            evaluation_root=PROJECT_ROOT,
        )
        entry_map = _entry_map(dataset)
        outcomes = []
        retrieval_stage = "prepare.candidate_query_ranking"
        with Session(engine) as session:
            for item in query_plan:
                case_id = str(item["query_kind"])
                expected = (
                    _diagnostic_expected_evidence(entry_map[case_id])
                    if case_id.startswith("LUSHAN-") and case_id != "LUSHAN-04"
                    else None
                )
                outcome = _rank_query(
                    session=session,
                    user_id=user_id,
                    project_id=UUID(project_ids[str(item["project_key"])]),
                    query=str(item["original_query"]),
                    case_id=case_id,
                    expected_lushan=expected,
                    supplementary_query=item["supplementary_query"],
                    candidate_capture=candidate_capture,
                )
                outcomes.append(outcome)
            lushan_project_id = UUID(project_ids["lushan"])
            lushan_project = session.get(Project, lushan_project_id)
            if lushan_project is None or lushan_project.owner_id != user_id:
                raise AcceptanceError("隔离芦山项目缺失或所有权不匹配。")

        retrieval_stage = "prepare.graph_evidence_probe"
        lushan_question = next(
            str(item["original_query"])
            for item in query_plan
            if item["query_kind"] == "LUSHAN-01"
        )
        graph_probe_result = _run_graph_evidence_probe(
            user_id=user_id,
            project_id=lushan_project_id,
            kb_id=lushan_project.kb_id,
            question=lushan_question,
            tool_query="贷款金额",
            session_factory=lambda: Session(engine),
            checkpoint_path=settings.agent_checkpoint_path,
            thread_id=f"fr042-graph-probe-{uuid4().hex}",
        )
        expected_lushan_evidence = _diagnostic_expected_evidence(entry_map["LUSHAN-01"])
        graph_results = graph_probe_result["tool_payload"].get("results")
        if not isinstance(graph_results, list) or len(graph_results) > 8:
            raise AcceptanceError("FR-042 Graph 探针没有返回合法 Top-8 证据列表。")
        graph_target_ranks = [
            rank
            for rank, item in enumerate(graph_results, start=1)
            if isinstance(item, dict)
            and item_contains_expected_evidence(item, expected_lushan_evidence)
        ]
        if not graph_probe_result["tool_payload"].get("found") or not graph_target_ranks:
            raise AcceptanceError("FR-042 Graph Top-8 未包含 LUSHAN-01 冻结目标证据。")
        graph_evidence_summary = {
            "external_model_calls": 0,
            "scripted_agent_invocations": graph_probe_result["agent_model_invocations"],
            "scripted_judge_invocations": graph_probe_result["judge_model_invocations"],
            "tool_call_count": graph_probe_result["turn"].tool_call_count,
            "tool_status": graph_probe_result["turn"].tool_events[0].status,
            "top8_count": len(graph_results),
            "target_evidence_ranks": graph_target_ranks,
            "target_in_top8": True,
            "answer_quality_evaluated": False,
        }
        summary = {
            "evaluation": "retrieval-only",
            "model_calls": 0,
            "project_count": len(project_ids),
            "documents_by_project": {key: len(value) for key, value in filenames_by_project.items()},
            "source_manifest_checks": {
                "lushan_document_count": len(sources["lushan_label"]["normal_documents"]),
                "belarus_document_count": 1,
                "belarus_source_sha256_verified": True,
            },
            "cases": outcomes,
            "fr042_graph_probe": graph_evidence_summary,
        }
    except BaseException as primary:
        cleanup_error = None
        if retrieval_stage == "prepare.document_seeding":
            operation_code = getattr(api, "safe_code", None)
            if operation_code in _SAFE_RETRIEVAL_OPERATION_CODES:
                primary.retrieval_operation_code = operation_code
            http_status = getattr(primary, "http_status", None)
            if type(http_status) is int and 100 <= http_status <= 599:
                primary.retrieval_http_status = http_status
        cleanup_user_id = user_id or registered_identity.get("user_id")
        try:
            _cleanup_and_verify(
                api=api,
                user_id=cleanup_user_id,
                seeded=seeded,
                project_ids=project_ids,
            )
        except BaseException as cleanup_exc:
            cleanup_error = cleanup_exc
        if cleanup_error:
            error = AcceptanceError("检索-only 验收失败且清理未通过。")
            _copy_safe_failure_diagnostics(error, primary, retrieval_stage=retrieval_stage)
            _copy_safe_failure_diagnostics(error, cleanup_error)
            # 阶段按主体、清理、外层顺序择一；清理诊断字段仍以清理异常为准。
            for stage in (
                getattr(primary, "retrieval_stage", None),
                getattr(cleanup_error, "retrieval_stage", None),
                retrieval_stage,
            ):
                if isinstance(stage, str) and stage in _SAFE_RETRIEVAL_STAGES:
                    error.retrieval_stage = stage
                    break
            primary_preflight_code = _allowlisted_failure_code(
                getattr(primary, "preflight_error_code", None)
            )
            if primary_preflight_code is not None:
                error.preflight_error_code = primary_preflight_code
            else:
                error.__dict__.pop("preflight_error_code", None)
            cleanup_probe_code = _allowlisted_failure_code(
                getattr(cleanup_error, "residual_probe_error_code", None)
            )
            if cleanup_probe_code is not None:
                error.residual_probe_error_code = cleanup_probe_code
            else:
                error.__dict__.pop("residual_probe_error_code", None)
            raise error from None
        error = AcceptanceError("检索-only 验收失败；隔离清理已尝试。")
        _copy_safe_failure_diagnostics(error, primary, retrieval_stage=retrieval_stage)
        raise error from None
    else:
        cleanup_error = None
        retrieval_stage = "cleanup"
        try:
            summary["cleanup"] = _cleanup_and_verify(
                api=api,
                user_id=user_id,
                seeded=seeded,
                project_ids=project_ids,
            )
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            error = AcceptanceError("检索-only 验收清理或归零核验失败。")
            _copy_safe_failure_diagnostics(error, cleanup_error, retrieval_stage=retrieval_stage)
            raise error from None
    _write_result(result_file, summary)
    return summary


def main() -> int:
    """解析 CLI 参数并只打印安全摘要。"""
    parser = argparse.ArgumentParser(description="运行 World Bank 隔离检索-only 验收。")
    parser.add_argument("--base-url", required=True, help="调用方已启动的隔离本机 API 地址。")
    parser.add_argument(
        "--result-file",
        type=Path,
        default=BELARUS_CORPUS_ROOT / "acceptance" / f"retrieval-only-{uuid4().hex}.json",
        help="写入忽略的安全摘要文件；默认名唯一且不覆盖。",
    )
    args = parser.parse_args()
    try:
        run_world_bank_retrieval_acceptance(base_url=args.base_url, result_file=args.result_file)
    except AcceptanceError as exc:
        print(json.dumps({"status": "failed", "code": exc.safe_code}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "completed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
