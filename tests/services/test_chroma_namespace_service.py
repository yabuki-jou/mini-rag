"""验证 Chroma 项目命名空间的显式、幂等准备逻辑。"""

from types import SimpleNamespace
from unittest.mock import Mock

from chromadb.errors import NotFoundError
import pytest

from app.services import chroma_namespace_service


@pytest.fixture
def project_namespace_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """让命名空间测试不依赖开发者本机的 .env 配置。"""
    monkeypatch.setattr(
        chroma_namespace_service,
        "settings",
        SimpleNamespace(
            chroma_tenant="mini_rag_tenant",
            chroma_database="mini_rag_chroma",
        ),
    )


def test_ensure_chroma_namespace_creates_missing_tenant_and_database(
    monkeypatch: pytest.MonkeyPatch,
    project_namespace_settings: None,
) -> None:
    """首次准备只创建缺失的项目 tenant/database。"""
    admin_client = Mock()
    admin_client.get_tenant.side_effect = NotFoundError("tenant not found")
    admin_client.get_database.side_effect = NotFoundError("database not found")
    monkeypatch.setattr(
        chroma_namespace_service,
        "get_chroma_admin_client",
        lambda: admin_client,
    )

    result = chroma_namespace_service.ensure_chroma_namespace()

    assert result.tenant_created is True
    assert result.database_created is True
    admin_client.create_tenant.assert_called_once_with(name="mini_rag_tenant")
    admin_client.create_database.assert_called_once_with(
        name="mini_rag_chroma",
        tenant="mini_rag_tenant",
    )


def test_ensure_chroma_namespace_keeps_existing_tenant_and_database(
    monkeypatch: pytest.MonkeyPatch,
    project_namespace_settings: None,
) -> None:
    """重复执行不能创建重复命名空间或触发删除。"""
    admin_client = Mock()
    monkeypatch.setattr(
        chroma_namespace_service,
        "get_chroma_admin_client",
        lambda: admin_client,
    )

    result = chroma_namespace_service.ensure_chroma_namespace()

    assert result.tenant_created is False
    assert result.database_created is False
    admin_client.create_tenant.assert_not_called()
    admin_client.create_database.assert_not_called()
