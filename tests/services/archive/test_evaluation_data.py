"""验证智慧档案 V1 虚构验收资料、标注和问题集的一致性。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.errors import AppError
from app.services.archive.parser import parse_archive_document


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_ROOT = PROJECT_ROOT / "tests" / "pytest_docs"
GENERATOR_SCRIPT = PROJECT_ROOT / "scripts" / "generate_archive_v1_eval_data.py"


@pytest.fixture(scope="session", autouse=True)
def ensure_local_evaluation_dataset() -> None:
    """缺少本地虚构资料时生成；已有资料只读取，便于人工检查。"""
    manifest_path = DATASET_ROOT / "labels" / "document-ground-truth.json"
    question_path = DATASET_ROOT / "labels" / "question-ground-truth.json"
    if manifest_path.is_file() and question_path.is_file():
        return

    result = subprocess.run(
        [sys.executable, str(GENERATOR_SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "无法生成本地虚构验收资料。"
        f" stdout={result.stdout!r}; stderr={result.stderr!r}"
    )


@pytest.fixture
def document_manifest() -> dict:
    """读取验收资料的人工字段与异常场景标注。"""
    return json.loads(
        (DATASET_ROOT / "labels" / "document-ground-truth.json").read_text(
            encoding="utf-8"
        )
    )


def test_normal_documents_cover_each_type_twice_and_all_parser_formats(
    document_manifest: dict,
) -> None:
    """12 份正常资料必须覆盖六类资料与四种 Parser 输入格式。"""
    normal_documents = document_manifest["normal_documents"]
    assert len(normal_documents) == 12
    assert {item["source_format"] for item in normal_documents} == {
        "PDF",
        "DOCX",
        "TXT",
        "MD",
    }
    for document_type in {
        "CONTRACT",
        "DESIGN",
        "CONSTRUCTION",
        "MEETING_MINUTES",
        "ACCEPTANCE",
        "OTHER",
    }:
        assert sum(
            item["expected_document_type"] == document_type
            for item in normal_documents
        ) >= 2


def test_ground_truth_evidence_exists_in_its_own_parsed_document(
    document_manifest: dict,
) -> None:
    """每一个可识别字段必须可在同项目、同文档的 Parser 片段中查证。"""
    for document in document_manifest["normal_documents"]:
        parsed = parse_archive_document(DATASET_ROOT / document["relative_path"])
        for field in document["expected_fields"].values():
            for evidence in field["evidence"]:
                assert any(
                    fragment.location_type.value == evidence["location_type"]
                    and fragment.location_start == evidence["location_start"]
                    and fragment.location_end == evidence["location_end"]
                    and evidence["excerpt"] in fragment.content
                    for fragment in parsed.fragments
                )


def test_anomaly_documents_cover_missing_ambiguous_duplicate_and_scanned(
    document_manifest: dict,
) -> None:
    """三份异常资料分别覆盖字段缺失/混淆、同哈希重复与扫描 PDF。"""
    anomalies = document_manifest["anomaly_documents"]
    assert len(anomalies) == 3
    assert {item["scenario"] for item in anomalies} == {
        "MISSING_FIELDS_AND_AMBIGUOUS_CLASSIFICATION",
        "EXACT_FILE_HASH_DUPLICATE",
        "SCANNED_PDF_NO_TEXT",
    }
    duplicate = next(item for item in anomalies if item["id"] == "X-02")
    original = next(
        item
        for item in document_manifest["normal_documents"]
        if item["id"] == duplicate["duplicate_of"]
    )
    assert duplicate["file_sha256"] == original["file_sha256"]

    scanned = next(item for item in anomalies if item["id"] == "X-03")
    with pytest.raises(AppError) as exc_info:
        parse_archive_document(DATASET_ROOT / scanned["relative_path"])
    assert exc_info.value.code == "SCANNED_PDF_UNSUPPORTED"


def test_question_set_has_expected_categories_answers_and_project_scope(
    document_manifest: dict,
) -> None:
    """问题集必须保持数量、标准答案和项目隔离 Ground Truth 的一致性。"""
    questions = json.loads(
        (DATASET_ROOT / "labels" / "question-ground-truth.json").read_text(
            encoding="utf-8"
        )
    )["questions"]
    assert len(questions) == 12
    assert sum(item["category"] == "GROUNDED" for item in questions) == 8
    assert sum(item["category"] == "NO_EVIDENCE" for item in questions) == 2
    assert sum(item["category"] == "ISOLATION" for item in questions) == 2
    assert all(
        item.get("expected_evidence") for item in questions if item["category"] == "GROUNDED"
    )
    documents_by_id = {
        item["id"]: item for item in document_manifest["normal_documents"]
    }
    for question in questions:
        if question["category"] == "GROUNDED":
            evidence = question["expected_evidence"]
            assert question["expected_answer"]
            assert documents_by_id[evidence["document_id"]]["project_id"] == question["project_id"]
        elif question["category"] == "NO_EVIDENCE":
            assert question["expected_answer"] is None
            assert "expected_evidence" not in question
        else:
            hidden_document = documents_by_id[question["hidden_evidence_in_other_project"]]
            assert question["expected_answer"] is None
            assert hidden_document["project_id"] != question["project_id"]
