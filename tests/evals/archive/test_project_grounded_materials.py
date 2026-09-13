"""验证以当前项目文档为素材的有据问答数据集契约。"""

import json
import re
from pathlib import Path

from pixie.harness.runner import load_dataset
from pixie.instrumentation.wrap import deserialize_wrap_data

from app.schemas.archive_retrieval import ArchiveRetrievalResponse


ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = (
    ROOT
    / "evals"
    / "archive"
    / "datasets"
    / "archive-question-project-grounded.json"
)
EXPECTED_EVALUATORS = {
    "evals/archive/evaluators.py:archive_answer_contract",
    "evals/archive/evaluators.py:archive_evidence_faithfulness",
    "evals/archive/evaluators.py:archive_refusal_quality",
    "evals/archive/evaluators.py:archive_v1_p02_quality_gate",
}
SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|access[_-]?token|refresh[_-]?token|api[_-]?key)"
)


def _raw_dataset() -> dict[str, object]:
    """按 UTF-8 读取原始数据，以便检查 Pixie 加载前的顶层契约。"""
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_project_grounded_dataset_has_four_unique_grounded_cases() -> None:
    """项目素材集应包含四条唯一且候选完整的有据问题。"""
    raw = _raw_dataset()
    entries = raw["entries"]

    assert len(entries) == 4
    metadata = [entry["eval_metadata"] for entry in entries]
    case_ids = [item["case_id"] for item in metadata]
    assert len(set(case_ids)) == 4
    assert all(item["category"] == "GROUNDED" for item in metadata)
    assert all(item["candidate_pool_complete"] is True for item in metadata)
    assert all(item["retrieval_latency_ms"] == 0 for item in metadata)
    assert set(raw["evaluators"]) == EXPECTED_EVALUATORS


def test_project_grounded_dataset_injects_complete_top8_retrieval() -> None:
    """每题应只接收问题，并注入可反序列化的完整 Top-8 检索响应。"""
    dataset = load_dataset(DATASET_PATH)

    for entry in dataset.entries:
        assert set(entry.input_data) == {"question"}
        assert len(entry.eval_input) == 1
        wrapped = entry.eval_input[0]
        assert wrapped.name == "archive_question_retrieval"
        retrieval = deserialize_wrap_data(wrapped.value)
        assert isinstance(retrieval, ArchiveRetrievalResponse)
        assert retrieval.requested_top_k == 8
        assert retrieval.returned_count == len(retrieval.items)
        assert retrieval.items


def test_project_grounded_expected_evidence_is_present_in_candidates() -> None:
    """确定性答案标注引用的文件、定位和摘录必须由注入候选直接支持。"""
    raw = _raw_dataset()

    for entry in raw["entries"]:
        metadata = entry["eval_metadata"]
        assert metadata["expected_answer"].strip()
        evidence = metadata["expected_evidence"]
        assert evidence["items"]
        retrieval = deserialize_wrap_data(entry["eval_input"][0]["value"])
        expected_filename = evidence["relative_path"].replace("\\", "/").rsplit("/", 1)[-1]
        for expected_item in evidence["items"]:
            assert any(
                candidate.filename == expected_filename
                and candidate.location_type.value == expected_item["location_type"]
                and candidate.location_start <= expected_item["location_start"]
                and candidate.location_end >= expected_item["location_end"]
                and expected_item["excerpt"] in candidate.excerpt
                for candidate in retrieval.items
            )


def test_project_grounded_dataset_has_diverse_sources_and_difficulty() -> None:
    """小集合也应覆盖多个事实来源，并限制 routine 样本占比。"""
    entries = _raw_dataset()["entries"]
    metadata = [entry["eval_metadata"] for entry in entries]
    source_documents = {item["source_document"] for item in metadata}
    difficulties = [item["difficulty"] for item in metadata]

    assert len(source_documents) >= 3
    assert difficulties.count("routine") / len(difficulties) <= 0.6
    assert "challenging" in difficulties


def test_project_grounded_dataset_has_distractor_and_no_sensitive_material() -> None:
    """至少一题应有相近干扰候选，数据集不得包含敏感字段或凭据。"""
    raw = _raw_dataset()
    candidate_counts = []
    for entry in raw["entries"]:
        retrieval = deserialize_wrap_data(entry["eval_input"][0]["value"])
        candidate_counts.append(len(retrieval.items))

    assert max(candidate_counts) >= 2
    assert SENSITIVE_PATTERN.search(json.dumps(raw, ensure_ascii=False)) is None


def _normalize_source_excerpt(value: str) -> str:
    """只折叠换行与空白，保留 Markdown 标记以阻止改写冒充原文。"""
    return re.sub(r"\s+", " ", value).strip()


def test_project_grounded_candidates_trace_to_declared_source_lines() -> None:
    """每个支持或干扰候选都必须逐字来自其声明源文件的定位范围。"""
    raw = _raw_dataset()

    for entry in raw["entries"]:
        metadata = entry["eval_metadata"]
        declared_sources = {metadata["source_document"]}
        distractor_source = metadata.get("distractor_source_document")
        if distractor_source is not None:
            declared_sources.add(distractor_source)
        sources_by_filename = {
            Path(source).name: ROOT / source for source in declared_sources
        }
        retrieval = deserialize_wrap_data(entry["eval_input"][0]["value"])
        for candidate in retrieval.items:
            source_path = sources_by_filename[candidate.filename]
            source_lines = source_path.read_text(encoding="utf-8").splitlines()
            located_text = "\n".join(
                source_lines[candidate.location_start - 1 : candidate.location_end]
            )
            assert _normalize_source_excerpt(candidate.excerpt) in _normalize_source_excerpt(
                located_text
            )
