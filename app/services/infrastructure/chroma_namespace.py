"""显式创建并检查 Chroma 项目命名空间。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from app.core.config import settings
from app.core.errors import AppError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChromaNamespaceProvisionResult:
    """一次命名空间准备操作的结果。

    Attributes:
        tenant_created: 本次是否新建了项目 tenant。
        database_created: 本次是否新建了项目 database。
    """

    tenant_created: bool
    database_created: bool


def get_chroma_admin_client() -> Any:
    """返回连接当前 Chroma 服务的管理客户端。

    仅显式运维脚本调用此函数。运行时向量读写仍使用普通 HttpClient，避免
    FastAPI 服务在每次启动时具备管理 tenant/database 的权限。
    """
    try:
        return chromadb.AdminClient(
            ChromaSettings(
                chroma_api_impl="chromadb.api.fastapi.FastAPI",
                chroma_server_host=settings.chroma_host,
                chroma_server_http_port=settings.chroma_port,
            )
        )
    except Exception as exc:
        logger.exception("chroma_admin_client_create_failed")
        raise AppError(
            status_code=503,
            code="VECTOR_ADMIN_UNAVAILABLE",
            message="无法连接 Chroma 管理服务。",
        ) from exc


def ensure_chroma_namespace() -> ChromaNamespaceProvisionResult:
    """幂等地创建当前配置指定的 Chroma tenant/database。"""
    admin_client = get_chroma_admin_client()
    try:
        try:
            admin_client.get_tenant(name=settings.chroma_tenant)
            tenant_created = False
        except NotFoundError:
            admin_client.create_tenant(name=settings.chroma_tenant)
            tenant_created = True

        try:
            admin_client.get_database(
                name=settings.chroma_database,
                tenant=settings.chroma_tenant,
            )
            database_created = False
        except NotFoundError:
            admin_client.create_database(
                name=settings.chroma_database,
                tenant=settings.chroma_tenant,
            )
            database_created = True
    except AppError:
        raise
    except Exception as exc:
        logger.exception("chroma_namespace_provision_failed")
        raise AppError(
            status_code=503,
            code="VECTOR_ADMIN_UNAVAILABLE",
            message="无法准备 Chroma 项目命名空间。",
        ) from exc

    return ChromaNamespaceProvisionResult(
        tenant_created=tenant_created,
        database_created=database_created,
    )
