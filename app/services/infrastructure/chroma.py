"""管理 Chroma HTTP 客户端连接和只读心跳。"""

import logging
from functools import lru_cache
from typing import Any

import chromadb

from app.core.config import settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

@lru_cache
def get_chroma_client() -> Any:
    """创建并缓存连接独立 Chroma HTTP 服务的客户端。

    Chroma 不接受客户端提交的用户或知识库范围；这些范围必须由本服务的
    写入、查询和删除调用显式构造。
    """
    try:
        return chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            tenant=settings.chroma_tenant,
            database=settings.chroma_database,
        )
    except Exception as exc:
        logger.exception(
            "chroma_client_create_failed host=%s port=%s",
            settings.chroma_host,
            settings.chroma_port,
        )
        raise AppError(
            status_code=503,
            code="VECTOR_UNAVAILABLE",
            message="无法连接 Chroma 向量服务。",
        ) from exc

def check_chroma_connection() -> int:
    """执行只读心跳，验证 Chroma 可用且不创建任何 Collection。"""
    try:
        return int(get_chroma_client().heartbeat())
    except AppError:
        raise
    except Exception as exc:
        logger.exception(
            "chroma_heartbeat_failed host=%s port=%s",
            settings.chroma_host,
            settings.chroma_port,
        )
        raise AppError(
            status_code=503,
            code="VECTOR_UNAVAILABLE",
            message="无法连接 Chroma 向量服务。",
        ) from exc
