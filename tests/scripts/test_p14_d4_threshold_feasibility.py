import json
import math
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.p14_d4_threshold_feasibility import (
    analyze,
    analyze_snapshot,
    build_safe_snapshot,
    main,
    score_threshold,
    validate_snapshot,
    write_safe_snapshot,
)


def make_snapshot(*, public_matches: int = 8) -> dict:
    """构造满足 D4 脱敏契约的固定 12 题、每题 30 候选快照。"""
    categories = ["GROUNDED"] * 8 + ["NO_EVIDENCE"] * 2 + ["ISOLATION"] * 2
    questions = []
    for question_index, category in enumerate(categories):
        candidates = []
        for rank in range(1, 31):
            if category == "GROUNDED":
                score = 0.9 - question_index * 0.001 - rank * 0.0001
            elif category == "NO_EVIDENCE":
                score = 0.2 - rank * 0.001
            else:
                score = 0.1 - rank * 0.001
            candidates.append(
                {
                    "candidate_key": f"{question_index:02x}{rank:062x}",
                    "reranker_rank": rank,
                    "reranker_score": score,
                    "public_coverage_match": (
                        category == "GROUNDED"
                        and rank == 1
                        and question_index < public_matches
                    ),
                    "strict_match": (
                        category == "GROUNDED" and rank == 1 and question_index < 6
                    ),
                    "isolation_violation": False,
                    "candidate_kind": "SAME_DOCUMENT",
                }
            )
        questions.append(
            {
                "case_id": f"CASE-{question_index:02}",
                "category": category,
                "candidates": candidates,
            }
        )
    return {"candidate_fingerprint": "a" * 64, "questions": questions}


def make_outcomes(*, public_matches: int = 8) -> list[dict]:
    """构造运行器内存中的固定 12 题 Top-30 原始结果。"""
    snapshot = make_snapshot(public_matches=public_matches)
    outcomes = []
    for question in snapshot["questions"]:
        candidates = []
        for candidate in question["candidates"]:
            candidates.append(
                {
                    "candidate_key": candidate["candidate_key"],
                    "reranker_rank": candidate["reranker_rank"],
                    "reranker_score": candidate["reranker_score"],
                    "public_coverage_match": candidate[
                        "public_coverage_match"
                    ],
                    "matches_expected_evidence": candidate["strict_match"],
                    "isolation_violation": candidate["isolation_violation"],
                    "candidate_kind": candidate["candidate_kind"],
                    "content": "不得持久化的候选正文",
                    "document_id": "不得持久化的文档标识",
                }
            )
        outcomes.append(
            {
                "case_id": question["case_id"],
                "category": question["category"],
                "diagnostic_candidate_count": 30,
                "diagnostic_chroma_candidate_count": 30,
                "internal_candidates": candidates,
                "question": "不得持久化的问题正文",
                "query": "不得持久化的查询",
                "token": "不得持久化的令牌",
                "path": "不得持久化的路径",
            }
        )
    return outcomes


def test_build_safe_snapshot_projects_complete_safe_structure() -> None:
    outcomes = make_outcomes()

    snapshot = build_safe_snapshot(outcomes)

    assert set(snapshot) == {"candidate_fingerprint", "questions"}
    assert len(snapshot["questions"]) == 12
    assert all(len(question["candidates"]) == 30 for question in snapshot["questions"])
    serialized = json.dumps(snapshot, ensure_ascii=False)
    for sensitive_value in (
        "不得持久化的候选正文",
        "不得持久化的文档标识",
        "不得持久化的问题正文",
        "不得持久化的查询",
        "不得持久化的令牌",
        "不得持久化的路径",
    ):
        assert sensitive_value not in serialized
    assert set(snapshot["questions"][0]["candidates"][0]) == {
        "candidate_key",
        "reranker_rank",
        "reranker_score",
        "public_coverage_match",
        "strict_match",
        "isolation_violation",
        "candidate_kind",
    }


def test_snapshot_fingerprint_ignores_rank_score_and_input_order() -> None:
    base_outcomes = make_outcomes()
    changed_outcomes = deepcopy(base_outcomes)
    changed_outcomes.reverse()
    for outcome in changed_outcomes:
        candidates = outcome["internal_candidates"]
        candidates.reverse()
        for rank, candidate in enumerate(candidates, start=1):
            candidate["reranker_rank"] = rank
            candidate["reranker_score"] = 1.0 - rank * 0.001

    base = build_safe_snapshot(base_outcomes)
    changed = build_safe_snapshot(changed_outcomes)

    assert changed["candidate_fingerprint"] == base["candidate_fingerprint"]


def test_snapshot_fingerprint_changes_when_candidate_identity_changes() -> None:
    base_outcomes = make_outcomes()
    changed_outcomes = deepcopy(base_outcomes)
    changed_outcomes[0]["internal_candidates"][0]["candidate_key"] = "f" * 64

    assert build_safe_snapshot(changed_outcomes)["candidate_fingerprint"] != (
        build_safe_snapshot(base_outcomes)["candidate_fingerprint"]
    )


def test_build_safe_snapshot_preserves_public_and_strict_difference() -> None:
    outcomes = make_outcomes()
    outcomes[0]["internal_candidates"][0]["public_coverage_match"] = True
    outcomes[0]["internal_candidates"][0]["matches_expected_evidence"] = False

    candidate = build_safe_snapshot(outcomes)["questions"][0]["candidates"][0]

    assert candidate["public_coverage_match"] is True
    assert candidate["strict_match"] is False


@pytest.mark.parametrize(
    ("count_field", "value"),
    [
        ("diagnostic_candidate_count", 29),
        ("diagnostic_chroma_candidate_count", 29),
        ("internal_candidates", []),
    ],
)
def test_build_safe_snapshot_rejects_incomplete_candidate_pool(
    count_field: str,
    value: object,
) -> None:
    outcomes = make_outcomes()
    outcomes[0][count_field] = value

    with pytest.raises(ValueError, match="Top-30 候选池不完整"):
        build_safe_snapshot(outcomes)


def test_write_safe_snapshot_atomically_writes_validated_payload(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "d4-a-snapshot.json"
    snapshot = build_safe_snapshot(make_outcomes())

    write_safe_snapshot(target, snapshot)

    assert json.loads(target.read_text(encoding="utf-8")) == snapshot
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_validate_snapshot_requires_exact_category_counts() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][7]["category"] = "NO_EVIDENCE"

    with pytest.raises(ValueError, match="固定题类别无效"):
        validate_snapshot(snapshot)


def test_validate_snapshot_rejects_sensitive_or_unknown_fields() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["candidates"][0]["content"] = "secret-text"

    with pytest.raises(ValueError, match="候选安全字段无效"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("field_path", "message"),
    [
        (("category",), "固定题类别无效"),
        (("candidates", 0, "candidate_kind"), "候选类型无效"),
    ],
)
def test_validate_snapshot_normalizes_unhashable_json_values(
    field_path: tuple,
    message: str,
) -> None:
    snapshot = make_snapshot()
    if field_path[0] == "category":
        snapshot["questions"][0]["category"] = []
    else:
        snapshot["questions"][0]["candidates"][0]["candidate_kind"] = []

    with pytest.raises(ValueError, match=message):
        validate_snapshot(snapshot)


def test_validate_snapshot_normalizes_bad_candidate_shape_to_value_error() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["candidates"][0] = "not-a-dict"

    with pytest.raises(ValueError, match="候选结构无效"):
        validate_snapshot(snapshot)


def test_validate_snapshot_rejects_duplicate_ranks() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["candidates"][1]["reranker_rank"] = 1

    with pytest.raises(ValueError, match="候选排名集合无效"):
        validate_snapshot(snapshot)


def test_validate_snapshot_requires_scores_to_follow_reranker_rank() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["candidates"][1]["reranker_score"] = 0.95

    with pytest.raises(ValueError, match="候选排名与分数不一致"):
        validate_snapshot(snapshot)


def test_extreme_integer_score_and_threshold_raise_value_error() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["candidates"][0]["reranker_score"] = 10**10000

    with pytest.raises(ValueError, match="候选分数无效"):
        validate_snapshot(snapshot)
    with pytest.raises(ValueError, match="阈值无效"):
        score_threshold(make_snapshot(), 10**10000)


def test_threshold_equal_score_keeps_candidate_and_nextafter_excludes_it() -> None:
    snapshot = make_snapshot()
    score = snapshot["questions"][0]["candidates"][0]["reranker_score"]

    equal_metrics = score_threshold(snapshot, score)
    above_metrics = score_threshold(snapshot, math.nextafter(score, math.inf))

    assert equal_metrics["public_coverage_hits"] == 1
    assert above_metrics["public_coverage_hits"] == 0


def test_score_threshold_applies_public_top_10_after_filtering() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["candidates"][0]["public_coverage_match"] = False
    snapshot["questions"][0]["candidates"][10]["public_coverage_match"] = True

    metrics = score_threshold(snapshot)

    assert metrics["public_coverage_hits"] == 7


def test_public_and_strict_grounded_semantics_are_independent() -> None:
    metrics = score_threshold(make_snapshot())

    assert metrics["public_coverage_hits"] == 8
    assert metrics["strict_grounded_hits"] == 6


def test_threshold_can_reject_both_no_evidence_questions() -> None:
    metrics = score_threshold(make_snapshot(), 0.5)

    assert metrics["no_evidence_rejected"] == 2


def test_isolation_violation_reduces_isolation_pass_count() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][10]["candidates"][0]["isolation_violation"] = True

    metrics = score_threshold(snapshot)

    assert metrics["isolation_passed"] == 1
    assert metrics["passed"] is False


def test_scan_reports_no_feasible_threshold() -> None:
    snapshot = make_snapshot(public_matches=6)

    result = analyze(snapshot, deepcopy(snapshot))["base"]

    assert result["feasible"] is False
    assert result["feasible_threshold_count"] == 0
    assert result["suggested_threshold"] is None


def test_scan_reports_exact_feasible_boundaries_and_selected_metrics() -> None:
    result = analyze(make_snapshot(), make_snapshot())["base"]
    expected_lower = math.nextafter(result["max_no_evidence_score"], math.inf)

    assert result["feasible"] is True
    assert result["feasible_threshold_min"] == expected_lower
    assert result["suggested_threshold"] == expected_lower
    assert result["feasible_threshold_max"] == pytest.approx(0.8939)
    assert result["suggested_threshold_metrics"]["passed"] is True


def test_equal_no_evidence_and_seventh_grounded_score_is_not_feasible() -> None:
    snapshot = make_snapshot()
    required = snapshot["questions"][6]["candidates"][0]["reranker_score"]
    snapshot["questions"][8]["candidates"][0]["reranker_score"] = required

    result = analyze(snapshot, deepcopy(snapshot))["base"]

    assert result["max_no_evidence_score"] == required
    assert result["required_grounded_score_for_7_of_8"] == required
    assert result["feasible"] is False


def test_candidate_comparison_allows_order_rank_and_score_changes() -> None:
    base = make_snapshot()
    large = deepcopy(base)
    large["questions"].reverse()
    for question in large["questions"]:
        question["candidates"].reverse()
        for candidate in question["candidates"]:
            candidate["reranker_rank"] = 31 - candidate["reranker_rank"]
        for candidate in question["candidates"]:
            candidate["reranker_score"] = 1.0 - candidate["reranker_rank"] * 0.001

    assert analyze(base, large)["candidate_snapshot_equal"] is True


def test_candidate_comparison_rejects_fingerprint_drift() -> None:
    base = make_snapshot()
    large = deepcopy(base)
    large["candidate_fingerprint"] = "b" * 64

    with pytest.raises(ValueError, match="候选快照不一致"):
        analyze(base, large)


def test_candidate_comparison_rejects_candidate_key_drift() -> None:
    base = make_snapshot()
    large = deepcopy(base)
    large["questions"][0]["candidates"][0]["candidate_key"] = "f" * 64

    with pytest.raises(ValueError, match="候选快照不一致"):
        analyze(base, large)


def test_candidate_comparison_rejects_static_label_drift() -> None:
    base = make_snapshot()
    large = deepcopy(base)
    large["questions"][0]["candidates"][0]["candidate_kind"] = "OTHER_DOCUMENT"

    with pytest.raises(ValueError, match="候选快照不一致"):
        analyze(base, large)


def test_scan_uses_none_when_fewer_than_seven_grounded_questions_match() -> None:
    snapshot = make_snapshot(public_matches=6)

    result = analyze(snapshot, deepcopy(snapshot))["base"]

    assert result["required_grounded_score_for_7_of_8"] is None
    assert result["separation_margin"] is None


def test_grounded_retention_score_ignores_match_outside_public_top_10() -> None:
    snapshot = make_snapshot(public_matches=7)
    snapshot["questions"][0]["candidates"][0]["public_coverage_match"] = False
    snapshot["questions"][0]["candidates"][10]["public_coverage_match"] = True

    result = analyze(snapshot, deepcopy(snapshot))["base"]

    assert result["required_grounded_score_for_7_of_8"] is None
    assert result["separation_margin"] is None


def test_quality_gate_margin_includes_visible_isolation_violation() -> None:
    snapshot = make_snapshot()
    isolation_candidate = snapshot["questions"][10]["candidates"][0]
    isolation_candidate["reranker_score"] = 0.95
    isolation_candidate["isolation_violation"] = True

    result = analyze(snapshot, deepcopy(snapshot))["base"]

    assert result["max_isolation_violation_score"] == pytest.approx(0.95)
    assert result["max_quality_blocking_score"] == pytest.approx(0.95)
    assert result["quality_gate_margin"] == pytest.approx(0.8939 - 0.95)
    assert result["feasible"] is False


def test_report_contains_only_safe_aggregates() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["case_id"] = "SENSITIVE_CASE_ID"
    candidate_key = snapshot["questions"][0]["candidates"][0]["candidate_key"]

    serialized = json.dumps(analyze(snapshot, deepcopy(snapshot)))

    assert "SENSITIVE_CASE_ID" not in serialized
    assert candidate_key not in serialized
    assert "case_id" not in serialized
    assert "candidate_key" not in serialized


def test_analyze_snapshot_returns_only_safe_aggregates() -> None:
    snapshot = make_snapshot()
    snapshot["questions"][0]["case_id"] = "SENSITIVE_CASE_ID"
    candidate_key = snapshot["questions"][0]["candidates"][0]["candidate_key"]

    serialized = json.dumps(analyze_snapshot(snapshot), ensure_ascii=False)

    assert "SENSITIVE_CASE_ID" not in serialized
    assert candidate_key not in serialized
    assert "case_id" not in serialized
    assert "candidate_key" not in serialized
    assert "feasible" in serialized


def test_report_includes_real_baseline_metrics_and_finite_margin() -> None:
    result = analyze(make_snapshot(), make_snapshot())["base"]

    assert result["baseline_metrics"] == {
        "public_coverage_hits": 8,
        "strict_grounded_hits": 6,
        "no_evidence_rejected": 0,
        "isolation_passed": 2,
        "passed": False,
    }
    assert result["required_grounded_score_for_7_of_8"] == pytest.approx(0.8939)
    assert result["max_no_evidence_score"] == pytest.approx(0.199)
    assert result["separation_margin"] == pytest.approx(0.6949)


def test_cli_writes_safe_report_and_creates_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_path = tmp_path / "base.json"
    large_path = tmp_path / "large.json"
    output_path = tmp_path / "nested" / "report.json"
    payload = json.dumps(make_snapshot())
    base_path.write_text(payload, encoding="utf-8")
    large_path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_d4_threshold_feasibility.py",
            "--base-snapshot",
            str(base_path),
            "--large-snapshot",
            str(large_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))[
        "candidate_snapshot_equal"
    ] is True
    assert "candidate_key" not in capsys.readouterr().out


def test_cli_single_snapshot_writes_safe_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot_path = tmp_path / "single-sensitive-name.json"
    output_path = tmp_path / "nested" / "single-report.json"
    snapshot = make_snapshot()
    snapshot["questions"][0]["case_id"] = "SENSITIVE_CASE_ID"
    candidate_key = snapshot["questions"][0]["candidates"][0]["candidate_key"]
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_d4_threshold_feasibility.py",
            "--snapshot",
            str(snapshot_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 0
    serialized_file = output_path.read_text(encoding="utf-8")
    serialized_stdout = capsys.readouterr().out
    for forbidden in (
        "SENSITIVE_CASE_ID",
        candidate_key,
        str(snapshot_path),
        "case_id",
        "candidate_key",
    ):
        assert forbidden not in serialized_file
        assert forbidden not in serialized_stdout
    assert json.loads(serialized_file)["feasible"] is True


@pytest.mark.parametrize(
    "input_arguments",
    [
        [],
        ["--base-snapshot", "base.json"],
        ["--large-snapshot", "large.json"],
        [
            "--snapshot",
            "single.json",
            "--base-snapshot",
            "base.json",
            "--large-snapshot",
            "large.json",
        ],
    ],
)
def test_cli_rejects_missing_or_mixed_snapshot_modes(
    input_arguments: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_d4_threshold_feasibility.py",
            *input_arguments,
            "--output",
            str(output_path),
        ],
    )

    assert main() == 2
    assert capsys.readouterr().out.strip() == "P14-D4 输入或执行失败。"
    assert not output_path.exists()


def test_cli_single_mode_rejects_output_that_overwrites_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot_path = tmp_path / "single.json"
    payload = json.dumps(make_snapshot())
    snapshot_path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_d4_threshold_feasibility.py",
            "--snapshot",
            str(snapshot_path),
            "--output",
            str(snapshot_path),
        ],
    )

    assert main() == 2
    assert capsys.readouterr().out.strip() == "P14-D4 输入或执行失败。"
    assert snapshot_path.read_text(encoding="utf-8") == payload


def test_cli_failure_is_fixed_and_does_not_leak_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad_path = tmp_path / "contains-secret-name.json"
    bad_path.write_text("not-json-secret-payload", encoding="utf-8")
    output_path = tmp_path / "report.json"
    output_path.write_text('{"stale": true}', encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_d4_threshold_feasibility.py",
            "--base-snapshot",
            str(bad_path),
            "--large-snapshot",
            str(bad_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 2
    output = capsys.readouterr().out
    assert output.strip() == "P14-D4 输入或执行失败。"
    assert "secret" not in output
    assert not output_path.exists()


def test_cli_rejects_output_path_that_would_overwrite_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_path = tmp_path / "base.json"
    large_path = tmp_path / "large.json"
    payload = json.dumps(make_snapshot())
    base_path.write_text(payload, encoding="utf-8")
    large_path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "p14_d4_threshold_feasibility.py",
            "--base-snapshot",
            str(base_path),
            "--large-snapshot",
            str(large_path),
            "--output",
            str(base_path),
        ],
    )

    assert main() == 2
    assert capsys.readouterr().out.strip() == "P14-D4 输入或执行失败。"
    assert base_path.read_text(encoding="utf-8") == payload
