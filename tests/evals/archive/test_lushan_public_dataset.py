"""验证芦山公开项目资料的 FR-042 Pixie smoke 数据集契约。"""

import json
import re
from pathlib import Path

from pixie.harness.runner import load_dataset
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = ROOT / "pixie_qa" / "datasets" / "lushan-p153548-smoke.json"
PROJECT_ROOT = ROOT / "tests" / "pytest_docs" / "public_projects" / "lushan_earthquake_p153548"
MANIFEST_PATH = PROJECT_ROOT / "metadata" / "manifest.json"
SOURCE_MARKER = "WORLD_BANK_P153548_PUBLIC"
DEFAULT_EVALUATOR = "pixie_qa/evaluators.py:archive_agent_trace_privacy"
ENTRY_EVALUATORS = [
    "...",
    "pixie_qa/evaluators.py:archive_agent_structural_contract",
    "pixie_qa/evaluators.py:archive_agent_tool_path_quality",
    "pixie_qa/evaluators.py:archive_agent_evidence_faithfulness",
]
SENSITIVE_PATTERN = re.compile(
    r"(?i)(document_id|chunk_id|session_id|thread_id|owner_id|user_id|"
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret|bearer\s+|"
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b)"
)


def _raw_dataset() -> dict[str, object]:
    """读取原始 JSON，检查文件顶层契约和真实资料绑定关系。"""
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _manifest() -> dict[str, object]:
    """读取本地公开资料清单，作为文件名和页码的唯一边界。"""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _wraps(entry: dict[str, object]) -> dict[str, object]:
    """把 Pixie 输入观测按名称展开，便于逐轮检查调用槽位。"""
    return {item["name"]: item["value"] for item in entry["eval_input"]}


def _evidence_items(entry: dict[str, object]) -> list[dict[str, object]]:
    """收集所有轮次的证据候选，不依赖生产 Agent 的运行结果。"""
    return [
        item
        for wrapped in entry["eval_input"]
        if wrapped["name"].startswith("archive_agent_evidence_retrieval")
        for item in wrapped["value"]
    ]


def _normalize_pdf_text(value: str) -> str:
    """折叠换行和 PDF 排版插入的标点空格，但不改写证据词语。"""
    normalized = re.sub(r"\s+", " ", value).strip()
    normalized = re.sub(r"\s+([.,;:])", r"\1", normalized)
    return normalized.replace("T he", "The")


def test_lushan_dataset_loads_with_five_required_cases() -> None:
    """公开资料 smoke 集应能被 Pixie 加载并覆盖三类核心案例。"""
    dataset = load_dataset(DATASET_PATH)
    raw = _raw_dataset()

    assert dataset.entries
    assert len(dataset.entries) == 5
    assert len(raw["entries"]) == 5
    categories = [entry["eval_metadata"]["category"] for entry in raw["entries"]]
    difficulties = [entry["eval_metadata"]["difficulty"] for entry in raw["entries"]]
    assert {"GROUNDED", "NO_EVIDENCE", "MULTI_TURN"}.issubset(categories)
    assert difficulties.count("routine") == 2
    assert difficulties.count("challenging") == 3
    assert raw["evaluators"] == [DEFAULT_EVALUATOR]
    assert all(
        entry["eval_metadata"]["source_provenance"] == SOURCE_MARKER
        for entry in raw["entries"]
    )
    for entry in raw["entries"]:
        expected = ENTRY_EVALUATORS.copy()
        if entry["eval_metadata"]["category"] == "MULTI_TURN":
            expected.append(
                "pixie_qa/evaluators.py:archive_agent_multiturn_freshness"
            )
        assert entry["evaluators"] == expected


def test_lushan_dataset_provides_all_possible_archive_wrap_slots() -> None:
    """每个案例应预置其单轮或双轮最多可能触发的目录与证据观测。"""
    for entry in _raw_dataset()["entries"]:
        metadata = entry["eval_metadata"]
        turn_count = metadata["expected_turn_count"]
        expected_suffixes = ["", "__2"] if turn_count == 1 else ["", "__2", "__3", "__4"]
        wraps = _wraps(entry)
        for suffix in expected_suffixes:
            catalog = wraps[f"archive_agent_catalog_result{suffix}"]
            assert f"archive_agent_evidence_retrieval{suffix}" in wraps
            assert catalog == {
                "py/object": "app.services.archive.catalog.AgentCatalogPage",
                "page": 1,
                "page_size": 20,
                "total": 0,
                "items": [],
            }
        assert len(entry["input_data"]["messages"]) == turn_count


def test_lushan_evidence_points_to_manifest_files_and_valid_pdf_pages() -> None:
    """证据候选的文件名、PDF 页码和摘录必须可回溯到本地真实 PDF。"""
    manifest = _manifest()
    documents = {
        Path(document["path"]).name: document
        for document in manifest["documents"]
    }

    for entry in _raw_dataset()["entries"]:
        for item in _evidence_items(entry):
            filename = item["filename"]
            assert filename in documents
            assert item["location_type"] == "PDF_PAGE"
            page_start = item["location_start"]
            page_end = item["location_end"]
            assert 1 <= page_start <= page_end <= documents[filename]["pages"]
            source_path = PROJECT_ROOT / next(
                document["path"]
                for document in manifest["documents"]
                if Path(document["path"]).name == filename
            )
            reader = PdfReader(source_path)
            extracted = "\n".join(
                (reader.pages[page - 1].extract_text() or "")
                for page in range(page_start, page_end + 1)
            )
            assert 150 <= len(_normalize_pdf_text(item["excerpt"])) <= 600
            assert _normalize_pdf_text(item["excerpt"]) in _normalize_pdf_text(extracted)


def test_lushan_dataset_has_expected_answers_and_public_only_trace_shape() -> None:
    """固定事实、拒答、多轮标注和脱敏边界应完整存在。"""
    raw = _raw_dataset()
    entries = {entry["eval_metadata"]["case_id"]: entry for entry in raw["entries"]}

    assert entries["LUSHAN-01"]["eval_metadata"]["expected_answer_fragments"] == [["US$300 million"]]
    assert entries["LUSHAN-02"]["eval_metadata"]["expected_answer_fragments"] == [["December 31, 2023"]]
    assert entries["LUSHAN-03"]["eval_metadata"]["expected_answer_fragments"] == [["Satisfactory"]]
    assert entries["LUSHAN-04"]["eval_metadata"]["expected_answer_statuses"] == [
        "REFUSED_NO_EVIDENCE"
    ]
    assert entries["LUSHAN-05"]["eval_metadata"]["expected_answer_fragments"] == [
        ["Highly Satisfactory"],
        ["Satisfactory"],
    ]
    assert entries["LUSHAN-05"]["eval_metadata"]["expected_turn_count"] == 2
    assert all(
        "pixie_qa/evaluators.py:archive_agent_multiturn_freshness" in entry["evaluators"]
        for entry in raw["entries"]
        if entry["eval_metadata"]["category"] == "MULTI_TURN"
    )

    refusal_items = _evidence_items(entries["LUSHAN-04"])
    assert {
        item["filename"] for item in refusal_items
    } == {
        "2016_project_appraisal_document.pdf",
        "2022_procurement_plan.pdf",
        "2024_completion_report.pdf",
    }
    assert all("手机号" not in item["excerpt"] for item in refusal_items)

    capabilities = {
        capability
        for entry in raw["entries"]
        for capability in entry["eval_metadata"]["capabilities"]
    }
    assert {
        "evidence_qa",
        "citations",
        "refusal",
        "multi_turn",
        "distractor_discrimination",
    }.issubset(capabilities)

    serialized = json.dumps(raw, ensure_ascii=False)
    assert SENSITIVE_PATTERN.search(serialized) is None
    assert '"score"' not in serialized
    assert all(
        item["document_ref"].startswith("D")
        for entry in raw["entries"]
        for item in _evidence_items(entry)
    )
