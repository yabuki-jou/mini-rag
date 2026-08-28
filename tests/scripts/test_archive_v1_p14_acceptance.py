"""验证 P14 固定题集检索评分和阈值选择规则。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import UUID

import pytest

from scripts.archive_v1_p14_acceptance import (
    AcceptanceError,
    build_manual_field_payload,
    choose_distance_threshold,
    is_confirmed_document_response,
    parse_registered_user_id,
    score_at_no_evidence_ceiling,
    item_contains_expected_evidence,
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


def test_expected_evidence_requires_filename_location_and_excerpt() -> None:
    """标准证据必须同时满足文件、定位范围与摘录，不能仅按文件名误判。"""
    expected = _grounded(distance=0.2)["expected_evidence"]

    assert item_contains_expected_evidence(
        _item(filename="alpha.txt", distance=0.2), expected
    )
    assert not item_contains_expected_evidence(
        _item(filename="alpha.txt", distance=0.2, excerpt="不同内容"), expected
    )


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
