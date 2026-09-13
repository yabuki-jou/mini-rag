"""提供应用及其依赖组件的健康检查接口。"""

import logging

from fastapi import APIRouter, Response

from app.core.config import settings
from app.db import engine
from app.schemas import HealthComponent, HealthResponse
from app.services.infrastructure.ai_models import get_embeddings
from app.services.infrastructure.chroma import check_chroma_connection

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(response: Response) -> HealthResponse:
    """独立检查 API、数据库、Chroma 和 Embedding 是否可用。

    Args:
        response: FastAPI 响应对象，用于在组件异常时设置 HTTP 503。

    Returns:
        应用整体状态以及每个依赖组件的检查结果。
    """
    components: dict[str, HealthComponent] = {
        "api": HealthComponent(status="ok"),
    }

    # 每项检查相互独立，避免一个组件失败后跳过其他组件。
    # 执行最小 SQL，验证 Engine 能够真正访问数据库。
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1").scalar_one()
        components["database"] = HealthComponent(status="ok")
    except Exception:
        logger.exception("数据库健康检查失败。")
        components["database"] = HealthComponent(
            status="error",
            detail="数据库连接失败",
        )

    # 只调用 Chroma 心跳，不在健康检查中创建或修改 Collection。
    try:
        check_chroma_connection()
        components["chroma"] = HealthComponent(
            status="ok",
            detail="heartbeat=ok",
        )
    except Exception:
        logger.exception("Chroma 健康检查失败。")
        components["chroma"] = HealthComponent(
            status="error",
            detail="Chroma 连接失败",
        )

    # 生成一个真实向量，同时验证模型加载和输出维度。
    try:
        vector = get_embeddings().embed_query("健康检查")
        if len(vector) != settings.embedding_dimension:
            raise RuntimeError("Embedding 向量维度不符合配置")
        components["embedding"] = HealthComponent(
            status="ok",
            detail=f"dimension={len(vector)}",
        )
    except Exception:
        logger.exception("Embedding 健康检查失败。")
        components["embedding"] = HealthComponent(
            status="error",
            detail="Embedding 模型不可用",
        )

    # 只有所有依赖都正常时，应用整体状态才是健康。
    is_healthy = all(
        component.status == "ok" for component in components.values()
    )

    # 组件异常时保留各项检查结果，同时通过 HTTP 503 提醒监控系统。
    if not is_healthy:
        response.status_code = 503

    return HealthResponse(
        status="ok" if is_healthy else "degraded",
        components=components,
    )
