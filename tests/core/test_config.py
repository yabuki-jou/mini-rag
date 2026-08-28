"""验证业务数据库配置不会回退到 SQLite。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_accept_psycopg_database_url() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost:5432/test_db",
    )
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_settings_reject_sqlite_business_database() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL 必须使用"):
        Settings(_env_file=None, database_url="sqlite:///./data/legacy.db")


def test_settings_default_to_project_chroma_namespace() -> None:
    """Chroma 命名空间必须使用项目固定名称，而不是服务端默认名称。"""
    settings = Settings(_env_file=None)

    assert settings.chroma_tenant == "mini_rag_tenant"
    assert settings.chroma_database == "mini_rag_chroma"
    assert settings.chroma_collection == "mini_rag_knowledge_chunks_v1"
    assert settings.chroma_final_collection == "archive_final_chunks"


def test_settings_supports_only_explicit_archive_embedding_context_modes() -> None:
    """正式索引的向量上下文模式必须是受控枚举，便于固定集复现实验。"""
    assert (
        Settings(
            _env_file=None,
            archive_embedding_context_mode="values",
        ).archive_embedding_context_mode
        == "values"
    )

    with pytest.raises(ValidationError):
        Settings(_env_file=None, archive_embedding_context_mode="unknown")
