"""验证 P14 固定题集检索评分和阈值选择规则。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import UUID

import pytest
import scripts.archive_v1_p14_acceptance as acceptance_runner

from scripts.archive_v1_p14_acceptance import (
    AcceptanceError,
    build_c4a_candidate_pool_diagnostics,
    build_c4a_aggregate_result,
    build_safe_retrieval_diagnostics,
    build_manual_field_payload,
    choose_distance_threshold,
    is_confirmed_document_response,
    parse_registered_user_id,
    choose_reranker_score_threshold,
    score_at_no_evidence_ceiling,
    item_contains_expected_evidence,
    score_reranker_outcomes,
    score_retrieval_outcomes,
    write_aggregate_result,
    write_safe_diagnostic,
)


def _item(*, filename: str, distance: float, excerpt: str = "标准证据") -> dict[str, object]:
    """构造与正式检索响应一致的最小证据项。"""
    return {
        "filename": filename,
        "location_type": "TEXT_LINE_RANGE",
        "location_start": 3,
        "location_end": 3,
        "excerpt": excerpt,
        "score": 1.0 - distance,
    }


def _grounded(*, distance: float) -> dict[str, object]:
    """构造一个命中标准证据的有据问题结果。"""
    return {
        "category": "GROUNDED",
        "project_id": "alpha",
        "expected_evidence": {
            "relative_path": "documents/alpha.txt",
            "items": [
                {
                    "location_type": "TEXT_LINE_RANGE",
                    "location_start": 3,
                    "location_end": 3,
                    "excerpt": "标准证据",
                }
            ],
        },
        "items": [_item(filename="alpha.txt", distance=distance)],
    }


def _reranked_item(*, filename: str, reranker_score: float) -> dict[str, object]:
    """构造带独立重排分数的最小正式检索项。"""
    item = _item(filename=filename, distance=0.2)
    item["reranker_score"] = reranker_score
    return item


def _d4_snapshot_outcomes() -> list[dict[str, object]]:
    """构造 D4-A 分支使用的 12 题、每题 30 候选内存结果。"""
    categories = ["GROUNDED"] * 8 + ["NO_EVIDENCE"] * 2 + ["ISOLATION"] * 2
    outcomes: list[dict[str, object]] = []
    for question_index, category in enumerate(categories):
        candidates = []
        for rank in range(1, 31):
            candidates.append(
                {
                    "candidate_key": f"{question_index:02x}{rank:062x}",
                    "reranker_rank": rank,
                    "reranker_score": 1.0 - rank * 0.001,
                    "public_coverage_match": category == "GROUNDED" and rank == 1,
                    "matches_expected_evidence": (
                        category == "GROUNDED" and rank == 1 and question_index < 6
                    ),
                    "isolation_violation": False,
                    "candidate_kind": "SAME_DOCUMENT",
                }
            )
        outcomes.append(
            {
                "case_id": f"Q-{question_index + 1:02d}",
                "category": category,
                "diagnostic_candidate_count": 30,
                "diagnostic_chroma_candidate_count": 30,
                "internal_candidates": candidates,
            }
        )
    return outcomes


def test_expected_evidence_requires_filename_location_and_excerpt() -> None:
    """标准证据必须同时满足文件、定位范围与摘录，不能仅按文件名误判。"""
    expected = _grounded(distance=0.2)["expected_evidence"]

    assert item_contains_expected_evidence(
        _item(filename="alpha.txt", distance=0.2), expected
    )
    assert not item_contains_expected_evidence(
        _item(filename="alpha.txt", distance=0.2, excerpt="不同内容"), expected
    )


def test_safe_retrieval_diagnostics_reports_expected_rank_and_nearest_error() -> None:
    """有据题必须并列保留 dense 与 Reranker 的安全排序证据。"""
    outcomes = [
        {
            "category": "GROUNDED",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "expected_evidence": {
                "relative_path": "documents/alpha-secret.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 3,
                        "location_end": 3,
                        "excerpt": "标准证据",
                    }
                ],
            },
            "items": [
                {
                    **_item(filename="alpha-secret.txt", distance=0.20),
                    "reranker_score": 0.9,
                },
                {
                    **_item(
                        filename="alpha-secret.txt",
                        distance=0.11,
                        excerpt="错误候选正文",
                    ),
                    "document_id": "11111111-1111-1111-1111-111111111111",
                    "reranker_score": 0.4,
                },
            ],
        }
    ]

    diagnostics = build_safe_retrieval_diagnostics(outcomes)

    assert diagnostics == [
        {
            "category": "GROUNDED",
            "case_id": "GROUNDED-01",
            "candidate_count": 2,
            "expected_in_chroma_top_10": True,
            "distance_gap": pytest.approx(-0.09),
            "expected_candidate_rank": 2,
            "expected_distance": 0.20,
            "incorrect_candidate_kind": "SAME_DOCUMENT",
            "nearest_candidate_distance": 0.11,
            "nearest_incorrect_distance": 0.11,
            "expected_reranker_rank": 1,
            "expected_reranker_score": 0.9,
            "strongest_incorrect_reranker_score": 0.4,
            "reranker_score_gap": 0.5,
            "strongest_reranker_incorrect_kind": "SAME_DOCUMENT",
        }
    ]
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    for forbidden in (
        "project-secret",
        "alpha-secret.txt",
        "错误候选正文",
        "11111111-1111-1111-1111-111111111111",
    ):
        assert forbidden not in serialized


def test_safe_retrieval_diagnostics_identifies_out_of_scope_candidates() -> None:
    """无据题必须显式保留最强 Reranker 候选的分数与 dense 距离。"""
    outcomes = [
        {
            "category": "NO_EVIDENCE",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "items": [
                {
                    **_item(
                        filename="alpha-secret.txt",
                        distance=0.4,
                        excerpt="Reranker 最强候选正文",
                    ),
                    "reranker_score": 0.8,
                },
                _item(
                    filename="foreign-secret.txt",
                    distance=0.12,
                    excerpt="其他项目原文",
                ) | {"reranker_score": 0.2},
            ],
        },
        {
            "category": "ISOLATION",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "items": [],
        },
        {
            "category": "NO_EVIDENCE",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "items": [],
        },
    ]

    diagnostics = build_safe_retrieval_diagnostics(outcomes)

    assert diagnostics == [
        {
            "category": "NO_EVIDENCE",
            "candidate_count": 2,
            "distance_gap": None,
            "expected_candidate_rank": None,
            "expected_distance": None,
            "incorrect_candidate_kind": "OUT_OF_SCOPE",
            "nearest_candidate_distance": 0.12,
            "nearest_incorrect_distance": 0.12,
            "strongest_candidate_reranker_score": 0.8,
            "strongest_candidate_dense_distance": 0.4,
        },
        {
            "category": "ISOLATION",
            "distance_gap": None,
            "expected_candidate_rank": None,
            "expected_distance": None,
            "incorrect_candidate_kind": "NONE",
            "nearest_candidate_distance": None,
            "nearest_incorrect_distance": None,
        },
        {
            "category": "NO_EVIDENCE",
            "candidate_count": 0,
            "distance_gap": None,
            "expected_candidate_rank": None,
            "expected_distance": None,
            "incorrect_candidate_kind": "NONE",
            "nearest_candidate_distance": None,
            "nearest_incorrect_distance": None,
            "strongest_candidate_reranker_score": None,
            "strongest_candidate_dense_distance": None,
        },
    ]


def test_safe_retrieval_diagnostics_marks_incomplete_candidate_pool_without_false_recall_claim() -> None:
    """候选不足 10 条时，缺失标准证据不能被断言为 Chroma Top-10 召回失败。"""
    outcome = _grounded(distance=0.2)
    outcome["allowed_filenames"] = ["alpha.txt"]
    outcome["items"] = [
        _item(filename="other.txt", distance=0.1, excerpt="错误候选")
        | {"reranker_score": 0.6}
        for _ in range(9)
    ]

    diagnostics = build_safe_retrieval_diagnostics([outcome])

    assert diagnostics[0]["candidate_count"] == 9
    assert diagnostics[0]["expected_candidate_rank"] is None
    assert diagnostics[0]["expected_in_chroma_top_10"] is False
    assert diagnostics[0]["expected_reranker_rank"] is None
    assert diagnostics[0]["expected_reranker_score"] is None


def test_safe_retrieval_diagnostics_prefers_internal_top_thirty_dual_ranking() -> None:
    """运行器必须保留内部 Top-30 的 dense 与 Reranker 独立排名。"""
    outcome = _grounded(distance=0.2)
    outcome["allowed_filenames"] = ["alpha.txt"]
    outcome["internal_candidates"] = [
        {
            "dense_rank": 12,
            "dense_distance": 0.4,
            "reranker_rank": 1,
            "reranker_score": 0.9,
            "matches_expected_evidence": True,
        },
        {
            "dense_rank": 1,
            "dense_distance": 0.1,
            "reranker_rank": 2,
            "reranker_score": 0.2,
            "matches_expected_evidence": False,
        },
    ]
    outcome["diagnostic_candidate_count"] = 30
    outcome["diagnostic_chroma_candidate_count"] = 30

    diagnostics = build_safe_retrieval_diagnostics([outcome])

    assert diagnostics[0]["candidate_count"] == 30
    assert diagnostics[0]["expected_candidate_rank"] == 12
    assert diagnostics[0]["expected_in_chroma_top_10"] is False
    assert diagnostics[0]["expected_reranker_rank"] == 1
    assert diagnostics[0]["expected_reranker_score"] == pytest.approx(0.9)
    assert diagnostics[0]["nearest_candidate_distance"] == pytest.approx(0.1)
    assert diagnostics[0]["chroma_candidate_count"] == 30


def test_c4a_diagnostics_marks_complete_top_thirty_and_compares_no_evidence_baseline() -> None:
    """C4-A 必须记录完整 Top-30，并保留两个无据题的 Top-20 对照分数。"""
    outcomes = [
        {
            "category": "GROUNDED",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "expected_evidence": _grounded(distance=0.2)["expected_evidence"],
            "items": [_reranked_item(filename="alpha-secret.txt", reranker_score=0.9)],
            "internal_candidates": [
                {
                    "dense_rank": 7,
                    "dense_distance": 0.2,
                    "reranker_rank": 1,
                    "reranker_score": 0.9,
                    "matches_expected_evidence": True,
                }
            ],
            "diagnostic_candidate_count": 30,
            "diagnostic_chroma_candidate_count": 30,
        },
        {
            "category": "NO_EVIDENCE",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "items": [_reranked_item(filename="alpha-secret.txt", reranker_score=0.8)],
            "internal_candidates": [
                {
                    "dense_rank": 1,
                    "dense_distance": 0.25,
                    "reranker_rank": 1,
                    "reranker_score": 0.8,
                    "matches_expected_evidence": False,
                }
            ],
            "diagnostic_candidate_count": 30,
            "diagnostic_chroma_candidate_count": 30,
        },
        {
            "category": "NO_EVIDENCE",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha-secret.txt"],
            "items": [],
            "internal_candidates": [],
            "diagnostic_candidate_count": 0,
            "diagnostic_chroma_candidate_count": 0,
        },
    ]

    diagnostics = build_c4a_candidate_pool_diagnostics(outcomes)

    assert diagnostics[0]["candidate_pool_complete"] is True
    assert diagnostics[0]["standard_evidence_in_complete_top_30"] is True
    assert diagnostics[1]["case_id"] == "NO_EVIDENCE-01"
    assert diagnostics[1]["candidate_pool_complete"] is True
    assert diagnostics[1]["top20_baseline_strongest_candidate_reranker_score"] == pytest.approx(0.973935)
    assert diagnostics[1]["top20_baseline_strongest_candidate_dense_distance"] == pytest.approx(0.287029)
    assert diagnostics[1]["reranker_score_delta_vs_top20"] == pytest.approx(-0.173935)
    assert diagnostics[2]["case_id"] == "NO_EVIDENCE-02"
    assert diagnostics[2]["strongest_candidate_reranker_score"] is None
    assert diagnostics[2]["top20_baseline_strongest_candidate_reranker_score"] == pytest.approx(0.707142)


def test_c4a_diagnostics_projects_isolation_candidate_count() -> None:
    """C4-A 必须把隔离题的 Chroma 候选数也投影到候选池完整性诊断。"""
    outcomes = [
        {
            "category": "ISOLATION",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha.txt"],
            "hidden_evidence_in_other_project": {
                "relative_path": "documents/beta.txt"
            },
            "items": [_reranked_item(filename="alpha.txt", reranker_score=0.8)],
            "internal_candidates": [
                {
                    "dense_rank": 1,
                    "dense_distance": 0.2,
                    "reranker_rank": 1,
                    "reranker_score": 0.8,
                    "matches_expected_evidence": False,
                    "candidate_kind": "SAME_DOCUMENT",
                }
            ],
            "diagnostic_candidate_count": 30,
            "diagnostic_chroma_candidate_count": 30,
        }
    ]

    diagnostics = build_c4a_candidate_pool_diagnostics(outcomes)

    assert diagnostics[0]["candidate_count"] == 30
    assert diagnostics[0]["chroma_candidate_count"] == 30
    assert diagnostics[0]["candidate_pool_complete"] is True


def test_c4a_diagnostics_distinguishes_public_coverage_from_exact_match() -> None:
    """C4-A 必须显式区分公开 Chunk 覆盖式匹配与诊断精确匹配。"""
    expected_evidence = _grounded(distance=0.2)["expected_evidence"]
    outcomes = [
        {
            "category": "GROUNDED",
            "project_id": "project-secret",
            "allowed_filenames": ["alpha.txt"],
            "expected_evidence": expected_evidence,
            "items": [
                {
                    **_reranked_item(filename="alpha.txt", reranker_score=0.8),
                    "location_end": 5,
                    "excerpt": "前缀 标准证据 后缀",
                }
            ],
            "internal_candidates": [
                {
                    "dense_rank": 1,
                    "dense_distance": 0.2,
                    "reranker_rank": 1,
                    "reranker_score": 0.8,
                    "matches_expected_evidence": False,
                    "candidate_kind": "SAME_DOCUMENT",
                }
            ],
            "diagnostic_candidate_count": 30,
            "diagnostic_chroma_candidate_count": 30,
        }
    ]

    diagnostics = build_c4a_candidate_pool_diagnostics(outcomes)

    assert diagnostics[0]["public_result_contains_expected_evidence"] is True
    assert diagnostics[0]["standard_evidence_in_complete_top_30"] is False
    assert diagnostics[0]["matching_semantics_disagree"] is True

    result = build_c4a_aggregate_result(
        outcomes,
        diagnostics,
        latency_p95_ms=100.0,
        index_context_summary={
            "document_count": 1,
            "contextual_chunk_count": 1,
            "zero_context_document_count": 0,
        },
    )

    assert result["grounded_public_coverage_hits"] == 1
    assert result["grounded_matching_semantics_disagreement_count"] == 1


def test_c4a_aggregate_records_candidate_pool_and_latency_without_threshold() -> None:
    """C4-A 聚合只记录候选池、当前未过滤结果和 P95，不生成阈值。"""
    outcomes = [
        {
            "category": "GROUNDED",
            "project_id": "alpha",
            "allowed_filenames": ["alpha.txt"],
            "expected_evidence": _grounded(distance=0.2)["expected_evidence"],
            "items": [_reranked_item(filename="alpha.txt", reranker_score=0.9)],
        },
        {
            "category": "NO_EVIDENCE",
            "project_id": "alpha",
            "allowed_filenames": ["alpha.txt"],
            "items": [_reranked_item(filename="alpha.txt", reranker_score=0.8)],
        },
    ]
    diagnostics = [
        {
            "category": "GROUNDED",
            "case_id": "GROUNDED-01",
            "candidate_count": 30,
            "chroma_candidate_count": 30,
            "candidate_pool_complete": True,
            "standard_evidence_in_complete_top_30": True,
            "public_result_contains_expected_evidence": True,
            "matching_semantics_disagree": False,
        },
        {
            "category": "NO_EVIDENCE",
            "case_id": "NO_EVIDENCE-01",
            "candidate_count": 30,
            "chroma_candidate_count": 30,
            "candidate_pool_complete": True,
            "strongest_candidate_reranker_score": 0.8,
            "strongest_candidate_dense_distance": 0.2,
        },
    ]

    result = build_c4a_aggregate_result(
        outcomes,
        diagnostics,
        latency_p95_ms=12345.67,
        index_context_summary={
            "document_count": 12,
            "contextual_chunk_count": 63,
            "zero_context_document_count": 0,
        },
    )

    assert result["candidate_pool_expected_count"] == 30
    assert result["grounded_candidate_pool_hits"] == 1
    assert result["grounded_public_coverage_hits"] == 1
    assert result["grounded_matching_semantics_disagreement_count"] == 0
    assert result["no_evidence_rejected_count"] == 0
    assert result["latency_p95_ms"] == pytest.approx(12345.67)
    assert "reranker_score_threshold" not in result


def test_threshold_selection_meets_recall_no_evidence_and_isolation_gates() -> None:
    """阈值选择必须同时满足 7/8 召回、2/2 空结果和 2/2 项目隔离。"""
    outcomes = [_grounded(distance=0.20) for _ in range(7)]
    outcomes.append(_grounded(distance=0.80))
    outcomes.extend(
        [
            {
                "category": "NO_EVIDENCE",
                "project_id": "alpha",
                "items": [_item(filename="alpha.txt", distance=0.45)],
            },
            {
                "category": "NO_EVIDENCE",
                "project_id": "beta",
                "items": [_item(filename="beta.txt", distance=0.50)],
            },
            {
                "category": "ISOLATION",
                "project_id": "alpha",
                "hidden_evidence_in_other_project": {
                    "relative_path": "documents/beta.txt"
                },
                "items": [_item(filename="alpha.txt", distance=0.22)],
            },
            {
                "category": "ISOLATION",
                "project_id": "beta",
                "hidden_evidence_in_other_project": {
                    "relative_path": "documents/alpha.txt"
                },
                "items": [_item(filename="beta.txt", distance=0.24)],
            },
        ]
    )

    threshold, score = choose_distance_threshold(outcomes)

    # 0.24 仍保留全部通过项且比 0.20 多出相关证据的安全余量；
    # 继续扩大到 0.45 会让无依据问题出现候选。
    assert threshold == pytest.approx(0.24)
    assert score["grounded_passed"] == 7
    assert score["no_evidence_passed"] == 2
    assert score["isolation_passed"] == 2
    assert score["passed"] is True


def test_reranker_threshold_selection_uses_only_final_reranker_scores() -> None:
    """阶段 C 标定必须按重排分数下限过滤，而不能复用 dense distance。"""
    outcomes: list[dict[str, object]] = []
    for _ in range(7):
        outcome = _grounded(distance=0.2)
        outcome["items"][0]["reranker_score"] = 0.8
        outcomes.append(outcome)
    eighth_grounded = _grounded(distance=0.2)
    eighth_grounded["items"][0]["reranker_score"] = 0.4
    outcomes.append(eighth_grounded)
    outcomes.extend(
        [
            {"category": "NO_EVIDENCE", "project_id": "alpha", "items": [_reranked_item(filename="alpha.txt", reranker_score=0.3)]},
            {"category": "NO_EVIDENCE", "project_id": "beta", "items": [_reranked_item(filename="beta.txt", reranker_score=0.2)]},
            {"category": "ISOLATION", "project_id": "alpha", "hidden_evidence_in_other_project": {"relative_path": "documents/beta.txt"}, "allowed_filenames": ["alpha.txt"], "items": [_reranked_item(filename="alpha.txt", reranker_score=0.8)]},
            {"category": "ISOLATION", "project_id": "beta", "hidden_evidence_in_other_project": {"relative_path": "documents/alpha.txt"}, "allowed_filenames": ["beta.txt"], "items": [_reranked_item(filename="beta.txt", reranker_score=0.8)]},
        ]
    )

    threshold, score = choose_reranker_score_threshold(outcomes)

    assert threshold == pytest.approx(0.4)
    assert score_reranker_outcomes(outcomes, threshold=threshold) == score
    assert score["grounded_passed"] == 8
    assert score["no_evidence_passed"] == 2
    assert score["isolation_passed"] == 2
    assert score["passed"] is True


def test_no_evidence_ceiling_reports_the_best_recall_before_any_no_evidence_candidate() -> None:
    """阈值无法通过时，诊断应量化拒答全部无依据候选所牺牲的有据召回。"""
    outcomes = [_grounded(distance=0.20) for _ in range(6)]
    outcomes.extend([_grounded(distance=0.80), _grounded(distance=0.80)])
    outcomes.extend(
        [
            {"category": "NO_EVIDENCE", "project_id": "alpha", "items": [_item(filename="alpha.txt", distance=0.45)]},
            {"category": "NO_EVIDENCE", "project_id": "beta", "items": [_item(filename="beta.txt", distance=0.50)]},
            {"category": "ISOLATION", "project_id": "alpha", "hidden_evidence_in_other_project": {"relative_path": "documents/beta.txt"}, "items": []},
            {"category": "ISOLATION", "project_id": "beta", "hidden_evidence_in_other_project": {"relative_path": "documents/alpha.txt"}, "items": []},
        ]
    )

    score = score_at_no_evidence_ceiling(outcomes)

    assert score["grounded_passed"] == 6
    assert score["no_evidence_passed"] == 2
    assert score["isolation_passed"] == 2


def test_score_rejects_other_project_evidence_even_when_query_has_results() -> None:
    """隔离问题只要出现隐藏项目文件，就不能通过。"""
    outcome = {
        "category": "ISOLATION",
        "project_id": "alpha",
        "hidden_evidence_in_other_project": {"relative_path": "documents/beta.txt"},
        "items": [_item(filename="beta.txt", distance=0.1)],
    }

    score = score_retrieval_outcomes([outcome], threshold=0.2)

    assert score["isolation_passed"] == 0
    assert score["passed"] is False


def test_manual_field_payload_uses_the_correct_value_column_and_marks_reviewed() -> None:
    """真实验收的人工确认必须使用字段所属值列并带上标注证据。"""
    title_payload = build_manual_field_payload(
        "TITLE",
        {
            "value": "虚构资料标题",
            "evidence": [
                {
                    "excerpt": "资料标题：虚构资料标题",
                    "location_type": "TEXT_LINE_RANGE",
                    "location_start": 1,
                    "location_end": 1,
                }
            ],
        },
        expected_version=3,
    )
    keywords_payload = build_manual_field_payload(
        "KEYWORDS",
        {"value": ["虚构", "验收"], "evidence": []},
        expected_version=4,
    )
    empty_payload = build_manual_field_payload(
        "VERSION_NUMBER", None, expected_version=5
    )

    assert title_payload["text_value"] == "虚构资料标题"
    assert "date_value" not in title_payload
    assert title_payload["source"] == "MANUAL"
    assert title_payload["review_status"] == "VALUE_CONFIRMED"
    assert title_payload["expected_version"] == 3
    assert keywords_payload["json_value"] == ["虚构", "验收"]
    assert "text_value" not in keywords_payload
    assert empty_payload == {
        "review_status": "EMPTY_ACCEPTED",
        "source": "MANUAL",
        "no_source_evidence": True,
        "evidences": [],
        "expected_version": 5,
    }


def test_cli_guard_runs_only_after_all_helper_definitions() -> None:
    """直接执行脚本时，main 不能早于它依赖的辅助函数定义。"""
    script_path = Path(__file__).parents[2] / "scripts" / "archive_v1_p14_acceptance.py"
    module = ast.parse(script_path.read_text(encoding="utf-8"))
    guard_index = next(
        index
        for index, node in enumerate(module.body)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(item, ast.Constant) and item.value == "__main__"
            for item in [node.test.left, *node.test.comparators]
        )
    )
    assert all(
        not isinstance(node, (ast.FunctionDef, ast.ClassDef))
        for node in module.body[guard_index + 1 :]
    )


def test_c4_phases_are_explicit_and_threshold_calibration_is_separate() -> None:
    """C4-A 与 C4-B 必须显式选择，阈值标定仍是独立阶段。"""
    script_path = Path(__file__).parents[2] / "scripts" / "archive_v1_p14_acceptance.py"
    module = ast.parse(script_path.read_text(encoding="utf-8"))
    main_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    phase_argument = next(
        node
        for node in ast.walk(main_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "--phase"
    )
    keyword_values = {
        keyword.arg: keyword.value for keyword in phase_argument.keywords
    }
    assert isinstance(keyword_values["default"], ast.Constant)
    assert keyword_values["default"].value == "c4-a"
    assert isinstance(keyword_values["choices"], (ast.Tuple, ast.List))
    assert {
        element.value
        for element in keyword_values["choices"].elts
        if isinstance(element, ast.Constant)
    } == {
        "c4-a",
        "c4-b",
        "d4-a-snapshot",
        "threshold-calibration",
        "d5-capture",
        "d6b-capture",
        "enterprise-capture",
    }

    calibration_call = next(
        node
        for node in ast.walk(main_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_retrieval_calibration"
    )
    calibrate_keyword = next(
        keyword
        for keyword in calibration_call.keywords
        if keyword.arg == "calibrate_threshold"
    )
    assert isinstance(calibrate_keyword.value, ast.Compare)
    assert isinstance(calibrate_keyword.value.comparators[0], ast.Constant)
    assert calibrate_keyword.value.comparators[0].value == "threshold-calibration"

    snapshot_argument = next(
        node
        for node in ast.walk(main_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "--snapshot-file"
    )
    assert snapshot_argument is not None

    snapshot_keyword = next(
        keyword
        for keyword in calibration_call.keywords
        if keyword.arg == "snapshot_file"
    )
    assert isinstance(snapshot_keyword.value, ast.Attribute)
    assert snapshot_keyword.value.attr == "snapshot_file"


def test_collect_retrieval_outcomes_preserves_fixed_question_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4-A 内存结果必须保留标注中的固定题 ID，且不得保存问题正文。"""

    class FakeApi:
        """返回公开检索与内部诊断的最小客户端。"""

        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            """根据调用顺序返回公开结果或诊断占位。"""
            if _args[1].endswith("archive-retrieval"):
                return {"items": []}
            return {"diagnostic": True}

    monkeypatch.setattr(
        acceptance_runner,
        "_read_safe_internal_diagnostic",
        lambda _payload: {
            "candidates": [],
            "candidate_count": 0,
            "chroma_candidate_count": 0,
            "reranker_query_mode": "c4_a",
        },
    )

    outcomes, _ = acceptance_runner._collect_retrieval_outcomes(
        FakeApi(),
        question_label={
            "questions": [
                {
                    "id": "Q-01",
                    "project_id": "alpha",
                    "category": "NO_EVIDENCE",
                    "question": "只用于请求，不得保留",
                }
            ]
        },
        project_ids={"alpha": "project-id"},
        filenames_by_project={"alpha": ["alpha.txt"]},
    )

    assert outcomes[0]["case_id"] == "Q-01"
    assert "question" not in outcomes[0]
    assert "query" not in outcomes[0]


def test_collect_retrieval_outcomes_supports_d5_top_five_candidates() -> None:
    """D5 捕获必须把真实问答检索请求固定为 Top-5。"""
    class FakeApi:
        """记录 D5 捕获请求的最小客户端。"""

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def request(
            self,
            method: str,
            path: str,
            **kwargs: object,
        ) -> dict[str, object]:
            self.calls.append({"method": method, "path": path, **kwargs})
            if path.endswith("archive-retrieval"):
                return {
                    "items": [_item(filename="alpha.txt", distance=0.2)],
                    "requested_top_k": 5,
                    "returned_count": 1,
                }
            return {"diagnostic": True}

    api = FakeApi()
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            acceptance_runner,
            "_read_safe_internal_diagnostic",
            lambda _payload: {
                "candidates": [],
                "candidate_count": 30,
                "chroma_candidate_count": 30,
                "reranker_query_mode": "c4_a",
            },
        )
        outcomes, _ = acceptance_runner._collect_retrieval_outcomes(
            api,
            question_label={
                "questions": [
                    {
                        "id": "Q-01",
                        "project_id": "alpha",
                        "category": "GROUNDED",
                        "question": "D5 问题",
                        "expected_evidence": {"items": []},
                    }
                ]
            },
            project_ids={"alpha": "project-id"},
            filenames_by_project={"alpha": ["alpha.txt"]},
            top_k=5,
            include_ground_truth_in_diagnostic=False,
            )
    finally:
        monkeypatch.undo()

    retrieval_call = next(
        call for call in api.calls if str(call["path"]).endswith("archive-retrieval")
    )
    assert retrieval_call["payload"] == {"query": "D5 问题", "top_k": 5}
    assert outcomes[0]["retrieval_requested_top_k"] == 5
    diagnostic_calls = [
        call
        for call in api.calls
        if str(call["path"]).endswith("archive-retrieval-diagnostic")
    ]
    assert diagnostic_calls
    assert "expected_evidence" not in diagnostic_calls[0]["payload"]


def test_d5_capture_phase_writes_twelve_pixie_entries_and_safe_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D5 阶段应写入 12 条候选捕获，并将聚合文件限制为安全指标。"""
    class FakeApi:
        """隔离 D5 阶段控制流的最小客户端。"""

        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭客户端。"""

    questions = [
        {
            "id": f"Q-{index:02d}",
            "project_id": "alpha" if index <= 8 else "beta",
            "category": (
                "GROUNDED"
                if index <= 8
                else "NO_EVIDENCE"
                if index <= 10
                else "ISOLATION"
            ),
            "question": f"问题 {index}",
            "expected_answer": f"答案 {index}" if index <= 8 else None,
            "expected_evidence": {
                "relative_path": "documents/alpha-secret.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 1,
                        "location_end": 1,
                        "excerpt": "真实候选摘录",
                    }
                ],
            }
            if index <= 8
            else None,
        }
        for index in range(1, 13)
    ]
    outcomes = [
        {
            "case_id": question["id"],
            "category": question["category"],
            "project_id": question["project_id"],
            "expected_evidence": question["expected_evidence"],
            "items": [
                {
                    "document_id": "11111111-1111-1111-1111-111111111111",
                    "filename": "alpha-secret.txt",
                    "location_type": "TEXT_LINE_RANGE",
                    "location_start": 1,
                    "location_end": 1,
                    "excerpt": "真实候选摘录",
                    "score": 0.9,
                }
            ],
            "allowed_filenames": ["alpha-secret.txt"],
            "diagnostic_candidate_count": 30,
            "diagnostic_chroma_candidate_count": 30,
            "retrieval_latency_ms": float(index),
        }
        for index, question in enumerate(questions, start=1)
    ]
    captured: dict[str, object] = {}
    user_id = UUID("12345678-1234-5678-1234-567812345678")

    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda path: (
            {"dataset_id": "fixture", "questions": questions}
            if "question" in str(path)
            else {"dataset_id": "fixture"}
        ),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "fixture"),
    )

    def fake_seed(
        _api: object,
        *,
        project_ids: dict[str, str],
        **_kwargs: object,
    ) -> tuple[dict[str, list[str]], list[int]]:
        project_ids.update({"alpha": "project-alpha", "beta": "project-beta"})
        return {"alpha": ["alpha-secret.txt"], "beta": ["beta-secret.txt"]}, [1] * 12

    monkeypatch.setattr(acceptance_runner, "_seed_confirmed_documents", fake_seed)

    def fake_collect(
        _api: object,
        *,
        top_k: int,
        **_kwargs: object,
    ) -> tuple[list[dict[str, object]], list[float]]:
        captured["top_k"] = top_k
        captured["include_ground_truth_in_diagnostic"] = _kwargs[
            "include_ground_truth_in_diagnostic"
        ]
        return outcomes, [float(index) for index in range(1, 13)]

    monkeypatch.setattr(acceptance_runner, "_collect_retrieval_outcomes", fake_collect)
    monkeypatch.setattr(
        acceptance_runner, "_cleanup_seeded_scope", lambda *_args, **_kwargs: None
    )

    dataset_file = tmp_path / "nested" / "d5-capture.json"
    result_file = tmp_path / "safe-result.json"
    result = acceptance_runner.run_retrieval_calibration(
        base_url="http://fixture",
        result_file=result_file,
        d5_dataset_file=dataset_file,
        calibrate_threshold=False,
        phase="d5-capture",
    )

    dataset = json.loads(dataset_file.read_text(encoding="utf-8"))
    aggregate = json.loads(result_file.read_text(encoding="utf-8"))
    assert captured["top_k"] == 5
    assert captured["include_ground_truth_in_diagnostic"] is False
    assert len(dataset["entries"]) == 12
    assert all(set(entry["input_data"]) == {"question"} for entry in dataset["entries"])
    assert dataset["entries"][0]["eval_metadata"]["expected_answer"] == "答案 1"
    assert dataset["entries"][0]["eval_metadata"]["expected_evidence"] == {
        "relative_path": "documents/alpha-secret.txt",
        "items": [
            {
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 1,
                "location_end": 1,
                "excerpt": "真实候选摘录",
            }
        ],
    }
    assert dataset["evaluators"] == [
        "evals/archive/evaluators.py:archive_answer_contract",
        "evals/archive/evaluators.py:archive_evidence_faithfulness",
        "evals/archive/evaluators.py:archive_refusal_quality",
        "evals/archive/evaluators.py:archive_v1_p02_quality_gate",
    ]
    assert [
        entry["eval_metadata"]["category"] for entry in dataset["entries"]
    ] == ["GROUNDED"] * 8 + ["NO_EVIDENCE"] * 2 + ["ISOLATION"] * 2
    assert dataset["entries"][0]["eval_metadata"]["public_coverage_match"] is True
    assert dataset["entries"][8]["eval_metadata"]["public_coverage_match"] is False
    assert all(
        entry["eval_metadata"]["candidate_pool_complete"] is True
        and "retrieval_latency_ms" in entry["eval_metadata"]
        for entry in dataset["entries"]
    )
    serialized_aggregate = json.dumps(aggregate, ensure_ascii=False)
    assert result["question_count"] == 12
    assert result["public_coverage_grounded_count"] == 8
    assert all(secret not in serialized_aggregate for secret in ("问题", "filename", "摘录"))
    assert "11111111-1111-1111-1111-111111111111" not in serialized_aggregate


def test_d5_capture_requires_target_and_removes_stale_file_before_external_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D5 缺少目标或前置失败时不得误读旧捕获文件。"""
    target = tmp_path / "stale-d5.json"
    target.write_text('{"stale": true}', encoding="utf-8")
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda _path: (_ for _ in ()).throw(
            acceptance_runner.AcceptanceError("固定集读取失败。")
        ),
    )

    with pytest.raises(ValueError, match="D5 捕获数据集文件"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            calibrate_threshold=False,
            phase="d5-capture",
        )

    with pytest.raises(acceptance_runner.AcceptanceError, match="固定集读取失败"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            d5_dataset_file=target,
            calibrate_threshold=False,
            phase="d5-capture",
        )
    assert not target.exists()


def test_d6b_capture_writes_twelve_top_eight_pixie_entries_and_marks_dataset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D6-B 必须请求 Top-8 并写出明确标记 D6-B 的 12 条数据集记录。"""

    class FakeApi:
        """隔离 D6-B 阶段控制流的最小客户端。"""

        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭客户端。"""

    questions = [
        {
            "id": f"Q-{index:02d}",
            "project_id": "alpha" if index <= 8 else "beta",
            "category": "GROUNDED" if index <= 8 else "NO_EVIDENCE",
            "question": f"D6-B 问题 {index}",
            "expected_answer": f"答案 {index}" if index <= 8 else None,
            "expected_evidence": {"items": []} if index <= 8 else None,
        }
        for index in range(1, 13)
    ]
    outcomes = [
        {
            "case_id": question["id"],
            "category": question["category"],
            "project_id": question["project_id"],
            "expected_evidence": question["expected_evidence"],
            "items": [],
            "allowed_filenames": ["alpha.txt"],
            "diagnostic_candidate_count": 30,
            "diagnostic_chroma_candidate_count": 30,
            "retrieval_requested_top_k": 8,
            "retrieval_returned_count": 0,
            "retrieval_latency_ms": 1.0,
        }
        for question in questions
    ]
    captured: dict[str, object] = {}
    user_id = UUID("12345678-1234-5678-1234-567812345678")

    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda path: (
            {"dataset_id": "fixture", "questions": questions}
            if "question" in str(path)
            else {"dataset_id": "fixture"}
        ),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "fixture"),
    )

    def fake_seed(
        _api: object,
        *,
        project_ids: dict[str, str],
        **_kwargs: object,
    ) -> tuple[dict[str, list[str]], list[int]]:
        project_ids.update({"alpha": "project-alpha", "beta": "project-beta"})
        return {"alpha": ["alpha.txt"], "beta": ["beta.txt"]}, [1] * 12

    monkeypatch.setattr(acceptance_runner, "_seed_confirmed_documents", fake_seed)

    def fake_collect(
        _api: object,
        *,
        top_k: int,
        **_kwargs: object,
    ) -> tuple[list[dict[str, object]], list[float]]:
        captured["top_k"] = top_k
        captured["include_ground_truth_in_diagnostic"] = _kwargs[
            "include_ground_truth_in_diagnostic"
        ]
        return outcomes, [float(index) for index in range(1, 13)]

    monkeypatch.setattr(acceptance_runner, "_collect_retrieval_outcomes", fake_collect)
    monkeypatch.setattr(
        acceptance_runner, "_cleanup_seeded_scope", lambda *_args, **_kwargs: None
    )

    dataset_file = tmp_path / "nested" / "d6b-capture.json"
    result = acceptance_runner.run_retrieval_calibration(
        base_url="http://fixture",
        d6b_dataset_file=dataset_file,
        calibrate_threshold=False,
        phase="d6b-capture",
    )

    dataset = json.loads(dataset_file.read_text(encoding="utf-8"))
    assert captured == {
        "top_k": 8,
        "include_ground_truth_in_diagnostic": False,
    }
    assert len(dataset["entries"]) == 12
    assert dataset["name"] == "archive-question-d6b-top8-captured"
    assert "D6-B" in dataset["description"]
    assert "Top-8" in dataset["description"]
    assert all(
        entry["eval_input"][0]["value"]["requested_top_k"] == 8
        for entry in dataset["entries"]
    )
    assert result["d6b_dataset_written"] is True


def test_d6b_capture_requires_own_target_and_rejects_d5_target_before_external_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D6-B 路径缺失或误用 D5 参数时必须在外部调用前失败。"""
    calls: list[str] = []
    monkeypatch.setattr(
        acceptance_runner,
        "P14Api",
        lambda _: calls.append("api") or pytest.fail("路径校验后不得创建客户端"),
    )

    with pytest.raises(ValueError, match="D6-B 捕获数据集文件"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            calibrate_threshold=False,
            phase="d6b-capture",
        )

    with pytest.raises(ValueError, match="D5 捕获数据集文件仅允许 d5-capture"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            d5_dataset_file=tmp_path / "wrong.json",
            calibrate_threshold=False,
            phase="d6b-capture",
        )

    target = tmp_path / "stale-d6b.json"
    target.write_text('{"stale": true}', encoding="utf-8")
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda _path: (_ for _ in ()).throw(
            acceptance_runner.AcceptanceError("固定集读取失败。")
        ),
    )
    with pytest.raises(acceptance_runner.AcceptanceError, match="固定集读取失败"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            d6b_dataset_file=target,
            calibrate_threshold=False,
            phase="d6b-capture",
        )

    assert calls == []
    assert not target.exists()


def test_internal_diagnostic_preserves_only_d4_candidate_fields() -> None:
    """诊断读取层必须保留 D4 标签，同时丢弃服务端额外敏感字段。"""
    candidate = {
        "dense_rank": 1,
        "dense_distance": 0.2,
        "reranker_rank": 1,
        "reranker_score": 0.9,
        "matches_expected_evidence": False,
        "candidate_kind": "SAME_DOCUMENT",
        "candidate_key": "a" * 64,
        "public_coverage_match": True,
        "isolation_violation": False,
        "content": "不得保留的正文",
        "document_id": "不得保留的文档标识",
    }

    diagnostic = acceptance_runner._read_safe_internal_diagnostic(
        {
            "candidate_count": 1,
            "chroma_candidate_count": 1,
            "reranker_query_mode": "c4_a",
            "candidates": [candidate],
        }
    )

    projected = diagnostic["candidates"][0]
    assert projected["candidate_key"] == "a" * 64
    assert projected["public_coverage_match"] is True
    assert projected["isolation_violation"] is False
    assert "content" not in projected
    assert "document_id" not in projected


def test_d4a_phase_requires_snapshot_file_before_external_work() -> None:
    """显式选择 D4-A 时必须提供快照路径，避免真实运行后无证据落盘。"""
    with pytest.raises(ValueError, match="D4-A 快照文件"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            calibrate_threshold=False,
            phase="d4-a-snapshot",
        )


def test_d4a_cli_without_snapshot_file_fails_stably(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """命令行遗漏快照路径时必须在任何外部调用前返回稳定失败。"""
    monkeypatch.setattr(
        "sys.argv",
        ["archive_v1_p14_acceptance.py", "--phase", "d4-a-snapshot"],
    )

    assert acceptance_runner.main() == 1
    assert capsys.readouterr().out.strip() == (
        "P14 retrieval evaluation failed: D4-A 快照文件不能为空。"
    )


def test_snapshot_file_is_rejected_outside_d4a_phase(tmp_path: Path) -> None:
    """非 D4-A 阶段不得意外写出包含逐候选分数的快照。"""
    with pytest.raises(ValueError, match="仅允许 D4-A"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            snapshot_file=tmp_path / "unexpected.json",
            calibrate_threshold=False,
            phase="c4-a",
        )


def test_d4a_phase_removes_stale_snapshot_before_external_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """新一轮启动后不得保留可被误认成本轮结果的旧快照。"""
    snapshot_file = tmp_path / "stale-snapshot.json"
    snapshot_file.write_text('{"stale": true}', encoding="utf-8")
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda _path: (_ for _ in ()).throw(
            acceptance_runner.AcceptanceError("固定集读取失败。")
        ),
    )

    with pytest.raises(acceptance_runner.AcceptanceError, match="固定集读取失败"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            snapshot_file=snapshot_file,
            calibrate_threshold=False,
            phase="d4-a-snapshot",
        )

    assert not snapshot_file.exists()


def test_d4a_run_writes_snapshot_without_threshold_calibration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D4-A 只写脱敏 Top-30 快照和聚合结果，不得执行阈值标定。"""

    class FakeApi:
        """隔离 D4-A 控制流的最小客户端。"""

        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭客户端。"""

    user_id = UUID("12345678-1234-5678-1234-567812345678")
    outcomes = _d4_snapshot_outcomes()
    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda _path: {"dataset_id": "fixture"},
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "fixture"),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_seed_confirmed_documents",
        lambda *_args, **_kwargs: ({"alpha": ["alpha.txt"]}, [2] * 12),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_collect_retrieval_outcomes",
        lambda *_args, **_kwargs: (outcomes, [float(index) for index in range(1, 13)]),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "choose_reranker_score_threshold",
        lambda _outcomes: pytest.fail("D4-A 不得调用阈值标定"),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_cleanup_seeded_scope",
        lambda *_args, **_kwargs: None,
    )

    snapshot_file = tmp_path / "d4-a" / "snapshot.json"
    result = acceptance_runner.run_retrieval_calibration(
        base_url="http://fixture",
        snapshot_file=snapshot_file,
        calibrate_threshold=False,
        phase="d4-a-snapshot",
    )

    snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
    assert len(snapshot["questions"]) == 12
    assert all(len(question["candidates"]) == 30 for question in snapshot["questions"])
    assert result == {
        "candidate_pool_expected_count": 30,
        "candidate_pool_complete_question_count": 12,
        "candidate_pool_incomplete_question_count": 0,
        "contextual_chunk_count": 24,
        "document_count": 12,
        "grounded_question_count": 8,
        "isolation_question_count": 2,
        "latency_p95_ms": 12.0,
        "no_evidence_question_count": 2,
        "question_count": 12,
        "snapshot_written": True,
        "zero_context_document_count": 0,
    }


def test_c4b_run_writes_query_expression_stage_without_threshold_calibration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C4-B 只记录查询表达复验，不得隐式进入阈值标定。"""

    class FakeApi:
        """隔离 C4-B 控制流的最小客户端。"""

        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭客户端。"""

    user_id = UUID("12345678-1234-5678-1234-567812345678")
    outcomes = [
        {
            "category": "GROUNDED",
            "project_id": "alpha",
            "allowed_filenames": ["alpha.txt"],
            "expected_evidence": _grounded(distance=0.2)["expected_evidence"],
            "items": [_reranked_item(filename="alpha.txt", reranker_score=0.9)],
        }
    ]
    diagnostics = [
        {
            "category": "GROUNDED",
            "case_id": "GROUNDED-01",
            "candidate_pool_complete": True,
            "standard_evidence_in_complete_top_30": True,
        }
    ]

    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda _path: {"dataset_id": "fixture"},
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "fixture"),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_seed_confirmed_documents",
        lambda *_args, **_kwargs: ({"alpha": ["alpha.txt"]}, [1]),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_collect_retrieval_outcomes",
        lambda *_args, **_kwargs: (outcomes, [12.3]),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "build_c4a_candidate_pool_diagnostics",
        lambda _outcomes: diagnostics,
    )
    monkeypatch.setattr(
        acceptance_runner,
        "build_c4a_aggregate_result",
        lambda *_args, **_kwargs: {
            "candidate_pool_expected_count": 30,
            "quality_gate_at_current_unfiltered_results": False,
        },
    )
    monkeypatch.setattr(
        acceptance_runner,
        "choose_reranker_score_threshold",
        lambda _outcomes: pytest.fail("C4-B 不得调用阈值标定"),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_cleanup_seeded_scope",
        lambda *_args, **_kwargs: None,
    )

    diagnostic_file = tmp_path / "p14-c4b-diagnostic.json"
    result = acceptance_runner.run_retrieval_calibration(
        base_url="http://fixture",
        diagnostic_file=diagnostic_file,
        calibrate_threshold=False,
        phase="c4-b",
    )

    assert result["candidate_pool_expected_count"] == 30
    assert json.loads(diagnostic_file.read_text(encoding="utf-8"))["stage"] == (
        "c4_b_query_expression"
    )


def test_c4a_run_writes_observation_without_threshold_calibration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C4-A 运行分支只能写候选池观测，不能隐式调用阈值标定。"""

    class FakeApi:
        """隔离 C4-A 控制流的最小客户端。"""

        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭客户端。"""

    user_id = UUID("12345678-1234-5678-1234-567812345678")
    outcomes = [
        {
            "category": "GROUNDED",
            "project_id": "alpha",
            "allowed_filenames": ["alpha.txt"],
            "items": [_reranked_item(filename="alpha.txt", reranker_score=0.9)],
        }
    ]
    diagnostics = [
        {
            "category": "GROUNDED",
            "case_id": "GROUNDED-01",
            "candidate_pool_complete": True,
            "standard_evidence_in_complete_top_30": True,
        }
    ]

    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda _path: {"dataset_id": "fixture"},
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "fixture"),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_seed_confirmed_documents",
        lambda *_args, **_kwargs: ({"alpha": ["alpha.txt"]}, [1]),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_collect_retrieval_outcomes",
        lambda *_args, **_kwargs: (outcomes, [12.3]),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "build_safe_retrieval_diagnostics",
        lambda _outcomes: diagnostics,
    )
    monkeypatch.setattr(
        acceptance_runner,
        "build_c4a_candidate_pool_diagnostics",
        lambda _outcomes: diagnostics,
    )
    monkeypatch.setattr(
        acceptance_runner,
        "build_c4a_aggregate_result",
        lambda *_args, **_kwargs: {
            "candidate_pool_expected_count": 30,
            "quality_gate_at_current_unfiltered_results": False,
        },
    )
    monkeypatch.setattr(
        acceptance_runner,
        "choose_reranker_score_threshold",
        lambda _outcomes: pytest.fail("C4-A 不得调用阈值标定"),
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_cleanup_seeded_scope",
        lambda *_args, **_kwargs: None,
    )

    diagnostic_file = tmp_path / "p14-c4a-diagnostic.json"
    result = acceptance_runner.run_retrieval_calibration(
        base_url="http://fixture",
        diagnostic_file=diagnostic_file,
        calibrate_threshold=False,
    )

    assert result["candidate_pool_expected_count"] == 30
    assert json.loads(diagnostic_file.read_text(encoding="utf-8"))["stage"] == (
        "c4_a_candidate_pool"
    )


def test_uploaded_document_is_added_to_cleanup_scope_before_parse() -> None:
    """上传后的后续步骤失败时，原文件也必须可被精确清理。"""
    script_path = Path(__file__).parents[2] / "scripts" / "archive_v1_p14_acceptance.py"
    source = script_path.read_text(encoding="utf-8")

    upload_position = source.index("document_id, _ = _document_id_and_version(uploaded")
    cleanup_tracking_position = source.index("seeded.append((project_id, document_id))")
    parse_position = source.index("parsed = _require_object(", upload_position)

    assert upload_position < cleanup_tracking_position < parse_position


def test_confirm_response_uses_the_actual_process_document_contract() -> None:
    """P09 确认路由只承诺状态和确认时间，不应假定未公开的索引计数字段。"""
    assert is_confirmed_document_response(
        {"status": "CONFIRMED", "confirmed_at": "2026-08-28T10:00:00Z"}
    )
    assert not is_confirmed_document_response(
        {"status": "CONFIRMED", "confirmed_at": None}
    )
    assert not is_confirmed_document_response({"status": "PARSED"})


def test_outer_cleanup_scope_is_passed_into_incremental_seed_workflow() -> None:
    """建档中途失败时，finally 必须看到已经创建的项目和文档。"""
    script_path = Path(__file__).parents[2] / "scripts" / "archive_v1_p14_acceptance.py"
    module = ast.parse(script_path.read_text(encoding="utf-8"))
    seed_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_seed_confirmed_documents"
    )
    assert {argument.arg for argument in seed_function.args.kwonlyargs} >= {
        "project_ids",
        "seeded",
    }
    calibration_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_retrieval_calibration"
    )
    seed_call = next(
        node
        for node in ast.walk(calibration_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_seed_confirmed_documents"
    )
    assert {keyword.arg for keyword in seed_call.keywords} >= {"project_ids", "seeded"}


def test_registered_user_id_remains_a_uuid_for_postgresql_cleanup_binding() -> None:
    """验收账号 ID 必须以 UUID 绑定 SQL，避免 PostgreSQL 的字符串比较歧义。"""
    user_id = parse_registered_user_id("12345678-1234-5678-1234-567812345678")

    assert isinstance(user_id, UUID)


def test_principal_cleanup_removes_sessions_before_user() -> None:
    """清理临时账号时必须先删除会话，避免用户删除被外键阻断。"""
    script_path = Path(__file__).parents[2] / "scripts" / "archive_v1_p14_acceptance.py"
    source = script_path.read_text(encoding="utf-8")

    session_delete = source.index("DELETE FROM auth_sessions WHERE user_id")
    user_delete = source.index("DELETE FROM users WHERE id", session_delete)

    assert session_delete < user_delete


def test_aggregate_result_file_contains_only_machine_readable_metrics(tmp_path: Path) -> None:
    """真实验收的聚合结论可写入临时文件，避免依赖终端输出。"""
    target = tmp_path / "p14-result.json"

    write_aggregate_result(target, {"passed": True, "distance_threshold": 0.24})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "distance_threshold": 0.24,
        "passed": True,
    }


def test_run_persists_aggregate_before_entering_cleanup_finally() -> None:
    """长时间验收被外部中断时，已得到的质量聚合指标不能因清理延后而丢失。"""
    script_path = Path(__file__).parents[2] / "scripts" / "archive_v1_p14_acceptance.py"
    source = script_path.read_text(encoding="utf-8")

    result_write = source.index("write_aggregate_result(result_file, result)")
    cleanup_finally = source.index("    finally:\n", result_write)

    assert result_write < cleanup_finally


def test_run_surfaces_cleanup_failure_after_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主验收失败时，清理失败也必须显式阻断，不能留下静默残留。"""

    class FakeApi:
        """隔离运行器控制流的最小 HTTP 客户端。"""

        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭连接池。"""

    user_id = UUID("12345678-1234-5678-1234-567812345678")

    def fail_seed(*_args: object, **_kwargs: object) -> object:
        """模拟阈值标定前的主验收异常。"""
        raise ValueError("主验收失败")

    def fail_cleanup(**_: object) -> None:
        """模拟跨存储清理失败。"""
        raise AcceptanceError("P14 虚构验收数据清理未全部完成。")

    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "p14be-fixture"),
    )
    monkeypatch.setattr(acceptance_runner, "_seed_confirmed_documents", fail_seed)
    monkeypatch.setattr(acceptance_runner, "_cleanup_seeded_scope", fail_cleanup)

    with pytest.raises(AcceptanceError, match="清理未全部完成"):
        acceptance_runner.run_retrieval_calibration(base_url="http://fixture")


def test_safe_diagnostic_omits_error_text_except_threshold_aggregates(tmp_path: Path) -> None:
    """诊断文件不能回写 HTTP 路径、资源标识或任何原文。"""
    target = tmp_path / "p14-diagnostic.json"

    write_safe_diagnostic(
        target,
        stage="threshold_calibration",
        error=ValueError("grounded=6/8，no_evidence=2/2，isolation=2/2"),
    )

    diagnostic = json.loads(target.read_text(encoding="utf-8"))
    assert diagnostic == {
        "error_type": "ValueError",
        "outcome": "failed",
        "stage": "threshold_calibration",
        "threshold_aggregate": "grounded=6/8，no_evidence=2/2，isolation=2/2",
    }


def test_safe_diagnostic_preserves_latency_p95_for_failed_quality_runs(
    tmp_path: Path,
) -> None:
    """质量门槛失败时仍应保存安全的 P95 延迟，便于比较单变量实验。"""
    target = tmp_path / "p14-diagnostic-latency.json"

    write_safe_diagnostic(
        target,
        stage="threshold_calibration",
        error=ValueError("grounded=6/8，no_evidence=2/2，isolation=2/2"),
        latency_p95_ms=123.45,
    )

    diagnostic = json.loads(target.read_text(encoding="utf-8"))
    assert diagnostic["latency_p95_ms"] == pytest.approx(123.45)


def test_safe_diagnostic_persists_only_the_sanitized_retrieval_diagnostics(
    tmp_path: Path,
) -> None:
    """阈值失败时应保留可行动的逐题指标，但不得写入原始候选。"""
    target = tmp_path / "p14-diagnostic-with-retrieval.json"
    diagnostics = build_safe_retrieval_diagnostics(
        [
            {
                "category": "NO_EVIDENCE",
                "project_id": "project-secret",
                "allowed_filenames": ["alpha-secret.txt"],
                "items": [
                    _item(
                        filename="alpha-secret.txt",
                        distance=0.12,
                        excerpt="不应写入的原文",
                    ) | {"reranker_score": 0.7}
                ],
            }
        ]
    )

    write_safe_diagnostic(
        target,
        stage="threshold_calibration",
        error=ValueError("grounded=6/8，no_evidence=2/2，isolation=2/2"),
        retrieval_diagnostics=diagnostics,
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["retrieval_diagnostics"] == diagnostics
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "project-secret" not in serialized
    assert "alpha-secret.txt" not in serialized
    assert "不应写入的原文" not in serialized


def test_safe_diagnostic_records_a_controlled_seed_code_without_http_path(tmp_path: Path) -> None:
    """建档失败只暴露预定义步骤代码，不暴露包含 UUID 的请求路径。"""
    target = tmp_path / "p14-seed-diagnostic.json"

    write_safe_diagnostic(
        target,
        stage="seed_confirmation",
        error=AcceptanceError(
            "internal detail",
            safe_code="SEED_CONFIRM",
            http_status=503,
            api_code="INDEX_FAILED",
        ),
    )

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "error_type": "AcceptanceError",
        "http_status": 503,
        "outcome": "failed",
        "api_code": "INDEX_FAILED",
        "safe_code": "SEED_CONFIRM",
        "stage": "seed_confirmation",
    }
