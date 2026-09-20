"""集中管理应用配置，并把相对路径统一解析到项目根目录。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from psycopg import postgres
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 所有相对路径都以项目根目录为基准，不受终端启动位置影响。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """从环境变量或项目根目录的 ``.env`` 文件读取运行参数。

    Attributes:
        app_name: Swagger 标题和应用名称。
        app_env: 当前运行环境，例如 ``development`` 或 ``production``。
        log_file: 应用日志基准路径；其父目录和后缀用于生成日期路径。
        log_max_bytes: 单个日志文件允许的最大字节数。
        log_backup_count: 每个日期日志按大小轮转后保留的备份文件数量。
        postgres_db: PostgreSQL 数据库名称。
        postgres_user: PostgreSQL 登录用户名。
        postgres_password: PostgreSQL 登录密码；仅用于构造本地默认连接地址。
        database_url: SQLModel 数据库连接地址。
        agent_checkpoint_file: LangGraph 执行状态使用的独立 SQLite 文件。
        file_storage_dir: 上传原文件的本地存储目录。
        max_upload_file_bytes: 单个上传原文件允许的最大字节数。
        chroma_host: Chroma HTTP 服务主机名。
        chroma_port: Chroma HTTP 服务端口。
        chroma_tenant: Chroma 服务端租户名称。
        chroma_database: Chroma 租户内数据库名称。
        chroma_final_collection: 保存智慧档案正式 Chunk 的独立 Chroma Collection 名称。
        embedding_model_path: 本地 BGE 模型目录。
        embedding_device: Embedding 运行设备。
        embedding_dimension: Embedding 模型输出的向量维度。
        archive_embedding_context_mode: 正式档案 Chunk 向量中确认字段的表示方式。
        archive_reranker_model_path: 本地正式档案 Reranker 模型目录。
        archive_reranker_device: Reranker 运行设备。
        archive_reranker_candidate_k: 每次交给 Reranker 的固定 Chroma 候选数量。
        archive_reranker_query_mode: Reranker 查询表达实验模式；默认使用 C4-A 基线。
        archive_reranker_score_threshold: 可选的最低 Reranker 分数；未标定时为 ``None``。
        deepseek_api_key: DeepSeek API 密钥。
        deepseek_base_url: DeepSeek 的 OpenAI 兼容接口地址。
        deepseek_model: 生成回答所使用的模型名称。
        deepseek_request_timeout_seconds: DeepSeek 单次请求固定超时秒数。
        auth_jwt_secret: 签发和验证 JWT 的本地机密；为空时认证接口安全地拒绝服务。
        auth_access_token_minutes: Access Token 有效期，单位为分钟。
        auth_refresh_token_days: Refresh Token 与认证会话有效期，单位为天。
    """

    # 从项目根目录读取 .env；忽略暂未使用的变量，且变量名不区分大小写。
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用、PostgreSQL、Checkpoint SQLite 和原文件存储配置。
    app_name: str = "Mini RAG Handwrite"
    app_env: str = "development"
    log_file: Path = Path("./logs/app.log")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_backup_count: int = Field(default=5, ge=0)

    postgres_db: str = "mini_rag"
    postgres_user: str = "mini_rag"
    postgres_password: str = "mini_rag"
    database_url: str = (
        f"postgresql+psycopg://{postgres_user}:{postgres_password}@localhost:5432/{postgres_db}"
    )
    agent_checkpoint_file: Path = Path("./data/agent_checkpoints.db")
    file_storage_dir: Path = Path("./data/files")
    max_upload_file_bytes: int = Field(default=20 * 1024 * 1024, gt=0)

    # Chroma 仅通过内部 HTTP 网络访问；本机调试只允许回环地址。
    chroma_host: str = "localhost"
    chroma_port: int = Field(default=8001, ge=1, le=65535)
    chroma_tenant: str = "mini_rag_tenant"
    chroma_database: str = "mini_rag_chroma"
    chroma_final_collection: str = "archive_final_chunks"

    # 本地 Embedding 模型及其输出维度配置。
    embedding_model_path: Path = Path(
        "../../../models/embedding_models/bge-base-zh-v1.5"
    )
    embedding_device: str = "cpu"
    embedding_dimension: int = Field(default=768, gt=0)
    archive_embedding_context_mode: Literal[
        "none", "values", "labeled", "evidence_values"
    ] = "evidence_values"

    # Reranker 只服务于已完成授权与元数据校验的正式档案候选，固定使用本地 CPU。
    archive_reranker_model_path: Path = Path("embedding_models/bge-reranker-base")
    archive_reranker_device: str = "cpu"
    archive_reranker_candidate_k: int = Field(default=30, ge=30, le=30)
    archive_reranker_query_mode: Literal["c4_a", "c4_b"] = "c4_a"
    archive_reranker_score_threshold: float | None = None

    # 阶段五调用 DeepSeek 时使用的生成模型配置。
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    deepseek_request_timeout_seconds: float = Field(default=30, gt=0)

    # 认证密钥不得提供不安全默认值；部署者必须通过本地 .env 明确配置。
    auth_jwt_secret: SecretStr | None = None
    auth_access_token_minutes: int = Field(default=30, gt=0, le=24 * 60)
    auth_refresh_token_days: int = Field(default=7, gt=0, le=31)

    @field_validator("database_url")
    @classmethod
    def validate_postgres_database_url(cls, value: str) -> str:
        """拒绝旧 SQLite 配置，确保业务数据库只能使用 Psycopg。

        Args:
            value: 从环境变量或 ``.env`` 读取的数据库连接 URL。

        Returns:
            去除首尾空白后的 PostgreSQL Psycopg URL。

        Raises:
            ValueError: URL 不是 ``postgresql+psycopg://`` 方案。
        """
        normalized = value.strip()
        if not normalized.startswith("postgresql+psycopg://"):
            raise ValueError(
                "DATABASE_URL 必须使用 postgresql+psycopg://；"
                "请按 .env.example 更新本地 .env。"
            )
        return normalized

    def resolve_path(self, path: Path) -> Path:
        """将配置路径解析为不依赖当前工作目录的绝对路径。

        Args:
            path: 环境变量或默认配置中读取到的路径。

        Returns:
            绝对路径；相对路径以项目根目录为基准。
        """
        # 绝对路径直接规范化；相对路径统一拼接项目根目录。
        if path.is_absolute():
            return path.resolve()
        return (PROJECT_ROOT / path).resolve()

    @property
    def embedding_path(self) -> Path:
        """本地 Embedding 模型目录的绝对路径。"""
        return self.resolve_path(self.embedding_model_path)

    @property
    def archive_reranker_path(self) -> Path:
        """本地正式档案 Reranker 模型目录的绝对路径。"""
        return self.resolve_path(self.archive_reranker_model_path)

    @property
    def log_path(self) -> Path:
        """返回应用日志基准路径，其父目录和后缀用于生成日期路径。"""
        return self.resolve_path(self.log_file)

    @property
    def file_storage_path(self) -> Path:
        """上传原文件目录的绝对路径。"""
        return self.resolve_path(self.file_storage_dir)

    @property
    def agent_checkpoint_path(self) -> Path:
        """LangGraph Checkpoint SQLite 文件的绝对路径。"""
        return self.resolve_path(self.agent_checkpoint_file)

@lru_cache
def get_settings() -> Settings:
    """返回进程内唯一的应用配置对象。"""
    return Settings()


# 模块内共享同一配置对象，避免各服务重复解析 .env。
settings = get_settings()
