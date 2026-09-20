"""定义文档数据库实体及其处理状态。"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import ForeignKeyConstraint, Index
from sqlmodel import Field, SQLModel

from app.models.common import utc_now


class DocumentStatus(str, Enum):
    """表示文档在上传、处理和删除过程中的状态。

    Attributes:
        UPLOADED: 原文件和 SQLite 记录已保存，尚未解析。
        PROCESSING: 正在解析、切分、生成向量或写入 Chroma。
        READY: 文档 Chunk 已成功写入 Chroma，可以参与检索。
        FAILED: 文档解析或向量化失败。
        DELETING: 正在清理文档及其关联数据。
        DELETE_FAILED: 删除过程未完整结束，可以重试。
    """

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETE_FAILED = "DELETE_FAILED"


class Document(SQLModel, table=True):
    """表示知识库中已上传原文件的数据库记录。

    Attributes:
        id: 文档的全局唯一标识。
        kb_id: 文档所属知识库的 ID。
        filename: 清理目录部分后的安全文件名。
        storage_path: 原文件在服务器上的存储路径。
        file_hash: 原文件内容的 SHA-256 哈希值。
        project_id: 智慧档案项目 ID；旧知识库文档保持为空。
        status: 文档当前处理状态。
        chunk_count: 成功写入向量库的 Chunk 数量。
        error_message: 最近一次处理或删除失败的错误摘要。
        created_at: 文档记录的 UTC 创建时间。
        updated_at: 文档记录最后一次更新的 UTC 时间。
    """

    __tablename__ = "documents"
    __table_args__ = (
        # 项目文档必须同时匹配项目和其知识库，防止把其他项目的文档挂入当前项目。
        ForeignKeyConstraint(
            ["project_id", "kb_id"],
            ["projects.id", "projects.kb_id"],
            name="fk_documents_project_kb",
        ),
        # project_id 可为空以兼容历史知识库文档；项目文档仍按项目和内容哈希去重。
        Index("uq_documents_project_file_hash", "project_id", "file_hash", unique=True),
        Index("ix_documents_project_created_id", "project_id", "created_at", "id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kb_id: UUID = Field(foreign_key="knowledge_bases.id", index=True)
    project_id: UUID | None = Field(default=None, index=True)
    filename: str = Field(min_length=1, max_length=255, index=True)
    storage_path: str = Field(min_length=1, max_length=1024)
    file_hash: str = Field(min_length=64, max_length=64, index=True)
    status: DocumentStatus = Field(default=DocumentStatus.UPLOADED, index=True)
    chunk_count: int = Field(default=0, ge=0)
    error_message: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
