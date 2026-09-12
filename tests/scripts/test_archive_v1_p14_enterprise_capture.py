"""验证 P14 企业规模捕获阶段的动态输入和输出保护。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.archive_v1_p14_acceptance as acceptance_runner


def _labels_and_outcomes() -> tuple[dict[str, object], list[dict[str, object]]]:
    questions: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    for index in range(7):
        category = (
            "GROUNDED"
            if index < 3
            else "NO_EVIDENCE"
            if index < 5
            else "ISOLATION"
        )
        case_id = f"{category}-{index + 1:02d}"
        question: dict[str, object] = {
            "id": case_id,
            "category": category,
            "project_id": "enterprise-a",
            "question": f"问题 {index + 1}",
            "expected_answer": "答案" if category == "GROUNDED" else None,
        }
        if category == "GROUNDED":
            question["expected_evidence"] = {
                "document_id": f"doc-{index}",
                "relative_path": f"documents/doc-{index}.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 3,
                        "location_end": 3,
                        "excerpt": "答案",
                    }
                ],
            }
            question["expected_answer_fragments"] = ["答案"]
        if category == "ISOLATION":
            question["hidden_evidence_in_other_project"] = {
                "document_id": "hidden-doc",
                "relative_path": "documents/hidden.txt",
            }
        questions.append(question)
        item: dict[str, object] = {
            "filename": f"doc-{index}.txt",
            "location_type": "TEXT_LINE_RANGE",
            "location_start": 3,
            "location_end": 3,
            "excerpt": "答案",
            "score": 0.9,
        }
        outcomes.append(
            {
                "case_id": case_id,
                "category": category,
                "items": [item],
                "allowed_filenames": [f"doc-{index}.txt"],
                "diagnostic_candidate_count": 30,
                "diagnostic_chroma_candidate_count": 30,
                "retrieval_requested_top_k": 8,
                "retrieval_returned_count": 1,
                "retrieval_latency_ms": 1.0,
            }
        )
    return {"dataset_id": "enterprise", "questions": questions}, outcomes


def test_enterprise_capture_dataset_uses_dynamic_question_count_and_top8() -> None:
    """企业捕获必须按输入问题数量工作，并保留真实 Top-8 请求结果。"""
    labels, outcomes = _labels_and_outcomes()

    dataset = acceptance_runner.build_enterprise_capture_dataset(
        labels, outcomes, top_k=8
    )

    assert dataset["name"] == "archive-question-enterprise-captured"
    assert len(dataset["entries"]) == 7
    assert all(entry["input_data"].keys() == {"question"} for entry in dataset["entries"])
    assert all(
        entry["eval_input"][0]["value"]["requested_top_k"] == 8
        for entry in dataset["entries"]
    )
    assert all(
        "expected_evidence" in entry["eval_metadata"]
        for entry in dataset["entries"][:3]
    )
    assert all(
        entry["eval_metadata"]["expected_answer_fragments"] == ["答案"]
        for entry in dataset["entries"][:3]
    )


def test_enterprise_capture_output_cannot_overwrite_inputs_or_other_outputs(
    tmp_path: Path,
) -> None:
    """自定义评测根目录和捕获输出不能覆盖输入标注或其他输出。"""
    labels, outcomes = _labels_and_outcomes()
    label_root = tmp_path / "enterprise"
    (label_root / "labels").mkdir(parents=True)
    (label_root / "labels" / "question-ground-truth.json").write_text(
        json.dumps(labels), encoding="utf-8"
    )
    (label_root / "labels" / "document-ground-truth.json").write_text(
        json.dumps({"dataset_id": "enterprise", "documents": []}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="覆盖"):
        acceptance_runner.run_retrieval_calibration(
            base_url="http://fixture",
            evaluation_root=label_root,
            phase="enterprise-capture",
            enterprise_dataset_file=label_root / "labels" / "question-ground-truth.json",
        )


def test_enterprise_capture_phase_reuses_seed_and_requests_top8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """独立企业阶段复用建档/检索控制流，并把 Top-8 写入捕获结果。"""
    labels, outcomes = _labels_and_outcomes()
    captured: dict[str, object] = {}

    class FakeApi:
        headers: dict[str, str] = {}

        def close(self) -> None:
            """模拟关闭验收客户端。"""

    user_id = "12345678-1234-5678-1234-567812345678"
    monkeypatch.setattr(acceptance_runner, "P14Api", lambda _: FakeApi())
    monkeypatch.setattr(
        acceptance_runner,
        "_read_label",
        lambda path: labels if "question" in str(path) else {"dataset_id": "enterprise"},
    )
    monkeypatch.setattr(
        acceptance_runner,
        "_register_and_login",
        lambda *_args, **_kwargs: (user_id, "enterprise-fixture"),
    )

    def fake_seed(_api: object, *, project_ids: dict[str, str], **_: object):
        project_ids.update({"enterprise-a": "project-a"})
        return {"enterprise-a": ["doc.txt"]}, [1]

    def fake_collect(_api: object, *, top_k: int, **_: object):
        captured["top_k"] = top_k
        return outcomes, [1.0] * len(outcomes)

    monkeypatch.setattr(acceptance_runner, "_seed_confirmed_documents", fake_seed)
    monkeypatch.setattr(acceptance_runner, "_collect_retrieval_outcomes", fake_collect)
    monkeypatch.setattr(
        acceptance_runner, "_cleanup_seeded_scope", lambda *_args, **_kwargs: None
    )

    output_file = tmp_path / "enterprise-capture.json"
    result = acceptance_runner.run_retrieval_calibration(
        base_url="http://fixture",
        evaluation_root=tmp_path / "enterprise",
        enterprise_dataset_file=output_file,
        phase="enterprise-capture",
    )

    assert captured["top_k"] == 8
    assert result["question_count"] == 7
    assert json.loads(output_file.read_text(encoding="utf-8"))["name"] == (
        "archive-question-enterprise-captured"
    )
