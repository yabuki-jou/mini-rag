"""运行芦山 P153548 公开 PDF 的隔离持久层真实验收。"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from statistics import median
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import UUID

import httpx
from sqlalchemy import inspect, text
from sqlmodel import Session

from app.core.config import PROJECT_ROOT, settings
from app.db import engine
from app.models import Project
from app.services.archive.evidence_matching import item_contains_expected_evidence
from app.services.archive.questions import (
    _build_archive_prompt,
    judge_archive_answer,
)
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
from app.services.infrastructure.ai_models import get_chat_model
from app.services.infrastructure.chroma import get_chroma_client
from scripts.archive_v1_p14_acceptance import (
    AcceptanceError,
    P14Api,
    _cleanup_seeded_scope,
    _read_safe_internal_diagnostic,
    _register_and_login,
    _require_object,
    _seed_confirmed_documents,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "pixie_qa" / "datasets" / "lushan-p153548-smoke.json"
DEFAULT_CORPUS_ROOT = (
    PROJECT_ROOT
    / "tests"
    / "pytest_docs"
    / "public_projects"
    / "lushan_earthquake_p153548"
)
_TARGET_FILENAMES = {
    "2016_project_appraisal_document.pdf",
    "2022_restructuring_paper.pdf",
    "2024_completion_report.pdf",
    "2024_completion_report_review.pdf",
}
_TYPE_EVIDENCE = {
    "2016_project_appraisal_document.pdf": "PROJECT APPRAISAL DOCUMENT",
    "2022_restructuring_paper.pdf": "RESTRUCTURING PAPER",
    "2024_completion_report.pdf": "IMPLEMENTATION COMPLETION AND RESULTS REPORT",
    "2024_completion_report_review.pdf": "Implementation Completion Report (ICR) Review",
}
_ALEMBIC_TABLES = {"alembic_version"}
_SUPPLEMENTARY_QUERY_REPLACEMENT = ("世界银行", "IBRD IDA")
_FUSION_GROUP_NAMES = ("A", "B", "C", "D", "E")
_FUSION_ROUND_ORDER = ("ABCDE", "CDEAB", "EABCD")
_MODEL_CALL_LIMIT = len(_FUSION_GROUP_NAMES) * len(_FUSION_ROUND_ORDER)


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    """读取验收 JSON，并拒绝缺失或非对象顶层结构。

    Args:
        path: 要读取的 JSON 文件。
        label: 安全错误消息中使用的资料名称。

    Returns:
        已解析的 JSON 对象。
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{label}无法读取。") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label}格式无效。")
    return value


def _dataset_entries(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """返回结构有效的数据集条目。

    Args:
        dataset: P153548 Pixie 数据集。

    Returns:
        按文件顺序排列的数据集条目。
    """
    entries = dataset.get("entries")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise AcceptanceError("P153548 数据集条目格式无效。")
    return entries


def build_document_label(
    dataset: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    """从固定集和来源清单构造既有 P14 上传确认输入。

    Args:
        dataset: 已提交的 P153548 Agent smoke 数据集。
        manifest: 本地公开 PDF 来源与哈希清单。

    Returns:
        可交给 ``_seed_confirmed_documents`` 的单项目文档标注。
    """
    expected_filenames = {
        str(filename)
        for entry in _dataset_entries(dataset)
        for filename in entry.get("eval_metadata", {}).get(
            "expected_document_filenames", []
        )
    }
    # 无据题的干扰文件不进入正式验收范围；目标文件必须与已审计的四份原始 PDF 完全一致。
    if expected_filenames != _TARGET_FILENAMES:
        raise AcceptanceError("P153548 数据集目标文件与冻结范围不一致。")

    raw_documents = manifest.get("documents")
    if not isinstance(raw_documents, list) or not all(
        isinstance(item, dict) for item in raw_documents
    ):
        raise AcceptanceError("P153548 来源清单格式无效。")
    by_filename = {Path(str(item.get("path", ""))).name: item for item in raw_documents}
    if not expected_filenames.issubset(by_filename):
        raise AcceptanceError("P153548 数据集文件未全部登记在来源清单。")
    injected_filenames = {
        str(candidate.get("filename", ""))
        for entry in _dataset_entries(dataset)
        for wrapped in entry.get("eval_input", [])
        if isinstance(wrapped, dict)
        and str(wrapped.get("name", "")).startswith(
            "archive_agent_evidence_retrieval"
        )
        for candidate in wrapped.get("value", [])
        if isinstance(candidate, dict)
    }
    if not injected_filenames.issubset(by_filename):
        raise AcceptanceError("P153548 注入候选未全部登记在来源清单。")

    documents: list[dict[str, Any]] = []
    for index, filename in enumerate(sorted(expected_filenames), start=1):
        source = by_filename[filename]
        relative_path = str(source.get("path", ""))
        title = str(source.get("title", "")).strip()
        digest = str(source.get("sha256", "")).strip()
        if not relative_path or not title or len(digest) != 64:
            raise AcceptanceError("P153548 来源清单缺少路径、标题或哈希。")
        title_excerpt = (
            "Lushan Earthquake Reconstruction Project (P153548)"
            if filename == "2024_completion_report_review.pdf"
            else "LUSHAN EARTHQUAKE RECONSTRUCTION AND RISK REDUCTION PROJECT"
        )
        documents.append(
            {
                "id": f"L-{index:02d}",
                "project_id": "lushan",
                "relative_path": relative_path,
                "source_format": "PDF",
                "scenario": "public-persistence-acceptance",
                "file_sha256": digest,
                "expected_fields": {
                    "TITLE": {
                        "value": title,
                        "evidence": [
                            {
                                "location_type": "PDF_PAGE",
                                "location_start": 1,
                                "location_end": 1,
                                "excerpt": title_excerpt,
                            }
                        ],
                    },
                    "DOCUMENT_TYPE": {
                        "value": "OTHER",
                        "evidence": [
                            {
                                "location_type": "PDF_PAGE",
                                "location_start": 1,
                                "location_end": 1,
                                "excerpt": _TYPE_EVIDENCE[filename],
                            }
                        ],
                    },
                },
            }
        )
    return {
        "dataset_id": "lushan-p153548-persistence-v1",
        "projects": [{"id": "lushan", "name": "芦山地震恢复重建项目"}],
        "normal_documents": documents,
    }


def _entry_map(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按案例编号索引固定集条目。

    Args:
        dataset: P153548 Pixie 数据集。
    """
    result: dict[str, dict[str, Any]] = {}
    for entry in _dataset_entries(dataset):
        metadata = entry.get("eval_metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("case_id"), str):
            raise AcceptanceError("P153548 数据集缺少案例编号。")
        result[metadata["case_id"]] = entry
    return result


def _expected_evidence(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """从注入候选中提取当前案例的标准来源证据。

    Args:
        entry: 单个 Pixie 数据集条目。
    """
    metadata = entry.get("eval_metadata", {})
    target_filenames = set(metadata.get("expected_document_filenames", []))
    wraps = entry.get("eval_input", [])
    if not isinstance(wraps, list):
        raise AcceptanceError("P153548 案例缺少候选输入。")
    for wrapped in wraps:
        if not isinstance(wrapped, dict) or wrapped.get("name") != "archive_agent_evidence_retrieval":
            continue
        value = wrapped.get("value")
        if not isinstance(value, list):
            raise AcceptanceError("P153548 标准证据候选格式无效。")
        return [
            item
            for item in value
            if isinstance(item, dict) and item.get("filename") in target_filenames
        ]
    raise AcceptanceError("P153548 案例缺少标准证据候选。")


def _diagnostic_expected_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    """把案例的单文件标准证据转换为既有诊断端点输入。

    Args:
        entry: 单个有据 Pixie 数据集条目。
    """
    expected = _expected_evidence(entry)
    filenames = {str(item.get("filename", "")) for item in expected}
    if len(filenames) != 1 or "" in filenames:
        raise AcceptanceError("P153548 诊断案例必须绑定唯一目标文件。")
    return {
        "relative_path": filenames.pop(),
        "items": [
            {
                "location_type": item.get("location_type"),
                "location_start": item.get("location_start"),
                "location_end": item.get("location_end"),
                "excerpt": item.get("excerpt"),
            }
            for item in expected
        ],
    }


def summarize_retrieval_cases(
    dataset: dict[str, Any], responses: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """汇总三条有据题的真实 Top-8 覆盖，并记录无据探针。

    Args:
        dataset: P153548 Pixie 数据集。
        responses: 按案例编号保存的公开检索响应。

    Returns:
        不含资源标识、原文和分数的聚合计数。
    """
    entries = _entry_map(dataset)
    case_ids = ("LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04")
    if set(responses) != set(case_ids):
        raise AcceptanceError("P153548 真实检索响应案例不完整。")
    grounded_count = grounded_covered = no_evidence_count = 0
    coverage_by_case: dict[str, bool] = {}
    target_document_by_case: dict[str, bool] = {}
    returned_count_by_case: dict[str, int] = {}
    for case_id in case_ids:
        entry = entries[case_id]
        response = responses[case_id]
        items = response.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise AcceptanceError("P153548 真实检索响应缺少 items。")
        if response.get("requested_top_k") != 8 or response.get("returned_count") != len(items):
            raise AcceptanceError("P153548 真实检索响应 Top-8 计数不一致。")
        returned_count_by_case[case_id] = len(items)
        category = entry["eval_metadata"]["category"]
        if category == "NO_EVIDENCE":
            no_evidence_count += 1
            continue
        grounded_count += 1
        expected_filenames = set(
            entry["eval_metadata"].get("expected_document_filenames", [])
        )
        target_document_by_case[case_id] = any(
            item.get("filename") in expected_filenames for item in items
        )
        covered = False
        for expected in _expected_evidence(entry):
            evidence = {
                "relative_path": expected["filename"],
                "items": [
                    {
                        "location_type": expected["location_type"],
                        "location_start": expected["location_start"],
                        "location_end": expected["location_end"],
                        "excerpt": expected["excerpt"],
                    }
                ],
            }
            if any(item_contains_expected_evidence(item, evidence) for item in items):
                covered = True
                break
        grounded_covered += int(covered)
        coverage_by_case[case_id] = covered
    return {
        "question_count": len(case_ids),
        "grounded_question_count": grounded_count,
        "grounded_covered_count": grounded_covered,
        "no_evidence_question_count": no_evidence_count,
        "grounded_coverage_by_case": coverage_by_case,
        "grounded_target_document_present_by_case": target_document_by_case,
        "returned_count_by_case": returned_count_by_case,
        "quality_passed": grounded_covered == grounded_count,
    }


def validate_retrieval_cases(
    dataset: dict[str, Any], responses: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """严格验证三条有据题全部进入真实 Top-8。

    Args:
        dataset: P153548 Pixie 数据集。
        responses: 按案例编号保存的公开检索响应。

    Returns:
        与既有测试兼容的聚合计数。
    """
    summary = summarize_retrieval_cases(dataset, responses)
    if not summary["quality_passed"]:
        raise AcceptanceError("P153548 有据题未全部进入真实 Top-8。")
    return {
        key: int(summary[key])
        for key in (
            "question_count",
            "grounded_question_count",
            "grounded_covered_count",
            "no_evidence_question_count",
        )
    }


def summarize_retrieval_diagnostic(
    case_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """把双排序诊断压缩为不含候选标识和正文的案例级结论。

    Args:
        case_id: 固定集案例编号。
        payload: 开发环境诊断端点返回的安全候选投影。

    Returns:
        标准证据和同文档候选在两个排序阶段的位置摘要。
    """
    diagnostic = _read_safe_internal_diagnostic(payload)
    candidates = diagnostic["candidates"]
    expected = [
        candidate
        for candidate in candidates
        if candidate["public_coverage_match"] is True
    ]
    same_document = [
        candidate
        for candidate in candidates
        if candidate["candidate_kind"] == "SAME_DOCUMENT"
    ]
    expected_candidate = min(
        expected,
        key=lambda candidate: int(candidate["reranker_rank"]),
        default=None,
    )
    return {
        "case_id": case_id,
        "chroma_candidate_count": diagnostic["chroma_candidate_count"],
        "candidate_count": diagnostic["candidate_count"],
        "reranker_query_mode": diagnostic["reranker_query_mode"],
        "expected_evidence_in_top30": expected_candidate is not None,
        "expected_dense_rank": (
            int(expected_candidate["dense_rank"])
            if expected_candidate is not None
            else None
        ),
        "expected_dense_distance": (
            float(expected_candidate["dense_distance"])
            if expected_candidate is not None
            else None
        ),
        "expected_reranker_rank": (
            int(expected_candidate["reranker_rank"])
            if expected_candidate is not None
            else None
        ),
        "expected_reranker_score": (
            float(expected_candidate["reranker_score"])
            if expected_candidate is not None
            else None
        ),
        "expected_evidence_in_top8": bool(
            expected_candidate is not None
            and int(expected_candidate["reranker_rank"]) <= 8
        ),
        "same_document_candidate_count": len(same_document),
        "same_document_best_dense_rank": min(
            (int(candidate["dense_rank"]) for candidate in same_document),
            default=None,
        ),
        "same_document_best_reranker_rank": min(
            (int(candidate["reranker_rank"]) for candidate in same_document),
            default=None,
        ),
        "isolation_violation": any(
            candidate["isolation_violation"] is True for candidate in candidates
        ),
    }


def _candidate_chunk_id(item: object) -> str:
    """读取候选的稳定 Chunk 身份，兼容纯函数测试中的字符串候选。

    Args:
        item: 档案检索候选或仅用于测试的字符串标识。
    """
    chunk_id = item if isinstance(item, str) else getattr(item, "chunk_id", None)
    if not isinstance(chunk_id, str) or not chunk_id:
        raise AcceptanceError("双路检索候选缺少有效 Chunk 标识。")
    return chunk_id


def _merge_candidate_pools(
    original: list[tuple[float, Any]], supplementary: list[tuple[float, Any]]
) -> tuple[list[tuple[float, Any]], dict[str, int], dict[str, int]]:
    """按 Chunk ID 合并两路候选，并分别保留各自 dense 名次。

    Args:
        original: 原问题 dense 查询的距离与已校验候选。
        supplementary: 补充问题 dense 查询的距离与已校验候选。

    Returns:
        去重候选及原问题、补充问题各自的 1-based dense 名次映射。
    """
    original_ranks = {
        _candidate_chunk_id(item): rank
        for rank, (_, item) in enumerate(
            sorted(original, key=lambda pair: (pair[0], _candidate_chunk_id(pair[1]))),
            start=1,
        )
    }
    supplementary_ranks = {
        _candidate_chunk_id(item): rank
        for rank, (_, item) in enumerate(
            sorted(
                supplementary,
                key=lambda pair: (pair[0], _candidate_chunk_id(pair[1])),
            ),
            start=1,
        )
    }
    merged_by_id: dict[str, tuple[float, Any]] = {}
    for distance, item in [*original, *supplementary]:
        chunk_id = _candidate_chunk_id(item)
        current = merged_by_id.get(chunk_id)
        if current is None or distance < current[0]:
            merged_by_id[chunk_id] = (distance, item)
    merged = sorted(
        merged_by_id.values(),
        key=lambda pair: (pair[0], _candidate_chunk_id(pair[1])),
    )
    return merged, original_ranks, supplementary_ranks


def _reciprocal_rank_fusion(
    first_scores: dict[str, float], second_scores: dict[str, float], *, k: int = 10
) -> dict[str, float]:
    """按两路分数名次计算 RRF，未出现在某一路的候选不获该路分值。

    Args:
        first_scores: 第一种查询表达对合并池候选的 CrossEncoder 分数。
        second_scores: 第二种查询表达对合并池候选的 CrossEncoder 分数。
        k: RRF 平滑常数。
    """
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("RRF 平滑常数必须是正整数。")
    first_rank = {
        key: rank
        for rank, (key, _) in enumerate(
            sorted(first_scores.items(), key=lambda pair: (-pair[1], pair[0])), 1
        )
    }
    second_rank = {
        key: rank
        for rank, (key, _) in enumerate(
            sorted(second_scores.items(), key=lambda pair: (-pair[1], pair[0])), 1
        )
    }
    return {
        key: (1 / (k + first_rank[key]) if key in first_rank else 0.0)
        + (1 / (k + second_rank[key]) if key in second_rank else 0.0)
        for key in first_scores.keys() | second_scores.keys()
    }


def _minmax_fusion(
    first_scores: dict[str, float], second_scores: dict[str, float]
) -> dict[str, float]:
    """分别归一化两路 CrossEncoder 分数后作等权平均。

    Args:
        first_scores: 第一种查询表达对合并池候选的 CrossEncoder 分数。
        second_scores: 第二种查询表达对合并池候选的 CrossEncoder 分数。
    """
    def normalize(scores: dict[str, float]) -> dict[str, float]:
        if not scores:
            return {}
        low, high = min(scores.values()), max(scores.values())
        if high == low:
            return dict.fromkeys(scores, 0.5)
        return {key: (score - low) / (high - low) for key, score in scores.items()}

    first = normalize(first_scores)
    second = normalize(second_scores)
    return {
        key: (first.get(key, 0.0) + second.get(key, 0.0)) / 2
        for key in first.keys() | second.keys()
    }


def _safe_candidate_key(chunk_id: str) -> str:
    """通过生产诊断使用的域分离哈希生成不可逆候选展示键。

    Args:
        chunk_id: 仅在服务端内存中使用的持久 Chunk 标识。
    """
    return _diagnostic_candidate_key(chunk_id=chunk_id)


def _summarize_dual_rankings(
    rankings: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """将排序映射为只含脱敏键和标准证据 Top-8 覆盖的安全摘要。

    Args:
        rankings: 按策略排列的候选；候选仅含内部 Chunk ID 与 exact/public 布尔标记。
    """
    summary: dict[str, Any] = {"rankings": {}}
    for method, candidates in rankings.items():
        top8 = candidates[:8]
        evidence_labels = sorted(
            {
                str(label)
                for candidate in candidates
                for label in candidate.get("coverage", {})
            }
        )
        evidence_summary: dict[str, dict[str, Any]] = {}
        for label in evidence_labels:
            exact_ranks = [
                rank
                for rank, candidate in enumerate(candidates, 1)
                if candidate.get("coverage", {}).get(label, {}).get("exact") is True
            ]
            public_ranks = [
                rank
                for rank, candidate in enumerate(candidates, 1)
                if candidate.get("coverage", {}).get(label, {}).get("public") is True
            ]
            exact_rank = min(exact_ranks, default=None)
            public_rank = min(public_ranks, default=None)
            evidence_summary[label] = {
                "exact_rank": exact_rank,
                "exact_in_top8": exact_rank is not None and exact_rank <= 8,
                "public_rank": public_rank,
                "public_in_top8": public_rank is not None and public_rank <= 8,
            }
        summary["rankings"][method] = {
            "candidate_count": len(candidates),
            "top8_candidate_keys": [
                _safe_candidate_key(_candidate_chunk_id(candidate["chunk_id"]))
                for candidate in top8
            ],
            "target_evidence": evidence_summary,
        }
    return summary


def _supplementary_query(original_query: str) -> str:
    """只将贷款问题中的“世界银行”指称替换为包容式机构缩写表达。

    Args:
        original_query: P153548 固定集中的原始问题。
    """
    source, replacement = _SUPPLEMENTARY_QUERY_REPLACEMENT
    if source not in original_query:
        raise AcceptanceError("P153548 原问题不含预期世界银行指称，无法构造补充查询。")
    return original_query.replace(source, replacement, 1)


def _build_fusion_answer_groups(
    *,
    original_candidates: list[tuple[float, Any]],
    union_candidates: list[tuple[float, Any]],
    original_scores: dict[str, float],
    supplementary_scores: dict[str, float],
    baseline_chunk_ids: list[str],
) -> dict[str, list[Any]]:
    """按计划生成 A-E 五组 Top-8，并验证 A 与正式单路顺序相同。

    Args:
        original_candidates: 原问题 dense Top-30 候选。
        union_candidates: 按 Chunk 去重后的双路候选并集。
        original_scores: 原问题在并集上的重排分数。
        supplementary_scores: 补充表达在并集上的重排分数。
        baseline_chunk_ids: 正式 FR-039 Top-8 的有序 Chunk 标识。

    Returns:
        只在请求内使用的五组有序候选。
    """
    distances = {item.chunk_id: distance for distance, item in union_candidates}

    def ordered(scores: dict[str, float], allowed: set[str] | None = None) -> list[str]:
        keys = scores.keys() if allowed is None else scores.keys() & allowed
        return sorted(
            keys,
            key=lambda key: (-scores[key], distances[key], key),
        )

    original_ids = {_candidate_chunk_id(item) for _, item in original_candidates}
    original_route_ids = ordered(original_scores, original_ids)[:8]
    if original_route_ids != baseline_chunk_ids:
        raise AcceptanceError(
            "A 组排序与正式单路基线不一致，停止融合回答对照。"
        )
    rrf_scores = _reciprocal_rank_fusion(
        original_scores, supplementary_scores, k=10
    )
    minmax_scores = _minmax_fusion(original_scores, supplementary_scores)
    score_groups = {
        "A": (original_scores, original_ids),
        "B": (original_scores, None),
        "C": (supplementary_scores, None),
        "D": (rrf_scores, None),
        "E": (minmax_scores, None),
    }
    candidate_by_id = {
        _candidate_chunk_id(item): item for _, item in union_candidates
    }
    groups: dict[str, list[Any]] = {}
    for name, (scores, allowed) in score_groups.items():
        groups[name] = [candidate_by_id[key] for key in ordered(scores, allowed)[:8]]
        if not groups[name]:
            raise AcceptanceError("融合回答对照组没有可用候选。")
    return groups


class _UsageCapturingModel:
    """仅读取一次模型响应中的 token 用量，不保留响应正文。"""

    def __init__(self, model: Any) -> None:
        """包装不自动重试的聊天模型。

        Args:
            model: 由显式 max_retries=0 创建的聊天模型。
        """
        self._model = model
        self.last_usage: dict[str, int] | None = None

    def invoke(self, prompt: str) -> Any:
        """转发模型调用并只提取整数用量元数据。

        Args:
            prompt: 判定层生成的请求提示，仅转发、不保存。
        """
        response = self._model.invoke(prompt)
        self.last_usage = _safe_usage_metadata(response)
        return response


def _safe_usage_metadata(response: Any) -> dict[str, int] | None:
    """从模型响应提取可用 token 计数，不复制响应正文或其他元数据。

    Args:
        response: 原始聊天模型响应。
    """
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(response, "response_metadata", None)
        usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
    if not isinstance(usage, dict):
        return None
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    result: dict[str, int] = {}
    for output_key, candidates in aliases.items():
        value = next((usage.get(key) for key in candidates if key in usage), None)
        if type(value) is int and value >= 0:
            result[output_key] = value
    return result or None


def _source_amount_visible(candidate: Any) -> bool:
    """判断引用摘录是否直接包含标准贷款金额的常见格式。

    Args:
        candidate: 本轮模型引用的服务端候选。
    """
    excerpt = str(getattr(candidate, "excerpt", "")).casefold()
    compact = "".join(excerpt.split()).replace("usd", "us$")
    return "300million" in compact and ("us$300million" in compact or "300million" in compact)


def _run_fusion_answer_trials(
    *,
    query: str,
    groups: dict[str, list[Any]],
    expected_evidence: dict[str, Any],
    model_factory: Any = get_chat_model,
    judge: Any = judge_archive_answer,
    confirm_preflight: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """按固定轮换对五组候选各执行三次原问题判定并生成脱敏结果。

    Args:
        query: 固定集中的用户原问题。
        groups: 已验证正式基线顺序的 A-E 候选。
        expected_evidence: 冻结集对 LUSHAN-01 的标准摘录标注。
        model_factory: 支持 max_retries 参数的模型工厂。
        judge: 现有公共档案答案判定函数。
        confirm_preflight: 用户审阅脱敏预检信息并确认外发的回调。

    Returns:
        不含问题、提示、回答正文、摘录或持久化标识的逐次摘要。
    """
    if tuple(groups) != _FUSION_GROUP_NAMES:
        raise AcceptanceError("融合回答对照必须恰好包含 A-E 五组。")
    preflight: dict[str, Any] = {}
    for name, candidates in groups.items():
        if not candidates or len(candidates) > 8:
            raise AcceptanceError("融合回答对照组候选数量不符合 Top-8。")
        prompt = _build_archive_prompt(query, candidates)
        exact = any(
            _candidate_matches_expected_evidence(
                item=candidate, expected_evidence=expected_evidence
            )
            for candidate in candidates
        )
        public = any(
            _candidate_publicly_covers_expected_evidence(
                item=candidate, expected_evidence=expected_evidence
            )
            for candidate in candidates
        )
        preflight[name] = {
            "candidate_count": len(candidates),
            "prompt_character_count": len(prompt),
            "exact_evidence_in_top8": exact,
            "public_evidence_in_top8": public,
            "scheduled_calls": len(_FUSION_ROUND_ORDER),
        }

    if confirm_preflight is None or not confirm_preflight(preflight):
        raise AcceptanceError("融合回答对照未获模型调用确认，未发送问题或候选。")

    trials_by_group: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _FUSION_GROUP_NAMES
    }
    attempted_calls = 0
    for round_number, round_order in enumerate(_FUSION_ROUND_ORDER, start=1):
        for position, name in enumerate(round_order, start=1):
            usage_model: _UsageCapturingModel | None = None
            trial: dict[str, Any] = {
                "round": round_number,
                "order_position": position,
                "status": "ERROR",
                "answer_status": None,
                "frozen_annotation_pass": False,
                "source_support_status": "not_supported",
                "citations": [],
                "latency_ms": None,
                "usage": None,
                "error_type": None,
            }
            started = perf_counter()
            try:
                usage_model = _UsageCapturingModel(model_factory(max_retries=0))
                attempted_calls += 1
                decision = judge(
                    question=query,
                    candidates=groups[name],
                    model=usage_model,
                )
                status = getattr(decision.answer_status, "value", decision.answer_status)
                citations: list[dict[str, Any]] = []
                selected: list[Any] = []
                for number in decision.citation_numbers:
                    if type(number) is not int or number < 1 or number > len(groups[name]):
                        raise AcceptanceError("答案判定返回越界引用编号。")
                    item = groups[name][number - 1]
                    selected.append(item)
                    citations.append(
                        {
                            "filename": str(item.filename),
                            "location_start": int(item.location_start),
                            "location_end": int(item.location_end),
                        }
                    )
                answer = str(decision.answer)
                fixed_amount_matched = "US$300 million" in answer
                frozen_pass = (
                    status == "ANSWERED"
                    and fixed_amount_matched
                    and any(
                        _candidate_publicly_covers_expected_evidence(
                            item=item, expected_evidence=expected_evidence
                        )
                        for item in selected
                    )
                )
                amount_cited = any(_source_amount_visible(item) for item in selected)
                if frozen_pass and amount_cited:
                    source_status = "supported_by_frozen_source"
                elif amount_cited:
                    source_status = "needs_manual_review"
                else:
                    source_status = "not_supported"
                trial.update(
                    {
                        "status": "COMPLETED",
                        "answer_status": str(status),
                        "fixed_amount_matched": fixed_amount_matched,
                        "frozen_annotation_pass": frozen_pass,
                        "source_support_status": source_status,
                        "citation_amount_visible": amount_cited,
                        "citations": citations,
                    }
                )
            except Exception as exc:
                # 不保留异常消息，因为供应商异常可能包含提示、响应片段或请求信息。
                trial["error_type"] = type(exc).__name__
            finally:
                trial["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                trial["usage"] = usage_model.last_usage if usage_model else None
            trials_by_group[name].append(trial)

    group_results: dict[str, Any] = {}
    for name, trials in trials_by_group.items():
        latencies = [trial["latency_ms"] for trial in trials if trial["latency_ms"] is not None]
        frozen_count = sum(trial["frozen_annotation_pass"] for trial in trials)
        source_count = sum(
            trial["source_support_status"] == "supported_by_frozen_source"
            for trial in trials
        )
        group_results[name] = {
            **preflight[name],
            "completed_calls": sum(trial["status"] == "COMPLETED" for trial in trials),
            "frozen_annotation_pass_count": frozen_count,
            "source_support_pass_count": source_count,
            "source_support_manual_review_count": sum(
                trial["source_support_status"] == "needs_manual_review"
                for trial in trials
            ),
            "latency_median_ms": round(median(latencies), 3) if latencies else None,
            "trials": trials,
        }
    return {
        "planned_model_calls": _MODEL_CALL_LIMIT,
        "attempted_calls": attempted_calls,
        "round_order": [list(order) for order in _FUSION_ROUND_ORDER],
        "groups": group_results,
    }


def _dual_retrieval_diagnostic(
    *,
    session: Session,
    user_id: UUID,
    project_id: UUID,
    query: str,
    expected_evidence: dict[str, Any],
    capture_internal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在已确认项目范围中运行双路 dense 并集与两表达 CrossEncoder 离线排序。

    Args:
        session: 当前隔离验收使用的数据库会话。
        user_id: 已注册验收用户的身份。
        project_id: 已确认验收项目的身份。
        query: 保持不变的原问题；回答问答链路仍使用此文本。
        expected_evidence: LUSHAN-01 的固定标准证据标注。
        capture_internal: 可选的请求内容器，不得写入结果文件。
    """
    project = session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise AcceptanceError("双路诊断项目不属于当前验收用户。")
    formal_ids = _formal_document_ids(
        user_id=user_id,
        project_id=project_id,
        kb_id=project.kb_id,
        session=session,
    )
    if not formal_ids:
        raise AcceptanceError("双路诊断没有可检索的已确认档案文档。")
    supplementary_query = _supplementary_query(query)
    original_dense_started = perf_counter()
    _, original_candidates = _query_validated_candidates(
        user_id=user_id,
        project_id=project_id,
        kb_id=project.kb_id,
        query=query,
        formal_ids=formal_ids,
    )
    original_dense_ms = (perf_counter() - original_dense_started) * 1000
    supplementary_dense_started = perf_counter()
    _, supplementary_candidates = _query_validated_candidates(
        user_id=user_id,
        project_id=project_id,
        kb_id=project.kb_id,
        query=supplementary_query,
        formal_ids=formal_ids,
    )
    supplementary_dense_ms = (perf_counter() - supplementary_dense_started) * 1000
    candidates, original_dense_ranks, supplementary_dense_ranks = _merge_candidate_pools(
        original_candidates, supplementary_candidates
    )
    if not candidates:
        raise AcceptanceError("双路诊断候选并集为空。")

    contents = [item.excerpt for _, item in candidates]
    original_rerank_started = perf_counter()
    original_scores = score_archive_candidates(
        query=_build_archive_reranker_query_expression(query), contents=contents
    )
    original_rerank_ms = (perf_counter() - original_rerank_started) * 1000
    supplementary_rerank_started = perf_counter()
    supplementary_scores = score_archive_candidates(
        query=_build_archive_reranker_query_expression(supplementary_query),
        contents=contents,
    )
    supplementary_rerank_ms = (perf_counter() - supplementary_rerank_started) * 1000
    if len(original_scores) != len(candidates) or len(supplementary_scores) != len(candidates):
        raise AcceptanceError("双路诊断重排分数与候选并集数量不一致。")
    original_score_map = {
        item.chunk_id: float(score)
        for score, (_, item) in zip(original_scores, candidates, strict=True)
    }
    supplementary_score_map = {
        item.chunk_id: float(score)
        for score, (_, item) in zip(supplementary_scores, candidates, strict=True)
    }
    if not all(
        math.isfinite(score)
        for score in [*original_score_map.values(), *supplementary_score_map.values()]
    ):
        raise AcceptanceError("双路诊断重排分数包含非有限值。")
    fusion_started = perf_counter()
    distances = {item.chunk_id: distance for distance, item in candidates}
    fused_scores = {
        "rrf_k10": _reciprocal_rank_fusion(
            original_score_map, supplementary_score_map, k=10
        ),
        "minmax_equal": _minmax_fusion(original_score_map, supplementary_score_map),
    }
    score_methods = {
        "original_reranker": original_score_map,
        "supplementary_reranker": supplementary_score_map,
        **fused_scores,
    }
    coverage_by_chunk: dict[str, dict[str, bool]] = {}
    for _, item in candidates:
        coverage_by_chunk[item.chunk_id] = {
            "exact": _candidate_matches_expected_evidence(
                item=item, expected_evidence=expected_evidence
            ),
            "public": _candidate_publicly_covers_expected_evidence(
                item=item, expected_evidence=expected_evidence
            ),
        }
    rankings: dict[str, list[dict[str, Any]]] = {}
    for method, scores in score_methods.items():
        ordered_ids = sorted(
            scores,
            key=lambda chunk_id: (-scores[chunk_id], distances[chunk_id], chunk_id),
        )
        rankings[method] = [
            {
                "chunk_id": chunk_id,
                "coverage": {"LUSHAN-01": coverage_by_chunk[chunk_id]},
            }
            for chunk_id in ordered_ids
        ]
    summary = _summarize_dual_rankings(rankings)
    summary.update(
        {
            "latency_ms": {
                "original_dense": round(original_dense_ms, 3),
                "supplementary_dense": round(supplementary_dense_ms, 3),
                "original_rerank": round(original_rerank_ms, 3),
                "supplementary_rerank": round(supplementary_rerank_ms, 3),
                "fusion_postprocess": round((perf_counter() - fusion_started) * 1000, 3),
            },
            "query_kind": "loan_amount_world_bank_vs_ibrd_ida",
            "original_dense_candidate_count": len(original_candidates),
            "supplementary_dense_candidate_count": len(supplementary_candidates),
            "union_candidate_count": len(candidates),
            "overlap_candidate_count": (
                len(original_dense_ranks.keys() & supplementary_dense_ranks.keys())
            ),
            "original_dense_target_rank": next(
                (
                    original_dense_ranks[item.chunk_id]
                    for _, item in original_candidates
                    if coverage_by_chunk[item.chunk_id]["exact"]
                ),
                None,
            ),
            "supplementary_dense_target_rank": next(
                (
                    supplementary_dense_ranks[item.chunk_id]
                    for _, item in supplementary_candidates
                    if coverage_by_chunk[item.chunk_id]["exact"]
                ),
                None,
            ),
        }
    )
    if capture_internal is not None:
        capture_internal.update(
            {
                "original_candidates": [item for _, item in original_candidates],
                "union_candidates": [item for _, item in candidates],
                "union_distances": distances,
                "original_scores": original_score_map,
                "supplementary_scores": supplementary_score_map,
            }
        )
    return summary


def assess_agent_turn(
    payload: dict[str, Any],
    *,
    expected_status: str,
    expected_fragments: tuple[str, ...],
    expected_filenames: tuple[str, ...],
) -> dict[str, Any]:
    """生成不含回答正文和引用摘录的 FR-042 单轮质量诊断。

    Args:
        payload: 消息端点返回的公开响应。
        expected_status: 当前案例期望状态。
        expected_fragments: 必须出现在答案中的事实片段。
        expected_filenames: 必须至少出现一次的引用文件名。
    """
    observed_status = payload.get("answer_status")
    answer = payload.get("answer")
    citations = payload.get("citations")
    if not isinstance(answer, str) or not isinstance(citations, list) or not all(
        isinstance(item, dict) for item in citations
    ):
        raise AcceptanceError("FR-042 回答或引用结构无效。")
    cited_filenames = {str(item.get("filename", "")) for item in citations}
    status_matched = observed_status == expected_status
    fragments_matched = all(fragment in answer for fragment in expected_fragments)
    citations_matched = set(expected_filenames).issubset(cited_filenames)
    refusal_citations_empty = (
        expected_status != "REFUSED_NO_EVIDENCE" or not citations
    )
    return {
        "observed_status": (
            observed_status
            if observed_status in {"ANSWERED", "REFUSED_NO_EVIDENCE"}
            else "INVALID"
        ),
        "status_matched": status_matched,
        "answer_fragments_matched": fragments_matched,
        "citations_matched": citations_matched,
        "refusal_citations_empty": refusal_citations_empty,
        "passed": (
            status_matched
            and fragments_matched
            and citations_matched
            and refusal_citations_empty
        ),
    }


def validate_agent_turn(
    payload: dict[str, Any],
    *,
    expected_status: str,
    expected_fragments: tuple[str, ...],
    expected_filenames: tuple[str, ...],
) -> None:
    """严格验证 FR-042 单轮公开状态、事实片段和引用文件。

    Args:
        payload: 消息端点返回的公开响应。
        expected_status: 当前案例期望状态。
        expected_fragments: 必须出现在答案中的事实片段。
        expected_filenames: 必须至少出现一次的引用文件名。
    """
    assessment = assess_agent_turn(
        payload,
        expected_status=expected_status,
        expected_fragments=expected_fragments,
        expected_filenames=expected_filenames,
    )
    if not assessment["status_matched"]:
        raise AcceptanceError("FR-042 回答状态不符合 P153548 验收预期。")
    if not assessment["answer_fragments_matched"]:
        raise AcceptanceError("FR-042 回答缺少 P153548 预期事实。")
    if not assessment["citations_matched"]:
        raise AcceptanceError("FR-042 回答缺少 P153548 预期引用。")
    if not assessment["refusal_citations_empty"]:
        raise AcceptanceError("FR-042 无据拒答不得返回引用。")


def _validate_question_answer(
    payload: dict[str, Any],
    *,
    expected_status: str,
    expected_fragments: tuple[str, ...],
    expected_filenames: tuple[str, ...],
) -> None:
    """复用 Agent 校验规则验证 FR-039 的公开响应。

    Args:
        payload: FR-039 问答响应。
        expected_status: 期望回答状态。
        expected_fragments: 必须出现的事实片段。
        expected_filenames: 必须引用的来源文件名。
    """
    validate_agent_turn(
        payload,
        expected_status=expected_status,
        expected_fragments=expected_fragments,
        expected_filenames=expected_filenames,
    )


def _question(entry: dict[str, Any], turn: int = 0) -> str:
    """读取固定集指定轮次的问题文本。

    Args:
        entry: 单个数据集条目。
        turn: 从零开始的轮次下标。
    """
    try:
        value = entry["input_data"]["messages"][turn]
    except (KeyError, IndexError, TypeError) as exc:
        raise AcceptanceError("P153548 案例缺少问题文本。") from exc
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceError("P153548 案例问题文本无效。")
    return value


def _postgres_row_count() -> int:
    """统计当前 PostgreSQL Schema 全部非 Alembic 表的记录数。"""
    with engine.connect() as connection:
        schema = str(connection.execute(text("SELECT current_schema()")).scalar_one())
        if not schema or schema in {"None", "public"}:
            raise AcceptanceError("隔离验收数据库没有有效的当前 Schema。")
        table_names = inspect(connection).get_table_names(schema=schema)
        quoted_schema = connection.dialect.identifier_preparer.quote(schema)
        return sum(
            int(
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {quoted_schema}."
                        f"{connection.dialect.identifier_preparer.quote(table)}"
                    )
                ).scalar_one()
            )
            for table in table_names
            if table not in _ALEMBIC_TABLES
        )


def _existing_final_collection_count() -> int:
    """只读取已存在的 Final Collection；不存在时不创建 Collection。"""
    try:
        collection = get_chroma_client().get_collection(
            name=settings.chroma_final_collection
        )
    except Exception as exc:
        # Chroma 未找到资源时返回空计数；其他连接错误必须中止，不能掩盖环境故障。
        from chromadb.errors import NotFoundError

        if isinstance(exc, NotFoundError):
            return 0
        raise AcceptanceError("隔离 Chroma Collection 无法只读检查。") from exc
    return int(collection.count())


def _validate_isolation_configuration(
    *,
    embedding_context_mode: str,
    chroma_tenant: str,
    chroma_database: str,
    chroma_collection: str,
    file_storage_path: Path,
    checkpoint_path: Path,
) -> None:
    """拒绝默认共享命名空间/持久目录，并要求 evidence_values 索引。

    Args:
        embedding_context_mode: 当前已索引 Final Chunk 使用的上下文模式。
        chroma_tenant: 本次验收配置的 Chroma Tenant。
        chroma_database: 本次验收配置的 Chroma Database。
        chroma_collection: 本次验收配置的 Final Collection。
        file_storage_path: 当前上传文件根目录。
        checkpoint_path: 当前 LangGraph Checkpoint 文件。
    """
    default_file_root = (PROJECT_ROOT / "data" / "files").resolve()
    default_checkpoint = (PROJECT_ROOT / "data" / "agent_checkpoints.db").resolve()
    normalized_file_root = Path(file_storage_path).resolve()
    normalized_checkpoint = Path(checkpoint_path).resolve()
    if embedding_context_mode != "evidence_values":
        raise AcceptanceError("P153548 双路诊断要求 evidence_values 索引上下文。")
    if (
        chroma_database == "mini_rag_chroma"
        or chroma_collection == "archive_final_chunks"
    ):
        raise AcceptanceError("隔离验收必须配置非默认且专用的 Chroma 命名空间。")
    if (
        normalized_file_root == default_file_root
        or normalized_checkpoint == default_checkpoint
        or normalized_file_root == normalized_checkpoint
        or normalized_file_root in normalized_checkpoint.parents
        or normalized_checkpoint in normalized_file_root.parents
    ):
        raise AcceptanceError("隔离验收必须配置独立于项目默认目录的文件与 Checkpoint 路径。")


def _assert_empty_isolated_targets() -> None:
    """要求目标 PostgreSQL Schema、Chroma Collection 和本地存储均为空。"""
    _validate_isolation_configuration(
        embedding_context_mode=settings.archive_embedding_context_mode,
        chroma_tenant=settings.chroma_tenant,
        chroma_database=settings.chroma_database,
        chroma_collection=settings.chroma_final_collection,
        file_storage_path=settings.file_storage_path,
        checkpoint_path=settings.agent_checkpoint_path,
    )
    if (
        _postgres_row_count() != 0
        or _existing_final_collection_count() != 0
        or _stored_file_count(settings.file_storage_path) != 0
        or _checkpoint_row_count(settings.agent_checkpoint_path) != 0
    ):
        raise AcceptanceError(
            "隔离验收目标并非空资源；请使用专用空 Schema、Chroma 命名空间和本地存储目录。"
        )


def _checkpoint_row_count(path: Path) -> int:
    """统计 Checkpoint SQLite 的业务状态行，不包含迁移元数据。

    Args:
        path: 隔离 Checkpoint 文件路径。
    """
    if not path.is_file():
        return 0
    connection = sqlite3.connect(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        return sum(
            int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in ("checkpoints", "writes", "blobs")
            if table in tables
        )
    finally:
        connection.close()


def _stored_file_count(root: Path) -> int:
    """统计隔离文件根目录中的普通文件。

    Args:
        root: 当前进程配置的上传文件根目录。
    """
    return sum(path.is_file() for path in root.rglob("*")) if root.exists() else 0


def wrap_stage_error(stage: str, error: BaseException) -> AcceptanceError:
    """把内部异常投影为不含路径、资源标识或正文的阶段诊断。

    Args:
        stage: 失败时正在执行的验收阶段。
        error: 原始内部异常。

    Returns:
        仅保留稳定阶段和公开错误码的验收异常。
    """
    if isinstance(error, AcceptanceError):
        return AcceptanceError(
            f"P153548 验收阶段失败：{stage}。",
            safe_code=error.safe_code,
            http_status=error.http_status,
            api_code=error.api_code,
        )
    return AcceptanceError(
        f"P153548 验收阶段失败：{stage}。",
        safe_code="PERSISTENCE_ACCEPTANCE_ERROR",
    )


def _write_result(path: Path | None, value: dict[str, Any]) -> None:
    """原子写入不含资源标识和正文的聚合结果。

    Args:
        path: 可选输出路径。
        value: 已通过清理门的安全聚合结果。
    """
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _request_list(api: P14Api, method: str, path: str) -> list[dict[str, Any]]:
    """调用返回 JSON 数组的验收接口。

    Args:
        api: 已认证的本地验收客户端。
        method: HTTP 方法。
        path: 相对 API 路径。

    Returns:
        仅包含对象元素的 JSON 数组。
    """
    response = api._client.request(method, path, headers=api.headers)  # noqa: SLF001
    if response.status_code != 200:
        raise AcceptanceError(
            f"{method} {path} 返回 HTTP {response.status_code}，不符合验收预期。",
            http_status=response.status_code,
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise AcceptanceError(f"{method} {path} 未返回有效 JSON。") from exc
    if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
        raise AcceptanceError(f"{method} {path} 未返回对象数组。")
    return body


def _validate_pre_cleanup_evidence(
    *,
    context_counts: list[int],
    chroma_count: int,
    file_count: int,
    checkpoint_count: int,
    fusion_answer_comparison: bool,
) -> None:
    """按运行模式验证索引持久化证据及 Checkpoint 预期。

    Args:
        context_counts: 四份原始 PDF 写入的证据上下文数量。
        chroma_count: 隔离 Collection 中的向量数。
        file_count: 临时上传文件数。
        checkpoint_count: 独立 Checkpoint 中的状态行数。
        fusion_answer_comparison: 是否跳过 FR-042 会话和模型轮次。
    """
    common_evidence_valid = (
        len(context_counts) == 4
        and all(count > 0 for count in context_counts)
        and chroma_count > 0
        and file_count >= 4
    )
    checkpoint_valid = (
        checkpoint_count == 0 if fusion_answer_comparison else checkpoint_count > 0
    )
    if not common_evidence_valid or not checkpoint_valid:
        expectation = "必须保持为空" if fusion_answer_comparison else "必须有写入"
        raise AcceptanceError(f"P153548 跨存储写入证据不完整，Checkpoint {expectation}。")


def _run_legacy_answer_checks(
    *, api: P14Api, project_id: str, entries: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """执行原持久层验收中的 FR-039 与 FR-042 模型轮次。

    Args:
        api: 已认证的隔离验收客户端。
        project_id: 当前隔离项目标识。
        entries: 按固定集案例编号索引的数据。

    Returns:
        不含回答正文和引用摘录的旧版回答质量摘要。
    """
    fr039_grounded = _require_object(
        api.request(
            "POST",
            f"/projects/{project_id}/archive-questions",
            expected_statuses=(200,),
            payload={"question": _question(entries["LUSHAN-01"])},
        ),
        step="P153548 FR-039 有据问答",
    )
    fr039_grounded_assessment = assess_agent_turn(
        fr039_grounded,
        expected_status="ANSWERED",
        expected_fragments=("US$300 million",),
        expected_filenames=("2016_project_appraisal_document.pdf",),
    )
    fr039_refusal = _require_object(
        api.request(
            "POST",
            f"/projects/{project_id}/archive-questions",
            expected_statuses=(200,),
            payload={"question": _question(entries["LUSHAN-04"])},
        ),
        step="P153548 FR-039 无据问答",
    )
    fr039_refusal_assessment = assess_agent_turn(
        fr039_refusal,
        expected_status="REFUSED_NO_EVIDENCE",
        expected_fragments=(),
        expected_filenames=(),
    )

    created_session = _require_object(
        api.request(
            "POST",
            f"/projects/{project_id}/agent-sessions",
            expected_statuses=(201,),
            payload={},
        ),
        step="P153548 FR-042 会话创建",
    )
    session_id = str(created_session.get("id", ""))
    if not session_id:
        raise AcceptanceError("FR-042 会话创建未返回标识。")
    message_path = f"/projects/{project_id}/agent-sessions/{session_id}/messages"
    assessments: list[dict[str, Any]] = []
    turns = (
        ("LUSHAN-05", 0, "ANSWERED", ("Highly Satisfactory",), ("2024_completion_report.pdf",)),
        ("LUSHAN-05", 1, "ANSWERED", ("Satisfactory",), ("2024_completion_report_review.pdf",)),
        ("LUSHAN-04", 0, "REFUSED_NO_EVIDENCE", (), ()),
    )
    for index, (case_id, turn, status, fragments, filenames) in enumerate(turns, start=1):
        payload = _require_object(
            api.request(
                "POST",
                message_path,
                expected_statuses=(200,),
                payload={"message": _question(entries[case_id], turn)},
            ),
            step=f"P153548 FR-042 第 {index} 轮",
        )
        assessments.append(
            assess_agent_turn(
                payload,
                expected_status=status,
                expected_fragments=fragments,
                expected_filenames=filenames,
            )
        )
    history = _request_list(api, "GET", message_path)
    tool_calls = _request_list(
        api, "GET", f"/projects/{project_id}/agent-sessions/{session_id}/tool-calls"
    )
    if len(history) != 6:
        raise AcceptanceError("FR-042 历史未形成三轮完整用户/助手消息。")
    if not tool_calls:
        raise AcceptanceError("FR-042 未保存脱敏工具调用记录。")

    grounded_passed = bool(fr039_grounded_assessment["passed"])
    refusal_passed = bool(fr039_refusal_assessment["passed"])
    return {
        "fr039_grounded_passed": grounded_passed,
        "fr039_grounded_assessment": fr039_grounded_assessment,
        "fr039_refusal_passed": refusal_passed,
        "fr039_refusal_assessment": fr039_refusal_assessment,
        "fr042_turn_count": len(assessments),
        "fr042_turn_passed": [bool(item["passed"]) for item in assessments],
        "fr042_turn_assessments": assessments,
        "fr042_history_message_count": len(history),
        "fr042_tool_call_count": len(tool_calls),
        "answer_quality_passed": (
            grounded_passed
            and refusal_passed
            and all(item["passed"] for item in assessments)
        ),
    }


def _confirm_fusion_preflight(preflight: dict[str, Any]) -> bool:
    """展示不含 Prompt/正文的调用预检，并要求确认公开资料外发。

    Args:
        preflight: 每组候选数、提示字符数和预计调用数。
    """
    print(
        json.dumps(
            {
                "fusion_answer_preflight": preflight,
                "maximum_model_calls": _MODEL_CALL_LIMIT,
                "external_data_notice": "将把固定原问题和所选公开 PDF 候选发送给配置的 DeepSeek 模型。",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return input("确认继续该模型调用对照？输入 yes：").strip().casefold() == "yes"


def run_lushan_persistence_acceptance(
    *,
    base_url: str,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    result_file: Path | None = None,
    fusion_answer_comparison: bool = False,
    confirm_fusion_preflight: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """执行 P153548 真实持久层、检索、问答与 Agent 闭环。

    Args:
        base_url: 已使用隔离配置启动的 FastAPI 地址。
        corpus_root: 保存公开 PDF 与 manifest 的本地目录。
        result_file: 可选的安全聚合结果文件。
        fusion_answer_comparison: 显式启用五组回答引用对照并跳过旧模型轮次。
        confirm_fusion_preflight: 可选的脱敏预检确认回调，默认由 CLI 提示。

    Returns:
        不含 UUID、原文、分数或 Token 的验收聚合。
    """
    # 在任何注册、上传或索引写入前检查资源；该检查不会创建 Chroma Collection。
    _assert_empty_isolated_targets()
    dataset = _read_json(DATASET_PATH, label="P153548 数据集")
    manifest = _read_json(corpus_root / "metadata" / "manifest.json", label="P153548 来源清单")
    document_label = build_document_label(dataset, manifest)
    entries = _entry_map(dataset)
    api = P14Api(base_url)
    user_id: UUID | None = None
    project_ids: dict[str, str] = {}
    seeded: list[tuple[str, str]] = []
    stage = "registration"
    primary_error: BaseException | None = None
    result: dict[str, Any] | None = None
    try:
        user_id, _ = _register_and_login(api, run_tag="lushan-p153548")
        stage = "seed_confirmation"
        _, context_counts = _seed_confirmed_documents(
            api,
            document_label=document_label,
            run_tag="lushan-p153548",
            project_ids=project_ids,
            seeded=seeded,
            evaluation_root=corpus_root,
        )
        project_id = project_ids["lushan"]

        stage = "retrieval"
        retrieval_responses: dict[str, dict[str, Any]] = {}
        retrieval_diagnostics: list[dict[str, Any]] = []
        for case_id in ("LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04"):
            question = _question(entries[case_id])
            retrieval_responses[case_id] = _require_object(
                api.request(
                    "POST",
                    f"/projects/{project_id}/archive-retrieval",
                    expected_statuses=(200,),
                    payload={"query": question, "top_k": 8},
                ),
                step="P153548 正式检索",
            )
            if entries[case_id]["eval_metadata"]["category"] != "NO_EVIDENCE":
                diagnostic_response = _require_object(
                    api.request(
                        "POST",
                        f"/projects/{project_id}/archive-retrieval-diagnostic",
                        expected_statuses=(200,),
                        payload={
                            "query": question,
                            "expected_evidence": _diagnostic_expected_evidence(
                                entries[case_id]
                            ),
                        },
                    ),
                    step="P153548 Top-30 双排序诊断",
                )
                retrieval_diagnostics.append(
                    summarize_retrieval_diagnostic(case_id, diagnostic_response)
                )
        retrieval_summary = summarize_retrieval_cases(dataset, retrieval_responses)
        stage = "dual_retrieval_diagnostic"
        dual_internal: dict[str, Any] | None = {} if fusion_answer_comparison else None
        with Session(engine) as session:
            dual_retrieval_summary = _dual_retrieval_diagnostic(
                session=session,
                user_id=user_id,
                project_id=UUID(project_id),
                query=_question(entries["LUSHAN-01"]),
                expected_evidence=_diagnostic_expected_evidence(entries["LUSHAN-01"]),
                capture_internal=dual_internal,
            )

        fusion_summary: dict[str, Any] | None = None
        legacy_answer_summary: dict[str, Any] = {}
        if fusion_answer_comparison:
            if dual_internal is None:
                raise AcceptanceError("融合回答对照缺少请求内候选数据。")
            stage = "fusion_answer_preflight"
            with Session(engine) as session:
                project = session.get(Project, UUID(project_id))
                if project is None or project.owner_id != user_id:
                    raise AcceptanceError("融合回答对照项目不属于当前验收用户。")
                baseline = retrieve_archive_answer_candidates(
                    user_id=user_id,
                    project_id=UUID(project_id),
                    kb_id=project.kb_id,
                    query=_question(entries["LUSHAN-01"]),
                    session=session,
                    observe_result=False,
                )
            original_candidates = dual_internal["original_candidates"]
            union_candidates = dual_internal["union_candidates"]
            groups = _build_fusion_answer_groups(
                original_candidates=[(0.0, item) for item in original_candidates],
                union_candidates=[
                    (dual_internal["union_distances"][item.chunk_id], item)
                    for item in union_candidates
                ],
                original_scores=dual_internal["original_scores"],
                supplementary_scores=dual_internal["supplementary_scores"],
                baseline_chunk_ids=[item.chunk_id for item in baseline.items],
            )
            stage = "fusion_answer_comparison"
            fusion_summary = _run_fusion_answer_trials(
                query=_question(entries["LUSHAN-01"]),
                groups=groups,
                expected_evidence=_diagnostic_expected_evidence(entries["LUSHAN-01"]),
                confirm_preflight=(confirm_fusion_preflight or _confirm_fusion_preflight),
            )
        else:
            stage = "fr039_fr042"
            legacy_answer_summary = _run_legacy_answer_checks(
                api=api, project_id=project_id, entries=entries
            )

        stage = "pre_cleanup_evidence"
        chroma_count = _existing_final_collection_count()
        file_count = _stored_file_count(settings.file_storage_path)
        checkpoint_count = _checkpoint_row_count(settings.agent_checkpoint_path)
        _validate_pre_cleanup_evidence(
            context_counts=context_counts,
            chroma_count=chroma_count,
            file_count=file_count,
            checkpoint_count=checkpoint_count,
            fusion_answer_comparison=fusion_answer_comparison,
        )
        retrieval_quality_passed = bool(retrieval_summary.pop("quality_passed"))
        result = {
            **retrieval_summary,
            "retrieval_diagnostics": retrieval_diagnostics,
            "dual_retrieval_diagnostic": dual_retrieval_summary,
            "document_count": len(context_counts),
            "all_documents_indexed": all(count > 0 for count in context_counts),
            "retrieval_quality_passed": retrieval_quality_passed,
            "chroma_had_chunks": chroma_count > 0,
            "files_written": file_count >= 4,
            "checkpoint_written": checkpoint_count > 0,
            **legacy_answer_summary,
            "quality_passed": (
                None
                if fusion_answer_comparison
                else retrieval_quality_passed
                and bool(legacy_answer_summary["answer_quality_passed"])
            ),
        }
        if fusion_answer_comparison:
            result["fusion_answer_comparison"] = fusion_summary
        else:
            result.pop("answer_quality_passed", None)
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
            except BaseException as exc:
                cleanup_error = exc
        api.close()
        try:
            post_cleanup = {
                "postgres_zero": _postgres_row_count() == 0,
                "chroma_zero": _existing_final_collection_count() == 0,
                "files_zero": _stored_file_count(settings.file_storage_path) == 0,
                "checkpoint_zero": _checkpoint_row_count(settings.agent_checkpoint_path) == 0,
            }
            if not all(post_cleanup.values()):
                raise AcceptanceError("P153548 隔离持久层清理后仍有残留。")
            if result is not None:
                result.update(post_cleanup)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise AcceptanceError("P153548 验收清理未全部完成。") from cleanup_error
    if primary_error is not None:
        raise wrap_stage_error(stage, primary_error) from primary_error
    if result is None:
        raise AcceptanceError(f"P153548 验收未产生结果，停止阶段：{stage}。")
    _write_result(result_file, result)
    return result


def main() -> int:
    """运行命令行验收并只输出安全聚合。"""
    parser = argparse.ArgumentParser(description="运行芦山 P153548 隔离持久层真实验收。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument(
        "--fusion-answer-comparison",
        action="store_true",
        help="运行 A-E 五组融合候选回答引用对照，并跳过旧 FR-039/FR-042 模型轮次。",
    )
    args = parser.parse_args()
    try:
        result = run_lushan_persistence_acceptance(
            base_url=str(args.base_url),
            corpus_root=args.corpus_root,
            result_file=args.result_file,
            fusion_answer_comparison=args.fusion_answer_comparison,
        )
    except (AcceptanceError, ValueError, httpx.HTTPError) as exc:
        diagnostic = {
            "outcome": "failed",
            "error_type": type(exc).__name__,
            "message": str(exc) if isinstance(exc, AcceptanceError) else "P153548 验收输入无效。",
        }
        if isinstance(exc, AcceptanceError):
            diagnostic.update(
                {
                    "safe_code": exc.safe_code,
                    "http_status": exc.http_status,
                    "api_code": exc.api_code,
                }
            )
        print(json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
