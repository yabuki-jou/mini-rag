"""定义文档解析结果，并提供不同文件格式的统一解析入口。"""

from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader

from app.core.errors import AppError


@dataclass(frozen=True)
class ParsedPage:
    """表示从原文件中解析出的一页文本。

    Attributes:
        page: 页码，从 1 开始；无分页格式统一使用 1。
        content: 当前页解析得到的正文。
    """

    page: int
    content: str


def parse_text_file(file_path: Path) -> list[ParsedPage]:
    """读取 TXT 或 Markdown 文件，并返回统一的页面结构。

    Args:
        file_path: 待解析原文件的本地路径。

    Returns:
        只包含一个逻辑页面的解析结果。
    """
    # utf-8-sig 同时兼容普通 UTF-8 和带 BOM 的文本文件。
    content = file_path.read_text(encoding="utf-8-sig")
    return [ParsedPage(page=1, content=content)]


def parse_pdf_file(file_path: Path) -> list[ParsedPage]:
    """按页读取普通 PDF，并返回统一的页面结构。

    Args:
        file_path: 待解析 PDF 文件的本地路径。

    Returns:
        按原始页码排列的解析结果。
    """
    # PDF 必须逐页提取，才能让后续 Chunk 保留真实页码。
    reader = PdfReader(file_path)
    parsed_pages: list[ParsedPage] = []
    for page_number, pdf_page in enumerate(reader.pages, start=1):
        # 无文本页统一转换为空字符串，最终由统一入口判断是否全为空。
        content = pdf_page.extract_text() or ""
        parsed_pages.append(
            ParsedPage(
                page=page_number,
                content=content,
            )
        )

    return parsed_pages


def parse_docx_file(file_path: Path) -> list[ParsedPage]:
    """读取 DOCX 段落，并返回统一的页面结构。

    Args:
        file_path: 待解析 DOCX 文件的本地路径。

    Returns:
        只包含一个逻辑页面的解析结果。
    """
    # DOCX 没有稳定分页信息，因此按段落拼成一个逻辑页面。
    document = DocxDocument(file_path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    content = "\n".join(paragraphs)

    return [ParsedPage(page=1, content=content)]


# 将文件扩展名映射到对应的具体解析函数。
PARSE_FILE = {
    ".txt": parse_text_file,
    ".md": parse_text_file,
    ".pdf": parse_pdf_file,
    ".docx": parse_docx_file,
}


def parse_document(file_path: Path) -> list[ParsedPage]:
    """根据文件扩展名选择解析器，并返回统一的页面结构。

    Args:
        file_path: 待解析原文件的本地路径。

    Returns:
        保留原始逻辑页码的解析结果。

    Raises:
        AppError: 原文件不存在、类型不支持、解析失败或没有有效正文。
    """

    # 数据库中的文档记录存在时，服务器上的原文件也必须存在。
    if not file_path.is_file():
        raise AppError(
            status_code=500,
            code="DOCUMENT_FILE_MISSING",
            message="文档原文件不存在。",
        )

    # 扩展名决定解析策略，未知格式在调用第三方库前直接拒绝。
    file_extension = file_path.suffix.lower()
    if file_extension not in PARSE_FILE:
        raise AppError(
            status_code=400,
            code="UNSUPPORTED_FILE_TYPE",
            message="仅支持 TXT、Markdown、PDF 和 DOCX 文件。",
        )

    # 通过统一映射选择具体解析器，避免调用方重复判断文件类型。
    file_function = PARSE_FILE.get(file_extension)
    try:
        parsed_pages: list[ParsedPage] = file_function(file_path)
    except AppError:
        # 已经标准化的业务错误不重复包装。
        raise
    except Exception as e:
        # 第三方解析器异常统一转换为稳定的 422 响应。
        raise AppError(
            status_code=422,
            code="DOCUMENT_PARSE_FAILED",
            message="文档内容解析失败。",
        ) from e

    # 扫描版 PDF 等文件可能有页面结构，但没有可提取的文字。
    if not any(page.content.split() for page in parsed_pages):
        raise AppError(
            status_code=422,
            code="DOCUMENT_CONTENT_EMPTY",
            message="文档中没有可提取的文字。",
        )
    return parsed_pages
