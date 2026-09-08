"""验证正式 D5 Pixie 结果汇总 CLI。"""

import json
from pathlib import Path

import pytest


def _write_entry(root: Path, index: int, *, pending: bool = False) -> None:
    entry = root / "dataset-0" / f"entry-{index}"
    entry.mkdir(parents=True, exist_ok=True)
    category = ("GROUNDED" if index < 8 else "NO_EVIDENCE" if index < 10 else "ISOLATION")
    metadata = {"case_id": f"{category}-{index + 1:02d}", "category": category,
                "candidate_pool_complete": True, "retrieval_latency_ms": 10 + index,
                "expected_answer": "秘密答案", "expected_evidence": {"secret": "不得输出"}}
    (entry / "config.json").write_text(json.dumps({"evalMetadata": metadata}), encoding="utf-8")
    response = {"answer_status": "ANSWERED", "answer": "秘密答案", "citations": []}
    if category != "GROUNDED":
        response = {"answer_status": "REFUSED_NO_EVIDENCE", "answer": "正式档案中没有足够依据。", "citations": []}
    (entry / "eval-output.jsonl").write_text(
        json.dumps({"name": "archive_question_response", "value": response}) + "\n", encoding="utf-8"
    )
    score = {"evaluator": "archive_v1_p02_quality_gate", "score": 1.0}
    if pending:
        score = {"evaluator": "archive_v1_p02_quality_gate", "status": "pending"}
    (entry / "evaluations.jsonl").write_text(json.dumps(score) + "\n", encoding="utf-8")


def test_summary_requires_exactly_one_dataset_and_twelve_entries(tmp_path: Path) -> None:
    from scripts.p14_d5_pixie_result_summary import summarize_result_directory

    for index in range(12):
        _write_entry(tmp_path, index)
    output = tmp_path / "summary.json"
    result = summarize_result_directory(tmp_path, output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert result["quality_gate_passed"] is False
    assert data["question_count"] == 12
    assert "expected_answer" not in output.read_text(encoding="utf-8")
    assert "秘密答案" not in output.read_text(encoding="utf-8")
    assert set(data["entries"][0]) <= {"case_id", "category", "entry_passed", "candidate_pool_complete", "retrieval_latency_ms", "answer_status", "answer_contains_expected", "citation_matches_expected"}


def test_pending_quality_gate_fails_stably(tmp_path: Path) -> None:
    from scripts.p14_d5_pixie_result_summary import summarize_result_directory

    for index in range(12):
        _write_entry(tmp_path, index, pending=index == 0)
    with pytest.raises(ValueError, match="pending"):
        summarize_result_directory(tmp_path, tmp_path / "summary.json")
