"""按页切分解析结果，并生成保留引用位置的稳定 Chunk 身份。"""

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.core.errors import AppError
from app.services.rag.parser import ParsedPage


@dataclass(frozen=True)
class TextChunk:
    """表示从文档页面正文中切分出的一个文本块。

    Attributes:
        page: Chunk 所属的原始页码，从 1 开始。
        start_index: Chunk 在当前页正文中的起始字符位置。
        chunk_index: Chunk 在整个文档中的顺序，从 0 开始。
        content: Chunk 中保留的正文。
    """

    page: int
    start_index: int
    chunk_index: int
    content: str


def split_pages(parsed_pages: list[ParsedPage]) -> list[TextChunk]:
    """逐页切分正文，并保留页码、页内位置和文档内顺序。

    Args:
        parsed_pages: 文档解析后按原始顺序排列的页面。

    Returns:
        按文档顺序排列的文本块。

    Raises:
        AppError: Chunk 大小和重叠字符数配置无效。
    """
    # 重叠必须小于目标大小，否则切分器无法向前推进。
    chunk_overlap = settings.chunk_overlap
    chunk_size = settings.chunk_size
    if chunk_size <= chunk_overlap:
        raise AppError(
            status_code=500,
            code="INVALID_CHUNK_CONFIG",
            message="chunk_overlap 必须小于 chunk_size。",
        )

    # 优先按段落和中文句子边界切分，最后才回退到字符级切分。
    separators = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
        separators=separators,
    )

    # 每页单独切分以保留页码，chunk_index 则在整个文档中连续递增。
    text_chunks: list[TextChunk] = []
    for parsed_page in parsed_pages:
        # 跳过空白页面，避免产生没有检索价值的 Chunk。
        if not parsed_page.content.strip():
            continue

        # LangChain Document metadata 中会包含页内 start_index。
        documents = text_splitter.create_documents([parsed_page.content])
        for document in documents:
            text_chunk: TextChunk = TextChunk(
                page=parsed_page.page,
                start_index=document.metadata["start_index"],
                chunk_index=len(text_chunks),
                content=document.page_content,
            )
            text_chunks.append(text_chunk)

    return text_chunks


def build_chunk_id(document_id: UUID, chunk: TextChunk) -> str:
    """根据文档身份、页内位置和正文生成稳定的 Chunk ID。

    Args:
        document_id: Chunk 所属文档的 UUID。
        chunk: 已完成位置标注的文本块。

    Returns:
        64 位 SHA-256 十六进制字符串。
    """
    # 文档、页码、位置或正文任一变化都会生成不同身份。
    identity_text = (
        f"{document_id}|{chunk.page}|{chunk.start_index}|{chunk.content}"
    )
    return sha256(identity_text.encode("utf-8")).hexdigest()
