"""创建并配置 Mini RAG 的 FastAPI 应用。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.migration_service import upgrade_database
from app.routers import (
    archive_agent,
    auth,
    health,
    projects,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """管理 FastAPI 应用的启动和关闭生命周期。

    Args:
        _: 当前 FastAPI 应用；本阶段不需要直接使用。

    Yields:
        启动初始化完成后，将控制权交还给 FastAPI。
    """
    # 应用接收请求前先验证旧 Schema，并按版本顺序执行缺失迁移。
    upgrade_database()
    yield


# 启动时安装控制台和文件日志，业务日志会自动携带 request_id。
configure_logging(
    log_file_path=settings.log_path,
    max_bytes=settings.log_max_bytes,
    backup_count=settings.log_backup_count,
)

# 集中创建 ASGI 应用，Uvicorn 通过 ``app.main:app`` 导入该对象。
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "从零手写的 FastAPI + LangChain + Chroma RAG 后端。\n\n"
        "智慧档案 V1 当前提供项目管理接口；内置清单仅为虚构演示规则，"
        "不代表真实工程项目的法定或行业归档要求。"
    ),
    lifespan=lifespan,
)

# 请求上下文中间件负责响应头、请求总耗时和全链路 request_id。
app.add_middleware(RequestContextMiddleware)

# 先安装统一异常响应，再按业务领域注册各组路由。
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(archive_agent.router)
