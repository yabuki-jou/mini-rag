import json
from pathlib import Path

import pytest

from scripts.p14_d3_reranker_ab_probe import build_report, load_json


def diagnostic(case: str, score: float = 0.8) -> dict:
    row = {
        "case_id": case, "category": "grounded", "candidate_count": 30,
        "chroma_candidate_count": 30, "candidate_pool_complete": True,
        "standard_evidence_in_complete_top_30": True,
        "public_result_contains_expected_evidence": True,
        "matching_semantics_disagree": False, "candidate_kind": "expected",
        "expected_candidate_rank": 1, "expected_distance": 0.2,
    }
    return {"stage": "c4_a_candidate_pool", "reranker_query_mode": "c4_a",
            "retrieval_diagnostics": [{**row, "case_id": f"{case}-{i}"} for i in range(12)]}


def aggregate() -> dict:
    return {"grounded_returned_count": 5, "grounded_public_coverage_hits": 7, "grounded_candidate_pool_total": 8,
            "no_evidence_rejected_count": 2, "isolation_passed_count": 2,
            "latency_p95_ms": 1000}


def test_invalid_json_shape(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_json(p)


def test_equal_and_unequal_fingerprint_and_gates():
    report = build_report(aggregate(), diagnostic("A"), aggregate(), diagnostic("A"))
    assert report["candidate_snapshot_equal"] is True
    assert report["base_quality_gate"]["passed"] is True
    assert report["large_quality_gate"]["passed"] is True
    assert report["base_performance_gate"]["passed"] is True
    assert report["large_performance_gate"]["passed"] is True
    report = build_report(aggregate(), diagnostic("A"), aggregate(), diagnostic("B"))
    assert report["candidate_snapshot_equal"] is False


def test_sensitive_fields_are_not_output():
    report = build_report({**aggregate(), "reranker_score": 0.99}, diagnostic("A", 0.99), aggregate(), diagnostic("A", 0.99))
    dumped = json.dumps(report, ensure_ascii=False)
    assert "reranker_score" not in dumped
    assert "0.99" not in dumped


def test_quality_and_performance_failures_are_reported():
    poor = {**aggregate(), "grounded_public_coverage_hits": 6, "latency_p95_ms": 9000}
    report = build_report(poor, diagnostic("A"), poor, diagnostic("A"))
    assert report["base_quality_gate"]["passed"] is False
    assert report["large_performance_gate"]["passed"] is False


def test_reranker_dependent_dense_field_is_ignored():
    left = diagnostic("A")
    right = diagnostic("A")
    left["retrieval_diagnostics"][0]["strongest_candidate_dense_distance"] = 0.1
    right["retrieval_diagnostics"][0]["strongest_candidate_dense_distance"] = 0.9
    report = build_report(aggregate(), left, aggregate(), right)
    assert report["candidate_snapshot_equal"] is True
