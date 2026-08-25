"""为智慧档案 V1 验证可重复的解析定位与快照规则。"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.core.errors import AppError


ARCHIVE_PARSER_VERSION = "archive-v1-parser-v1"
"""当前定位、归一化与快照序列化契约的版本。"""

ARCHIVE_NORMALIZATION_VERSION = "archive-v1-normalization-v1"
"""当前文本空白归一化规则的版本。"""

MIN_EFFECTIVE_TEXT_CHARACTERS = 20
"""归一化后可提取正文的最小字符数，用于过滤空壳文档。"""

MAX_PARSED_TEXT_CHARACTERS = 1_000_000
"""单个文件归一化后允许保存到解析快照的最大字符数。"""


class ArchiveLocationType(str, Enum):
    """归档证据允许使用的原文定位类型。"""

    PDF_PAGE = "PDF_PAGE"
    DOCX_PARAGRAPH = "DOCX_PARAGRAPH"
    TEXT_LINE_RANGE = "TEXT_LINE_RANGE"


@dataclass(frozen=True)
class ParsedFragment:
    """解析快照中可独立引用的一段归一化文本。

    Attributes:
        location_type: 证据定位的类别。
        location_start: 从 1 开始的页码、段落序号或行号起点。
        location_end: 从 1 开始的页码、段落序号或行号终点。
        content: 经固定空白规则归一化后的正文。
        anchor_text: DOCX 段落前 50 个字符；其他格式为空字符串。
    """

    location_type: ArchiveLocationType
    location_start: int
    location_end: int
    content: str
    anchor_text: str = ""


@dataclass(frozen=True)
class ParsedArchiveDocument:
    """单个原文件的解析验证结果。

    Attributes:
        fragments: 保留原文件顺序的可引用文本片段。
        effective_text_characters: 用于有效文本阈值判断的归一化字符数。
        snapshot_hash: 由固定序列化后的片段内容计算出的 SHA-256。
        parser_version: 生成本快照的定位契约版本。
    """

    fragments: tuple[ParsedFragment, ...]
    effective_text_characters: int
    snapshot_hash: str
    parser_version: str = ARCHIVE_PARSER_VERSION


def parse_archive_document(file_path: Path) -> ParsedArchiveDocument:
    """解析归档候选文件并返回可复现的定位快照。

    Args:
        file_path: 已保存的原文件路径。

    Returns:
        仅包含有效文本片段的解析结果。

    Raises:
        AppError: 文件不存在、格式不支持、无法提取有效文本或文本超出快照上限。
    """
    if not file_path.is_file():
        raise AppError(
            status_code=500,
            code="DOCUMENT_FILE_MISSING",
            message="文档原文件不存在。",
        )

    extension = file_path.suffix.lower()
    fragments = _parse_fragments(file_path, extension)
    effective_text_characters = sum(len(fragment.content) for fragment in fragments)
    _validate_effective_text(
        extension,
        len(fragments),
        effective_text_characters,
    )

    return ParsedArchiveDocument(
        fragments=tuple(fragments),
        effective_text_characters=effective_text_characters,
        snapshot_hash=_build_snapshot_hash(fragments),
    )


def _parse_fragments(
    file_path: Path,
    extension: str,
) -> list[ParsedFragment]:
    """按扩展名选择定位适配器，避免调用方自行猜测定位语义。"""
    if extension == ".pdf":
        return _parse_pdf_fragments(file_path)
    if extension == ".docx":
        return _parse_docx_fragments(file_path)
    if extension in {".txt", ".md"}:
        return _parse_text_fragments(file_path)

    raise AppError(
        status_code=415,
        code="FILE_TYPE_UNSUPPORTED",
        message="仅支持 PDF、DOCX、TXT 和 MD 文件。",
    )


def _parse_pdf_fragments(file_path: Path) -> list[ParsedFragment]:
    """按 PDF 原始页序号提取非空文本页。"""
    try:
        reader = PdfReader(file_path)
        fragments = []
        for page_number, page in enumerate(reader.pages, start=1):
            content = _normalize_text(page.extract_text() or "")
            if content:
                fragments.append(
                    ParsedFragment(
                        location_type=ArchiveLocationType.PDF_PAGE,
                        location_start=page_number,
                        location_end=page_number,
                        content=content,
                    )
                )
        return fragments
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            status_code=422,
            code="PARSE_TEXT_UNAVAILABLE",
            message="文档文本解析失败。",
        ) from exc


def _parse_docx_fragments(file_path: Path) -> list[ParsedFragment]:
    """按非空逻辑块顺序提取 DOCX 段落、列表项和表格行。"""
    try:
        document = DocxDocument(file_path)
        fragments: list[ParsedFragment] = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                _append_docx_text_block(
                    fragments,
                    Paragraph(child, document).text,
                )
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                for row in table.rows:
                    row_text = " | ".join(_distinct_table_cell_texts(row))
                    _append_docx_text_block(fragments, row_text)
        return fragments
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            status_code=422,
            code="PARSE_TEXT_UNAVAILABLE",
            message="文档文本解析失败。",
        ) from exc


def _distinct_table_cell_texts(row: object) -> list[str]:
    """按从左到右顺序读取表格行，并跳过横向合并单元格的重复引用。"""
    cell_texts: list[str] = []
    seen_cell_elements: set[int] = set()
    for cell in row.cells:
        element_identity = id(cell._tc)
        if element_identity in seen_cell_elements:
            continue
        seen_cell_elements.add(element_identity)
        cell_texts.append(cell.text)
    return cell_texts


def _append_docx_text_block(
    fragments: list[ParsedFragment],
    raw_text: str,
) -> None:
    """把非空 DOCX 逻辑块追加为从 1 开始的稳定段落序号。"""
    content = _normalize_text(raw_text)
    if not content:
        return

    paragraph_number = len(fragments) + 1
    fragments.append(
        ParsedFragment(
            location_type=ArchiveLocationType.DOCX_PARAGRAPH,
            location_start=paragraph_number,
            location_end=paragraph_number,
            content=content,
            anchor_text=content[:50],
        )
    )


def _parse_text_fragments(file_path: Path) -> list[ParsedFragment]:
    """将 TXT/MD 的每个非空原始行保留为带行号的可引用片段。"""
    try:
        raw_text = file_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError(
            status_code=422,
            code="PARSE_TEXT_UNAVAILABLE",
            message="文档不是可解析的 UTF-8 文本。",
        ) from exc
    except OSError as exc:
        raise AppError(
            status_code=422,
            code="PARSE_TEXT_UNAVAILABLE",
            message="文档文本解析失败。",
        ) from exc

    normalized_newlines = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    fragments = []
    for line_number, raw_line in enumerate(normalized_newlines.split("\n"), start=1):
        content = _normalize_text(raw_line)
        if content:
            fragments.append(
                ParsedFragment(
                    location_type=ArchiveLocationType.TEXT_LINE_RANGE,
                    location_start=line_number,
                    location_end=line_number,
                    content=content,
                )
            )
    return fragments


def _normalize_text(raw_text: str) -> str:
    """按冻结的空白规则把任意空白序列压缩为一个半角空格。"""
    normalized_newlines = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized_newlines).strip()


def _validate_effective_text(
    extension: str,
    fragment_count: int,
    effective_text_characters: int,
) -> None:
    """用统一阈值拒绝空壳、扫描件和过大的解析快照。"""
    if effective_text_characters < MIN_EFFECTIVE_TEXT_CHARACTERS:
        if extension == ".pdf" and fragment_count == 0:
            raise AppError(
                status_code=422,
                code="SCANNED_PDF_UNSUPPORTED",
                message="暂不支持扫描件，请上传文本版资料。",
            )
        raise AppError(
            status_code=422,
            code="PARSE_TEXT_UNAVAILABLE",
            message="文档未提取到足够的有效文本。",
        )
    if effective_text_characters > MAX_PARSED_TEXT_CHARACTERS:
        raise AppError(
            status_code=422,
            code="PARSE_TEXT_TOO_LARGE",
            message="文档可提取文本超过归档解析上限。",
        )


def _build_snapshot_hash(fragments: list[ParsedFragment]) -> str:
    """对固定顺序、固定字段的归一化片段计算 SHA-256 快照哈希。"""
    canonical_fragments = [
        {
            "anchor_text": fragment.anchor_text,
            "content": fragment.content,
            "location_end": fragment.location_end,
            "location_start": fragment.location_start,
            "location_type": fragment.location_type.value,
        }
        for fragment in fragments
    ]
    canonical_json = json.dumps(
        canonical_fragments,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
