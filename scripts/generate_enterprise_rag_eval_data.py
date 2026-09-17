"""生成企业规模 RAG 真实链路复测所需的确定性虚构资料。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import textwrap
from typing import Any

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tests" / "enterprise_rag_eval"
TEXT_FORMATS = ("md", "txt")
PDF_DOCUMENT_IDS = {"A-03", "A-06", "B-02", "B-05"}
DOCUMENT_TYPES = (
    "CONTRACT",
    "DESIGN",
    "CONSTRUCTION",
    "MEETING_MINUTES",
    "ACCEPTANCE",
    "OTHER",
)
PROJECT_STAGES = (
    "PREPARATION",
    "DESIGN",
    "CONSTRUCTION",
    "CROSS_STAGE",
    "ACCEPTANCE",
    "OTHER_STAGE",
)
PDF_FONT_NAME = "STSong-Light"
FORBIDDEN_SOURCE_PARTS = {".env", "data", "logs", "results", "uploads", "upload"}

PROJECT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "enterprise-a",
        "name": "星河企业档案治理项目",
        "description": "面向企业文档归档、确认和只读检索的虚构项目资料。",
        "source_files": (
            "docs/design/需求说明.md",
            "docs/design/技术架构.md",
            "docs/decisions.md",
            "app/services/archive/retrieval.py",
            "app/services/archive/questions.py",
        ),
    },
    {
        "id": "enterprise-b",
        "name": "云港企业知识服务项目",
        "description": "面向项目范围、解析、索引和评测治理的虚构项目资料。",
        "source_files": (
            "docs/design/数据库设计.md",
            "docs/design/接口设计.md",
            "docs/implementation/智慧档案V1实施计划.md",
            "docs/stage/handoff.md",
            "app/services/archive/parser.py",
            "app/services/archive/indexing.py",
        ),
    },
)


FACT_SPECS: tuple[dict[str, Any], ...] = (
    {"project_id": "enterprise-a", "document_id": "A-01", "topic": "业务隔离", "answer": "业务隔离边界由 Project 承担。", "question": "星河项目的业务隔离边界由哪个实体承担？"},
    {"project_id": "enterprise-a", "document_id": "A-02", "topic": "知识库职责", "answer": "KnowledgeBase 只承担内部检索范围。", "question": "星河项目中 KnowledgeBase 的职责是什么？"},
    {"project_id": "enterprise-a", "document_id": "A-03", "topic": "人工确认", "answer": "只有人工确认后的 Final Chunk 才能进入正式检索。", "question": "星河项目什么条件下的 Final Chunk 才能进入正式检索？"},
    {"project_id": "enterprise-a", "document_id": "A-04", "topic": "问答候选", "answer": "档案问答内部保留 Top-8 候选。", "question": "星河项目档案问答内部保留多少条候选证据？", "answer_fragments": ["Top-8", "候选"]},
    {"project_id": "enterprise-a", "document_id": "A-05", "topic": "身份校验", "answer": "受保护接口必须从已验证 Bearer Access Token 获取用户身份。", "question": "星河项目受保护接口从哪里获取用户身份？"},
    {"project_id": "enterprise-a", "document_id": "A-06", "topic": "范围过滤", "answer": "Chroma 检索必须包含服务端确定的 user_id 和 kb_id。", "question": "星河项目 Chroma 检索必须包含哪些服务端范围条件？", "answer_fragments": ["Chroma", "user_id", "kb_id"]},
    {"project_id": "enterprise-a", "document_id": "A-07", "topic": "评测边界", "answer": "Ground Truth 只保留在离线评测元数据中。", "question": "星河项目的 Ground Truth 应保留在哪里？"},
    {"project_id": "enterprise-b", "document_id": "B-01", "topic": "解析入口", "answer": "解析服务先识别文件格式，再生成结构化片段。", "question": "云港项目解析服务的第一步是什么？"},
    {"project_id": "enterprise-b", "document_id": "B-02", "topic": "草稿状态", "answer": "AI 只能生成草稿，不能跳过人工确认进入正式索引。", "question": "云港项目 AI 草稿能否直接进入正式索引？"},
    {"project_id": "enterprise-b", "document_id": "B-03", "topic": "原文链路", "answer": "原文件、PostgreSQL 文档和 Chroma Chunk 使用同一个 document_id 关联。", "question": "云港项目如何关联原文件、业务文档和向量片段？", "answer_fragments": ["原文件", "PostgreSQL", "Chroma Chunk", "document_id", "关联"]},
    {"project_id": "enterprise-b", "document_id": "B-04", "topic": "检索响应", "answer": "公开检索接口最多返回 10 条候选。", "question": "云港项目公开检索接口最多返回多少条候选？", "answer_fragments": ["10条"]},
    {"project_id": "enterprise-b", "document_id": "B-05", "topic": "删除范围", "answer": "删除文档时 Chroma 条件必须额外包含 document_id。", "question": "云港项目删除文档时 Chroma 条件还必须包含什么？", "answer_fragments": ["document_id"]},
)


SUPPORTING_DOCS: tuple[dict[str, Any], ...] = (
    {"project_id": "enterprise-a", "document_id": "A-11", "topic": "归档审批", "answer": "星河项目归档审批单号为 XH-ARCH-2026-041，审批结论为通过。", "answer_fragments": ["XH-ARCH-2026-041", "审批结论为通过"]},
    {"project_id": "enterprise-a", "document_id": "A-12", "topic": "最终验收", "answer": "星河项目最终验收编号为 XH-ACCEPT-2026-0817，日期为 2026-08-17，责任单位为星河档案治理办公室。", "answer_fragments": ["2026-08-17", "星河档案治理办公室"]},
    {"project_id": "enterprise-b", "document_id": "B-11", "topic": "责任审批", "answer": "云港项目责任单位为云港知识治理中心，审批编号为 YG-OWNER-2026-073。", "answer_fragments": ["YG-OWNER-2026-073", "云港知识治理中心"]},
    {"project_id": "enterprise-b", "document_id": "B-12", "topic": "上线审批", "answer": "云港项目上线审批编号为 YG-APPROVAL-2026-0903，结论为有条件通过，签发日期为 2026-09-03。", "question": "云港项目上线审批编号、结论及签发日期是什么？", "answer_fragments": ["YG-APPROVAL-2026-0903", "有条件通过"]},
)

SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    "A-01": ("docs/design/需求说明.md",),
    "A-02": ("docs/design/技术架构.md",),
    "A-03": ("docs/decisions.md",),
    "A-04": ("README.md",),
    "A-05": ("docs/design/接口设计.md",),
    "A-06": ("docs/design/数据库设计.md",),
    "A-07": ("docs/implementation/智慧档案V1实施计划.md",),
    "A-11": ("docs/codebase/架构概览.md",),
    "A-12": ("docs/archive/review/接口设计评审.md",),
    "B-01": (
        "docs/implementation/智慧档案V1实施计划.md",
        "docs/design/智慧档案V1解析器设计.md",
    ),
    "B-02": ("docs/review/验收报告.md",),
    "B-03": (
        "docs/design/数据库设计.md",
        "docs/design/Chroma迁移决策.md",
    ),
    "B-04": ("docs/design/接口设计.md",),
    "B-05": ("docs/stage/handoff.md",),
    "B-11": ("docs/archive/review/接口设计补丁.md",),
    "B-12": ("docs/implementation/既有检索与智能体实施计划.md",),
}


def _source_hashes(source_files: tuple[str, ...]) -> dict[str, str]:
    """只读取允许的文档和源码并记录摘要，不把其内容复制进资料。"""
    hashes: dict[str, str] = {}
    for source in source_files:
        if any(part.lower() in FORBIDDEN_SOURCE_PARTS for part in Path(source).parts):
            raise ValueError(f"评测来源路径被禁止读取：{source}")
        path = PROJECT_ROOT / source
        if not path.is_file():
            raise FileNotFoundError(f"评测来源文件不存在：{source}")
        hashes[source] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _render_document(
    project: dict[str, Any],
    spec: dict[str, str],
    *,
    extension: str,
    source_files: tuple[str, ...],
    field_values: dict[str, Any],
) -> tuple[str, int]:
    """在虚构标题和证据行之后拼接声明来源的真实 Markdown 正文。"""
    title = f"{project['name']}｜{spec['document_id']}｜{spec['topic']}运行资料"
    lines = [
        f"# {title}" if extension == "md" else title,
        f"资料标题：{title}",
        f"项目标识：{project['id']}",
        f"文档编号：{spec['document_id']}",
        f"来源文件：{'、'.join(source_files)}",
        f"资料主题：{spec['topic']}",
        f"证据事实：{spec['answer']}",
        f"资料类型：{field_values['DOCUMENT_TYPE']}",
        f"项目阶段：{field_values['PROJECT_STAGE']}",
        f"文档日期：{field_values['DOCUMENT_DATE']}",
        f"编制单位：{field_values['AUTHORING_ORGANIZATION']}",
        f"版本号：{field_values['VERSION_NUMBER']}",
        f"关键词：{'；'.join(field_values['KEYWORDS'])}",
    ]
    rendered = "\n".join(lines) + "\n\n"
    rendered += "\n\n".join(
        f"## 来源正文：{source}\n{(PROJECT_ROOT / source).read_text(encoding='utf-8').strip()}"
        for source in source_files
    )
    rendered += "\n"
    return rendered, len(rendered)


def _write_pdf(file_path: Path, rendered: str) -> None:
    """将来源 Markdown 的完整文本分成多页可提取 PDF。"""
    pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT_NAME))
    canvas = Canvas(str(file_path), pagesize=letter, invariant=1)
    page_width, page_height = letter
    del page_width
    lines_per_page = 46
    rendered_lines: list[str] = []
    for line in rendered.splitlines():
        wrapped = textwrap.wrap(
            line,
            width=86,
            break_long_words=True,
            break_on_hyphens=False,
        )
        rendered_lines.extend(wrapped or [""])
    for line_number, line in enumerate(rendered_lines):
        if line_number % lines_per_page == 0:
            if line_number:
                canvas.showPage()
            text = canvas.beginText(40, page_height - 42)
            text.setFont(PDF_FONT_NAME, 9)
        text.textLine(line)
        if line_number % lines_per_page == lines_per_page - 1:
            canvas.drawText(text)
    if rendered_lines:
        canvas.drawText(text)
    canvas.save()


def _document_entry(
    project: dict[str, Any], spec: dict[str, str], *, ordinal: int
) -> tuple[dict[str, Any], str]:
    """生成单份资料的原文和可追溯标注。"""
    extension = (
        "pdf"
        if spec["document_id"] in PDF_DOCUMENT_IDS
        else TEXT_FORMATS[ordinal % len(TEXT_FORMATS)]
    )
    filename = f"{spec['document_id'].lower()}_{spec['topic']}.{extension}"
    relative_path = f"documents/{project['id']}/{filename}"
    source_files = SOURCE_GROUPS.get(spec["document_id"], tuple(project["source_files"]))
    ordinal = int(spec["document_id"].split("-")[1])
    title = f"{project['name']}｜{spec['document_id']}｜{spec['topic']}运行资料"
    field_values: dict[str, Any] = {
        "TITLE": title,
        "DOCUMENT_TYPE": DOCUMENT_TYPES[(ordinal - 1) % len(DOCUMENT_TYPES)],
        "DOCUMENT_DATE": f"2026-01-{((ordinal - 1) % 28) + 1:02d}",
        "AUTHORING_ORGANIZATION": f"{project['name']}文档治理办公室",
        "VERSION_NUMBER": f"V{((ordinal - 1) % 4) + 1}.0",
        "PROJECT_STAGE": PROJECT_STAGES[(ordinal - 1) % len(PROJECT_STAGES)],
        "KEYWORDS": [spec["topic"], "企业文档", project["id"]],
    }
    rendered, body_char_count = _render_document(
        project,
        spec,
        extension=extension,
        source_files=source_files,
        field_values=field_values,
    )
    evidence_excerpt = f"证据事实：{spec['answer']}"
    evidence_location_type = "PDF_PAGE" if extension == "pdf" else "TEXT_LINE_RANGE"
    evidence_location = {
        "location_type": evidence_location_type,
        "location_start": 1 if extension == "pdf" else 7,
        "location_end": 1 if extension == "pdf" else 7,
        "excerpt": evidence_excerpt,
    }
    document = {
        "id": spec["document_id"],
        "project_id": project["id"],
        "relative_path": relative_path,
        "format": extension.upper(),
        "title": f"{project['name']}｜{spec['topic']}运行资料",
        "body_char_count": body_char_count,
        "source_files": list(source_files),
        "source_sha256": _source_hashes(source_files),
        "answer": spec["answer"],
        "answer_fragments": list(spec.get("answer_fragments", [spec["answer"]])),
        "expected_fields": {},
        "evidence": {
            **evidence_location,
        },
    }
    field_line_numbers = {
        "TITLE": 2,
        "DOCUMENT_TYPE": 8,
        "DOCUMENT_DATE": 10,
        "AUTHORING_ORGANIZATION": 11,
        "VERSION_NUMBER": 12,
        "PROJECT_STAGE": 9,
        "KEYWORDS": 13,
    }
    rendered_lines = rendered.splitlines()
    for field_name, value in field_values.items():
        line_number = field_line_numbers[field_name]
        evidence = {
            "location_type": evidence_location_type,
            "location_start": 1 if extension == "pdf" else line_number,
            "location_end": 1 if extension == "pdf" else line_number,
            "excerpt": rendered_lines[line_number - 1],
        }
        document["expected_fields"][field_name] = {
            "value": value,
            "evidence": [evidence],
        }
    return document, rendered


def _build_questions(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从资料事实生成有据、无据和隔离三类问题集。"""
    by_id = {document["id"]: document for document in documents}
    questions: list[dict[str, Any]] = []
    for index, spec in enumerate(FACT_SPECS, start=1):
        document = by_id[spec["document_id"]]
        expected_answer = (
            "解析服务先识别文件格式。"
            if spec["document_id"] == "B-01"
            else spec["answer"]
        )
        question: dict[str, Any] = {
            "id": f"GROUNDED-{index:02d}",
            "category": "GROUNDED",
            "project_id": spec["project_id"],
            "question": spec["question"],
            "expected_answer": expected_answer,
            "expected_evidence": {
                "document_id": document["id"],
                "relative_path": document["relative_path"],
                "items": [document["evidence"]],
            },
        }
        if "answer_fragments" in spec:
            question["expected_answer_fragments"] = list(spec["answer_fragments"])
        questions.append(question)

    absent = (
        ("enterprise-a", "星河项目的付款比例是多少？"),
        ("enterprise-a", "星河项目的现场负责人姓名是什么？"),
        ("enterprise-b", "云港项目的合同总金额是多少？"),
        ("enterprise-b", "云港项目的现场联系电话是什么？"),
    )
    for index, (project_id, question) in enumerate(absent, start=1):
        questions.append(
            {
                "id": f"NO_EVIDENCE-{index:02d}",
                "category": "NO_EVIDENCE",
                "project_id": project_id,
                "question": question,
                "expected_answer": None,
            }
        )

    isolation_specs = (
        ("enterprise-a", "B-11", "云港项目的责任单位和审批编号是什么？"),
        ("enterprise-a", "B-12", "云港项目上线审批编号、结论及签发日期是什么？"),
        ("enterprise-b", "A-11", "星河项目归档审批单号及审批结论是什么？"),
        ("enterprise-b", "A-12", "星河项目最终验收日期和责任单位是什么？"),
    )
    for index, (project_id, hidden_id, question) in enumerate(isolation_specs, start=1):
        hidden = by_id[hidden_id]
        questions.append(
            {
                "id": f"ISOLATION-{index:02d}",
                "category": "ISOLATION",
                "project_id": project_id,
                "question": question,
                "expected_answer": None,
                "hidden_evidence_in_other_project": {
                    "document_id": hidden["id"],
                    "project_id": hidden["project_id"],
                    "relative_path": hidden["relative_path"],
                    "items": [hidden["evidence"]],
                    "expected_answer": hidden["answer"],
                    "expected_answer_fragments": hidden["answer_fragments"],
                },
            }
        )
    return questions


def generate_enterprise_eval_data(output_root: Path | str = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """生成两项目企业规模资料，返回与落盘标注完全一致的字典。"""
    root = Path(output_root)
    documents_root = root / "documents"
    labels_root = root / "labels"
    root.mkdir(parents=True, exist_ok=True)
    documents_root.mkdir(parents=True, exist_ok=True)
    labels_root.mkdir(parents=True, exist_ok=True)

    project_entries = [
        {
            "id": project["id"],
            "name": project["name"],
            "description": project["description"],
            "source_files": list(project["source_files"]),
        }
        for project in PROJECT_SPECS
    ]
    documents: list[dict[str, Any]] = []
    specs = [*FACT_SPECS, *SUPPORTING_DOCS]
    projects_by_id = {project["id"]: project for project in PROJECT_SPECS}
    for ordinal, spec in enumerate(specs):
        project = projects_by_id[spec["project_id"]]
        document, rendered = _document_entry(project, spec, ordinal=ordinal)
        path = root / document["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if document["format"] == "PDF":
            _write_pdf(path, rendered)
        else:
            path.write_text(rendered, encoding="utf-8")
        documents.append(document)

    manifest: dict[str, Any] = {
        "dataset_id": "enterprise-rag-eval-v1",
        "scope": "仅供本地企业规模 RAG 真实链路复测的虚构资料。",
        "projects": project_entries,
        "documents": documents,
        "questions": _build_questions(documents),
    }
    manifest["question_counts"] = {
        category: sum(
            question["category"] == category for question in manifest["questions"]
        )
        for category in ("GROUNDED", "NO_EVIDENCE", "ISOLATION")
    }
    (labels_root / "enterprise-ground-truth.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (labels_root / "question-ground-truth.json").write_text(
        json.dumps(
            {"dataset_id": manifest["dataset_id"], "questions": manifest["questions"]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (labels_root / "document-ground-truth.json").write_text(
        json.dumps(
            {
                "dataset_id": manifest["dataset_id"],
                "projects": project_entries,
                "documents": documents,
                # 运行器沿用既有字段确认流程，normal_documents 是兼容别名。
                "normal_documents": documents,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# 企业规模 RAG 虚构评测资料\n\n"
        "此目录由 `generate_enterprise_rag_eval_data.py` 确定性生成，已加入 Git 忽略规则。\n"
        "资料只用于本地上传、解析、人工确认、正式索引和检索捕获复测。\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    """生成默认目录或命令行指定目录的企业规模资料。"""
    import argparse

    parser = argparse.ArgumentParser(description="生成企业规模 RAG 虚构评测资料。")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    manifest = generate_enterprise_eval_data(args.output_root)
    print(
        f"generated {len(manifest['documents'])} documents and "
        f"{len(manifest['questions'])} questions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
