"""定义文档接口的数据结构。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import ArchiveDocumentStatus, DocumentStatus


class DocumentRead(BaseModel):
    """返回给客户端的文档信息，不暴露服务器存储路径。

    Attributes:
        id: 文档的全局唯一标识。
        kb_id: 文档所属知识库的 ID。
        filename: 已清理目录部分的原文件名。
        file_hash: 原文件内容的 SHA-256 摘要。
        status: 文档当前处理状态。
        chunk_count: 已成功写入 Chroma 的 Chunk 数量。
        error_message: 最近一次处理失败的安全错误摘要。
        created_at: 文档记录的创建时间。
        updated_at: 文档记录最后一次更新时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    filename: str
    file_hash: str
    status: DocumentStatus
    chunk_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class LastErrorRead(BaseModel):
    """返回归档文档最近一次受控失败摘要。

    Attributes:
        code: 稳定的失败代码；没有失败时为空。
        message: 可安全展示的失败说明；没有失败时为空。
    """

    code: str | None
    message: str | None


class FieldSummaryRead(BaseModel):
    """返回七个固定归档字段的人工检查进度。

    Attributes:
        checked_count: 已完成检查的字段数量。
        total_count: 固定归档字段总数量。
    """

    checked_count: int
    total_count: int


class ProcessDocumentRead(BaseModel):
    """返回项目归档文档的受控处理状态，不暴露内部存储信息。

    Attributes:
        id: 归档文档的全局唯一标识。
        filename: 原文件名。
        file_hash: 原文件内容的 SHA-256 摘要。
        status: 归档文档当前处理状态。
        last_error: 最近一次受控失败摘要。
        field_summary: 七个固定字段的人工检查进度。
        confirmed_at: 最近一次人工确认时间；未确认时为空。
        version: 归档文档的乐观锁版本。
        uploaded_at: 文档上传时间。
        updated_at: 文档最近更新时间。
        index_context_chunk_count: 正式索引使用的上下文 Chunk 数量；未索引时为空。
    """

    id: UUID
    filename: str
    file_hash: str
    status: ArchiveDocumentStatus
    last_error: LastErrorRead
    field_summary: FieldSummaryRead
    confirmed_at: datetime | None
    version: int
    uploaded_at: datetime
    updated_at: datetime
    index_context_chunk_count: int | None = None
