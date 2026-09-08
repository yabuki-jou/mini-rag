"""对脱敏 D4 快照执行离线 Reranker 阈值可行性扫描。"""

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4


_QUESTION_KEYS = {"case_id", "category", "candidates"}
_CANDIDATE_KEYS = {
    "candidate_key",
    "reranker_rank",
    "reranker_score",
    "public_coverage_match",
    "strict_match",
    "isolation_violation",
    "candidate_kind",
}
_CATEGORIES = {"GROUNDED", "NO_EVIDENCE", "ISOLATION"}
_CATEGORY_COUNTS = {"GROUNDED": 8, "NO_EVIDENCE": 2, "ISOLATION": 2}
_CANDIDATE_KINDS = {"SAME_DOCUMENT", "OTHER_DOCUMENT", "UNKNOWN"}
_BOOLEAN_LABELS = (
    "public_coverage_match",
    "strict_match",
    "isolation_violation",
)


def _candidate_fingerprint(questions: list[dict[str, Any]]) -> str:
    """计算不受模型分数、排名和输入顺序影响的候选身份指纹。

    参数:
        questions: 已完成安全字段投影的固定问题列表。
    """
    identity = [
        {
            "case_id": question["case_id"],
            "category": question["category"],
            "candidate_keys": sorted(
                candidate["candidate_key"] for candidate in question["candidates"]
            ),
        }
        for question in sorted(questions, key=lambda item: item["case_id"])
    ]
    canonical_json = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def build_safe_snapshot(outcomes: object) -> dict[str, Any]:
    """从验收运行器内存结果构造 D4-A 脱敏 Top-30 快照。

    参数:
        outcomes: 固定题的内存结果；允许包含运行所需敏感字段，但只投影白名单字段。

    返回:
        已通过 D4 扫描器契约校验的脱敏快照。

    异常:
        ValueError: 固定题、候选池或候选安全字段不满足 D4-A 契约。
    """
    if not isinstance(outcomes, list) or len(outcomes) != 12:
        raise ValueError("D4-A 固定题数量无效。")

    questions: list[dict[str, Any]] = []
    required_candidate_keys = {
        "candidate_key",
        "reranker_rank",
        "reranker_score",
        "public_coverage_match",
        "matches_expected_evidence",
        "isolation_violation",
        "candidate_kind",
    }
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise ValueError("D4-A 问题结果结构无效。")
        if (
            outcome.get("diagnostic_candidate_count") != 30
            or isinstance(outcome.get("diagnostic_candidate_count"), bool)
            or outcome.get("diagnostic_chroma_candidate_count") != 30
            or isinstance(outcome.get("diagnostic_chroma_candidate_count"), bool)
        ):
            raise ValueError("D4-A Top-30 候选池不完整。")
        raw_candidates = outcome.get("internal_candidates")
        if not isinstance(raw_candidates, list) or len(raw_candidates) != 30:
            raise ValueError("D4-A Top-30 候选池不完整。")

        candidates: list[dict[str, object]] = []
        for candidate in raw_candidates:
            if not isinstance(candidate, dict) or not required_candidate_keys.issubset(
                candidate
            ):
                raise ValueError("D4-A 候选安全字段不完整。")
            # 只复制离线扫描必需字段，原文、路径、资源 ID 与查询不会进入快照。
            candidates.append(
                {
                    "candidate_key": candidate["candidate_key"],
                    "reranker_rank": candidate["reranker_rank"],
                    "reranker_score": candidate["reranker_score"],
                    "public_coverage_match": candidate["public_coverage_match"],
                    "strict_match": candidate["matches_expected_evidence"],
                    "isolation_violation": candidate["isolation_violation"],
                    "candidate_kind": candidate["candidate_kind"],
                }
            )
        questions.append(
            {
                "case_id": outcome.get("case_id"),
                "category": outcome.get("category"),
                "candidates": candidates,
            }
        )

    # 先用占位指纹执行完整类型校验，避免无效排名或标识在排序时泄漏底层异常。
    validate_snapshot({"candidate_fingerprint": "0" * 64, "questions": questions})
    for question in questions:
        question["candidates"].sort(
            key=lambda candidate: candidate["reranker_rank"]
        )
    questions.sort(key=lambda question: str(question["case_id"]))
    snapshot: dict[str, Any] = {
        "candidate_fingerprint": _candidate_fingerprint(questions),
        "questions": questions,
    }
    return validate_snapshot(snapshot)


def write_safe_snapshot(path: Path, snapshot: object) -> None:
    """校验并原子写出只含 D4 白名单字段的快照。

    参数:
        path: 调用方指定的快照文件路径。
        snapshot: 由 ``build_safe_snapshot`` 构造的脱敏快照。

    异常:
        ValueError: 快照不满足脱敏契约。
        OSError: 目录创建、临时写入或原子替换失败。
    """
    validated = validate_snapshot(snapshot)
    serialized = json.dumps(
        validated,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(serialized + "\n", encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _is_lower_hex(value: object, *, length: int) -> bool:
    """判断值是否为固定长度的小写十六进制字符串。

    参数:
        value: 待检查的值。
        length: 要求的字符串长度。
    """
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_finite_number(value: object) -> bool:
    """判断值是否为可安全转换和扫描的有限数字。

    参数:
        value: 待检查的分数或阈值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_snapshot(snapshot: object) -> dict[str, Any]:
    """校验 D4 快照的结构、固定集数量、数据类型和安全字段。

    参数:
        snapshot: 待校验的反序列化 JSON 值。

    返回:
        已通过校验的快照字典。

    异常:
        ValueError: 快照不满足固定集或脱敏契约。
    """
    if not isinstance(snapshot, dict):
        raise ValueError("快照顶层结构无效。")
    if set(snapshot) != {"candidate_fingerprint", "questions"}:
        raise ValueError("快照顶层字段无效。")
    if not _is_lower_hex(snapshot["candidate_fingerprint"], length=64):
        raise ValueError("候选指纹无效。")

    questions = snapshot["questions"]
    if not isinstance(questions, list) or len(questions) != 12:
        raise ValueError("固定题数无效。")
    if any(not isinstance(question, dict) for question in questions):
        raise ValueError("问题结构无效。")

    categories = [question.get("category") for question in questions]
    if any(
        not isinstance(category, str) or category not in _CATEGORIES
        for category in categories
    ):
        raise ValueError("固定题类别无效。")
    if any(categories.count(category) != count for category, count in _CATEGORY_COUNTS.items()):
        raise ValueError("固定题类别无效。")

    case_ids: set[str] = set()
    for question in questions:
        if set(question) != _QUESTION_KEYS:
            raise ValueError("问题字段无效。")
        case_id = question["case_id"]
        if (
            not isinstance(case_id, str)
            or not case_id.strip()
            or case_id in case_ids
        ):
            raise ValueError("问题标识无效。")
        case_ids.add(case_id)

        candidates = question["candidates"]
        if not isinstance(candidates, list) or len(candidates) != 30:
            raise ValueError("候选数量无效。")

        ranks: list[int] = []
        candidate_keys: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("候选结构无效。")
            if set(candidate) != _CANDIDATE_KEYS:
                raise ValueError("候选安全字段无效。")

            candidate_key = candidate["candidate_key"]
            if (
                not _is_lower_hex(candidate_key, length=64)
                or candidate_key in candidate_keys
            ):
                raise ValueError("候选标识无效。")
            candidate_keys.add(candidate_key)

            rank = candidate["reranker_rank"]
            if isinstance(rank, bool) or not isinstance(rank, int):
                raise ValueError("候选排名无效。")
            ranks.append(rank)

            score = candidate["reranker_score"]
            if not _is_finite_number(score):
                raise ValueError("候选分数无效。")
            if any(not isinstance(candidate[key], bool) for key in _BOOLEAN_LABELS):
                raise ValueError("候选布尔标记无效。")
            candidate_kind = candidate["candidate_kind"]
            if (
                not isinstance(candidate_kind, str)
                or candidate_kind not in _CANDIDATE_KINDS
            ):
                raise ValueError("候选类型无效。")

        if sorted(ranks) != list(range(1, 31)):
            raise ValueError("候选排名集合无效。")
        ranked_candidates = sorted(
            candidates,
            key=lambda candidate: candidate["reranker_rank"],
        )
        if any(
            previous["reranker_score"] < current["reranker_score"]
            for previous, current in zip(
                ranked_candidates,
                ranked_candidates[1:],
            )
        ):
            raise ValueError("候选排名与分数不一致。")

    return snapshot


def _score_validated_snapshot(
    snapshot: dict[str, Any], threshold: float | None
) -> dict[str, int | bool]:
    """对已校验快照应用阈值和公开 Top-10 规则。

    参数:
        snapshot: 已通过 D4 契约校验的快照。
        threshold: 统一分数阈值；None 表示不设阈值。
    """
    selected_groups = []
    for question in snapshot["questions"]:
        selected = [
            candidate
            for candidate in question["candidates"]
            if threshold is None or candidate["reranker_score"] >= threshold
        ]
        selected.sort(key=lambda candidate: candidate["reranker_rank"])
        selected_groups.append((question, selected[:10]))

    public_hits = sum(
        any(candidate["public_coverage_match"] for candidate in selected)
        for question, selected in selected_groups
        if question["category"] == "GROUNDED"
    )
    strict_hits = sum(
        any(candidate["strict_match"] for candidate in selected)
        for question, selected in selected_groups
        if question["category"] == "GROUNDED"
    )
    no_evidence_rejected = sum(
        not selected
        for question, selected in selected_groups
        if question["category"] == "NO_EVIDENCE"
    )
    isolation_passed = sum(
        not any(candidate["isolation_violation"] for candidate in selected)
        for question, selected in selected_groups
        if question["category"] == "ISOLATION"
    )
    return {
        "public_coverage_hits": public_hits,
        "strict_grounded_hits": strict_hits,
        "no_evidence_rejected": no_evidence_rejected,
        "isolation_passed": isolation_passed,
        "passed": (
            public_hits >= 7
            and no_evidence_rejected == 2
            and isolation_passed == 2
        ),
    }


def score_threshold(
    snapshot: object, threshold: float | None = None
) -> dict[str, int | bool]:
    """按统一阈值过滤候选，并计算 D4 的四类聚合指标。

    参数:
        snapshot: 满足 D4 脱敏契约的快照。
        threshold: 统一 Reranker 分数阈值；None 表示不设阈值。

    返回:
        公开命中、严格命中、无据拒答、隔离通过和总门结果。

    异常:
        ValueError: 快照或阈值无效。
    """
    validated = validate_snapshot(snapshot)
    if threshold is not None and not _is_finite_number(threshold):
        raise ValueError("阈值无效。")
    return _score_validated_snapshot(validated, threshold)


def _static_candidate_labels(question: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    """按脱敏候选键提取与模型无关的匹配标签。

    参数:
        question: 已通过校验的单题快照。
    """
    return {
        candidate["candidate_key"]: (
            candidate["public_coverage_match"],
            candidate["strict_match"],
            candidate["isolation_violation"],
            candidate["candidate_kind"],
        )
        for candidate in question["candidates"]
    }


def _same_candidates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """比较候选身份与静态标签，允许问题顺序、排名和分数变化。

    参数:
        left: 已校验的第一个模型快照。
        right: 已校验的第二个模型快照。
    """
    if left["candidate_fingerprint"] != right["candidate_fingerprint"]:
        return False
    left_questions = {question["case_id"]: question for question in left["questions"]}
    right_questions = {
        question["case_id"]: question for question in right["questions"]
    }
    if set(left_questions) != set(right_questions):
        return False
    for case_id, left_question in left_questions.items():
        right_question = right_questions[case_id]
        if left_question["category"] != right_question["category"]:
            return False
        if _static_candidate_labels(left_question) != _static_candidate_labels(
            right_question
        ):
            return False
    return True


def _threshold_candidates(snapshot: dict[str, Any]) -> list[float]:
    """从实际分数和紧邻上边界生成有限阈值候选。

    参数:
        snapshot: 已通过校验的单模型快照。
    """
    scores = {
        float(candidate["reranker_score"])
        for question in snapshot["questions"]
        for candidate in question["candidates"]
    }
    thresholds = set(scores)
    for score in scores:
        upper_boundary = math.nextafter(score, math.inf)
        if math.isfinite(upper_boundary):
            thresholds.add(upper_boundary)
    return sorted(thresholds)


def _scan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """扫描实际分数边界，并只返回脱敏聚合结果。

    参数:
        snapshot: 已通过校验的单模型快照。
    """
    feasible_thresholds = []
    for threshold in _threshold_candidates(snapshot):
        if _score_validated_snapshot(snapshot, threshold)["passed"]:
            feasible_thresholds.append(threshold)

    no_evidence_scores = [
        float(candidate["reranker_score"])
        for question in snapshot["questions"]
        if question["category"] == "NO_EVIDENCE"
        for candidate in question["candidates"]
    ]
    grounded_best_scores = []
    for question in snapshot["questions"]:
        if question["category"] != "GROUNDED":
            continue
        public_candidates = sorted(
            question["candidates"],
            key=lambda candidate: candidate["reranker_rank"],
        )[:10]
        matching_scores = [
            float(candidate["reranker_score"])
            for candidate in public_candidates
            if candidate["public_coverage_match"]
        ]
        if matching_scores:
            grounded_best_scores.append(max(matching_scores))
    grounded_best_scores.sort(reverse=True)

    isolation_violation_scores = [
        float(candidate["reranker_score"])
        for question in snapshot["questions"]
        if question["category"] == "ISOLATION"
        for candidate in sorted(
            question["candidates"],
            key=lambda item: item["reranker_rank"],
        )[:10]
        if candidate["isolation_violation"]
    ]

    required_grounded_score = (
        grounded_best_scores[6] if len(grounded_best_scores) >= 7 else None
    )
    max_no_evidence_score = max(no_evidence_scores)
    max_isolation_violation_score = (
        max(isolation_violation_scores) if isolation_violation_scores else None
    )
    max_quality_blocking_score = max(
        [max_no_evidence_score]
        + (
            [max_isolation_violation_score]
            if max_isolation_violation_score is not None
            else []
        )
    )
    suggested_threshold = (
        min(feasible_thresholds) if feasible_thresholds else None
    )
    return {
        "feasible": bool(feasible_thresholds),
        "feasible_threshold_count": len(feasible_thresholds),
        "feasible_threshold_min": suggested_threshold,
        "feasible_threshold_max": (
            max(feasible_thresholds) if feasible_thresholds else None
        ),
        "suggested_threshold": suggested_threshold,
        "suggested_threshold_metrics": (
            _score_validated_snapshot(snapshot, suggested_threshold)
            if suggested_threshold is not None
            else None
        ),
        "baseline_metrics": _score_validated_snapshot(snapshot, None),
        "max_no_evidence_score": max_no_evidence_score,
        "max_isolation_violation_score": max_isolation_violation_score,
        "max_quality_blocking_score": max_quality_blocking_score,
        "required_grounded_score_for_7_of_8": required_grounded_score,
        "separation_margin": (
            required_grounded_score - max_no_evidence_score
            if required_grounded_score is not None
            else None
        ),
        "quality_gate_margin": (
            required_grounded_score - max_quality_blocking_score
            if required_grounded_score is not None
            else None
        ),
    }


def analyze_snapshot(snapshot: object) -> dict[str, Any]:
    """分析单个 D4 快照并返回不含逐题和候选标识的安全聚合。

    参数:
        snapshot: 单个模型生成的 D4 脱敏候选快照。

    返回:
        阈值可行性、门槛边界与公开聚合指标。

    异常:
        ValueError: 快照不满足固定集或脱敏契约。
    """
    return _scan(validate_snapshot(snapshot))


def analyze(base: object, large: object) -> dict[str, Any]:
    """校验双模型快照的一致性并生成安全联合报告。

    参数:
        base: base Reranker 的 D4 脱敏快照。
        large: large Reranker 的 D4 脱敏快照。

    返回:
        只含候选一致性结论和双模型聚合指标的报告。

    异常:
        ValueError: 任一快照无效或候选身份、静态标签发生漂移。
    """
    validated_base = validate_snapshot(base)
    validated_large = validate_snapshot(large)
    if not _same_candidates(validated_base, validated_large):
        raise ValueError("候选快照不一致。")
    return {
        "candidate_snapshot_equal": True,
        "base": _scan(validated_base),
        "large": _scan(validated_large),
    }


def _build_parser() -> argparse.ArgumentParser:
    """创建 D4 离线诊断命令行参数解析器。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--base-snapshot", type=Path)
    parser.add_argument("--large-snapshot", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    """读取单模型或双模型快照并写出脱敏报告。

    返回:
        成功返回 0；输入、校验或写出失败返回 2。
    """
    args = _build_parser().parse_args()
    single_mode = args.snapshot is not None
    dual_mode = args.base_snapshot is not None and args.large_snapshot is not None
    dual_mode_incomplete = (args.base_snapshot is None) != (
        args.large_snapshot is None
    )
    if (
        dual_mode_incomplete
        or single_mode == dual_mode
        or (
            single_mode
            and (
                args.base_snapshot is not None
                or args.large_snapshot is not None
            )
        )
    ):
        print("P14-D4 输入或执行失败。")
        return 2

    temporary_output = args.output.with_name(f".{args.output.name}.tmp")
    if single_mode:
        selected_inputs = [args.snapshot]
    else:
        selected_inputs = [args.base_snapshot, args.large_snapshot]
    input_paths = {path.resolve() for path in selected_inputs}
    if (
        args.output.resolve() in input_paths
        or temporary_output.resolve() in input_paths
    ):
        print("P14-D4 输入或执行失败。")
        return 2
    try:
        # 每次运行先移除指定的旧报告，避免失败后误读上一轮成功结果。
        args.output.unlink(missing_ok=True)
        temporary_output.unlink(missing_ok=True)
        if single_mode:
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            report = analyze_snapshot(snapshot)
        else:
            base = json.loads(args.base_snapshot.read_text(encoding="utf-8"))
            large = json.loads(args.large_snapshot.read_text(encoding="utf-8"))
            report = analyze(base, large)
        serialized = json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output.write_text(serialized + "\n", encoding="utf-8")
        temporary_output.replace(args.output)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        args.output.unlink(missing_ok=True)
        print("P14-D4 输入或执行失败。")
        return 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
