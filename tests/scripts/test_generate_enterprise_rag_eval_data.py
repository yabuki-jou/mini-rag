"""验证企业规模 RAG 虚构资料生成器的确定性和可追溯性。"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from app.services.archive.evidence_matching import item_contains_expected_evidence
from app.services.archive.parser import parse_archive_document
from scripts.archive_v1_p14_acceptance import build_manual_field_payload
from scripts.generate_enterprise_rag_eval_data import generate_enterprise_eval_data


SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key\s*[=:]|secret\s*[=:]|password\s*[=:]|(?:bearer|authorization)\s*[:=]\s*[a-z0-9._-]{20,}|(?:sk|ghp|eyJ)-[a-z0-9._-]{16,})"
)


def test_enterprise_materials_have_scale_isolation_and_exact_evidence(tmp_path: Path) -> None:
    """资料必须达到规模门槛，且每条有据题可回查到精确行。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    documents = manifest["documents"]
    questions = manifest["questions"]

    assert len(manifest["projects"]) == 2
    assert len(documents) >= 10
    assert all(int(item["body_char_count"]) > len("证据事实：") for item in documents)
    assert {item["category"] for item in questions} == {
        "GROUNDED",
        "NO_EVIDENCE",
        "ISOLATION",
    }
    for category, count in manifest["question_counts"].items():
        assert count == sum(item["category"] == category for item in questions)
        assert count >= 1

    documents_by_id = {item["id"]: item for item in documents}
    for question in questions:
        if question["category"] != "GROUNDED":
            continue
        evidence = question["expected_evidence"]
        document = documents_by_id[evidence["document_id"]]
        assert document["project_id"] == question["project_id"]
        lines = (tmp_path / "enterprise" / document["relative_path"]).read_text(
            encoding="utf-8"
        ).splitlines() if document["format"] != "PDF" else None
        location = evidence["items"][0]
        if document["format"] == "PDF":
            parsed = parse_archive_document(
                tmp_path / "enterprise" / document["relative_path"]
            )
            assert location["location_type"] == "PDF_PAGE"
            assert any(location["excerpt"] in fragment.content for fragment in parsed.fragments)
        else:
            start = location["location_start"]
            end = location["location_end"]
            assert "\n".join(lines[start - 1 : end]) == location["excerpt"]
            assert location["excerpt"] in "\n".join(lines)


def test_enterprise_materials_record_existing_sources_and_no_sensitive_patterns(
    tmp_path: Path,
) -> None:
    """来源必须指向当前仓库已有文档/源码，输出不能带敏感配置模式。"""
    output_root = tmp_path / "enterprise"
    manifest = generate_enterprise_eval_data(output_root)
    project_root = Path(__file__).parents[2]

    source_files = {
        source
        for document in manifest["documents"]
        for source in document["source_files"]
    }
    assert source_files
    assert all((project_root / source).is_file() for source in source_files)
    assert all(
        not any(part.lower() in {".env", "data", "logs"} for part in Path(source).parts)
        for source in source_files
    )

    for path in output_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}:
            assert not SENSITIVE_PATTERN.search(path.read_text(encoding="utf-8"))

    label_path = output_root / "labels" / "enterprise-ground-truth.json"
    assert json.loads(label_path.read_text(encoding="utf-8")) == manifest


def test_enterprise_generation_is_byte_deterministic(tmp_path: Path) -> None:
    """相同源码版本下重复生成必须产生相同材料和标注。"""
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    generate_enterprise_eval_data(first_root)
    generate_enterprise_eval_data(second_root)

    first_files = sorted(path.relative_to(first_root) for path in first_root.rglob("*"))
    second_files = sorted(path.relative_to(second_root) for path in second_root.rglob("*"))
    assert first_files == second_files
    for relative_path in first_files:
        first = first_root / relative_path
        second = second_root / relative_path
        if first.is_file():
            assert first.read_bytes() == second.read_bytes()


def test_enterprise_document_body_uses_declared_markdown_sources_without_template_repeats(
    tmp_path: Path,
) -> None:
    """正文应主要来自各自声明的 tracked Markdown 来源，而不是重复模板。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    project_root = Path(__file__).parents[2]
    source_texts = {
        source: (project_root / source).read_text(encoding="utf-8")
        for source in {
            source
            for document in manifest["documents"]
            for source in document["source_files"]
        }
        if source.lower().endswith(".md")
    }
    assert len(source_texts) >= 10
    for document in manifest["documents"]:
        document_path = tmp_path / "enterprise" / document["relative_path"]
        if document["format"] == "PDF":
            parsed = parse_archive_document(document_path)
            content = "\n".join(fragment.content for fragment in parsed.fragments)
            for source, digest in document["source_sha256"].items():
                assert hashlib.sha256((project_root / source).read_bytes()).hexdigest() == digest
            assert len(content) >= 300
            continue
        content = document_path.read_text(encoding="utf-8")
        body = content.split("证据事实：", 1)[1].split("\n", 1)[1]
        assert any(source_text in body for source_text in source_texts.values())
        assert "本节用于复测长文本切分和跨段检索" not in content
        paragraphs = [
            paragraph.strip()
            for paragraph in body.split("\n\n")
            if len(paragraph.strip()) >= 200
        ]
        assert len(paragraphs) == len(set(paragraphs))


def test_grounded_expected_evidence_uses_shared_items_contract(tmp_path: Path) -> None:
    """每条有据题的标注必须能被正式检索共享匹配器识别。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    grounded_questions = [
        question
        for question in manifest["questions"]
        if question["category"] == "GROUNDED"
    ]

    for question in grounded_questions:
        evidence = question["expected_evidence"]
        assert isinstance(evidence.get("items"), list)
        assert evidence["items"]
        location = evidence["items"][0]
        item = {
            "filename": Path(evidence["relative_path"]).name,
            "location_type": location["location_type"],
            "location_start": location["location_start"],
            "location_end": location["location_end"],
            "excerpt": location["excerpt"],
        }
        assert item_contains_expected_evidence(item, evidence)


def test_grounded_08_expected_answer_only_constrains_the_first_step(
    tmp_path: Path,
) -> None:
    """GROUNDED-08 的问题只询问解析服务的第一步。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    question = next(
        item for item in manifest["questions"] if item["id"] == "GROUNDED-08"
    )

    assert question["expected_answer"] == "解析服务先识别文件格式。"


def test_enterprise_materials_include_rich_parseable_pdf_with_pdf_page_evidence(
    tmp_path: Path,
) -> None:
    """资料应包含多页可提取 PDF，且 PDF Ground Truth 使用 PDF_PAGE 定位。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    pdf_documents = [
        document for document in manifest["documents"] if document["format"] == "PDF"
    ]
    assert pdf_documents

    for document in pdf_documents:
        path = tmp_path / "enterprise" / document["relative_path"]
        parsed = parse_archive_document(path)
        assert len(parsed.fragments) >= 2
        assert sum(len(fragment.content) for fragment in parsed.fragments) >= 300
        assert document["evidence"]["location_type"] == "PDF_PAGE"
        grounded = next(
            question
            for question in manifest["questions"]
            if question["category"] == "GROUNDED"
            and question["expected_evidence"]["document_id"] == document["id"]
        )
        evidence = grounded["expected_evidence"]
        assert evidence["items"][0]["location_type"] == "PDF_PAGE"
        item = {
            "filename": path.name,
            "location_type": "PDF_PAGE",
            "location_start": evidence["items"][0]["location_start"],
            "location_end": evidence["items"][0]["location_end"],
            "excerpt": evidence["items"][0]["excerpt"],
        }
        assert item_contains_expected_evidence(item, evidence)


def test_enterprise_documents_have_confirmable_required_fields_and_matching_evidence(
    tmp_path: Path,
) -> None:
    """每份资料都必须能通过现有人工字段确认状态机。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    required = {"TITLE", "DOCUMENT_TYPE", "PROJECT_STAGE"}
    all_fields = {
        "TITLE",
        "DOCUMENT_TYPE",
        "DOCUMENT_DATE",
        "AUTHORING_ORGANIZATION",
        "VERSION_NUMBER",
        "PROJECT_STAGE",
        "KEYWORDS",
    }
    for document in manifest["documents"]:
        fields = document["expected_fields"]
        assert set(fields) == all_fields
        assert required <= set(fields)
        assert fields["DOCUMENT_TYPE"]["value"] in {
            "CONTRACT",
            "DESIGN",
            "CONSTRUCTION",
            "MEETING_MINUTES",
            "ACCEPTANCE",
            "OTHER",
        }
        assert fields["PROJECT_STAGE"]["value"] in {
            "PREPARATION",
            "DESIGN",
            "CONSTRUCTION",
            "CROSS_STAGE",
            "ACCEPTANCE",
            "OTHER_STAGE",
        }
        parsed = parse_archive_document(
            tmp_path / "enterprise" / document["relative_path"]
        )
        for field in fields.values():
            evidence = field["evidence"][0]
            assert any(
                fragment.location_type.value == evidence["location_type"]
                and fragment.location_start == evidence["location_start"]
                and fragment.location_end == evidence["location_end"]
                and evidence["excerpt"] in fragment.content
                for fragment in parsed.fragments
            )
        for field_name, field_spec in fields.items():
            payload = build_manual_field_payload(
                field_name, field_spec, expected_version=1
            )
            assert payload["review_status"] == "VALUE_CONFIRMED"
            assert payload["evidences"] == field_spec["evidence"]


def test_isolation_questions_use_project_unique_hidden_facts(tmp_path: Path) -> None:
    """隔离题的隐藏答案必须是另一项目专属事实，当前项目正文不能复述。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    documents = {document["id"]: document for document in manifest["documents"]}
    isolation_questions = [
        question
        for question in manifest["questions"]
        if question["category"] == "ISOLATION"
    ]

    assert len(isolation_questions) == 4
    assert len(
        {
            question["hidden_evidence_in_other_project"]["expected_answer"]
            for question in isolation_questions
        }
    ) == 4
    for question in isolation_questions:
        hidden = question["hidden_evidence_in_other_project"]
        hidden_document = documents[hidden["document_id"]]
        assert hidden_document["project_id"] == hidden["project_id"]
        assert hidden_document["project_id"] != question["project_id"]
        assert hidden["items"] == [hidden_document["evidence"]]
        assert hidden["items"][0]["excerpt"] == (
            f"证据事实：{hidden['expected_answer']}"
        )
        assert all(
            fragment in hidden["expected_answer"]
            and fragment in hidden["items"][0]["excerpt"]
            for fragment in hidden["expected_answer_fragments"]
        )

        current_documents = [
            document
            for document in manifest["documents"]
            if document["project_id"] == question["project_id"]
        ]
        forbidden_fragments = hidden["expected_answer_fragments"]
        for document in current_documents:
            content = (tmp_path / "enterprise" / document["relative_path"]).read_bytes()
            assert all(fragment.encode("utf-8") not in content for fragment in forbidden_fragments)


def test_enterprise_grounded_fragments_cover_observed_false_negatives(
    tmp_path: Path,
) -> None:
    """实际假阴性问题必须保存保守的必要事实片段。"""
    manifest = generate_enterprise_eval_data(tmp_path / "enterprise")
    questions = {
        question["id"]: question
        for question in manifest["questions"]
        if question["category"] == "GROUNDED"
    }

    assert {
        case_id: questions[case_id]["expected_answer_fragments"]
        for case_id in (
            "GROUNDED-04",
            "GROUNDED-06",
            "GROUNDED-10",
            "GROUNDED-11",
            "GROUNDED-12",
        )
    } == {
        "GROUNDED-04": ["Top-8", "候选"],
        "GROUNDED-06": ["Chroma", "user_id", "kb_id"],
        "GROUNDED-10": ["原文件", "PostgreSQL", "Chroma Chunk", "document_id", "关联"],
        "GROUNDED-11": ["10条"],
        "GROUNDED-12": ["document_id"],
    }
