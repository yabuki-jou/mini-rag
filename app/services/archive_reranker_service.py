"""加载本地 Cross-Encoder，并为正式档案候选计算重排分数。"""

import logging
import math
from functools import lru_cache

from sentence_transformers import CrossEncoder

from app.core.config import settings
from app.core.errors import AppError


logger = logging.getLogger(__name__)


@lru_cache
def get_archive_reranker() -> CrossEncoder:
    """延迟加载并缓存正式档案使用的本地 Reranker。"""
    model_path = settings.archive_reranker_path
    if not model_path.exists():
        logger.error("archive_reranker_model_not_found path=%s", model_path)
        raise AppError(503, "RERANKER_UNAVAILABLE", "Reranker 模型目录不存在。")

    try:
        # 仅从已验证的本地目录加载，避免服务运行时因网络或模型版本变化得到不同结果。
        return CrossEncoder(
            str(model_path),
            device=settings.archive_reranker_device,
            local_files_only=True,
        )
    except Exception as exc:
        logger.exception("archive_reranker_load_failed path=%s", model_path)
        raise AppError(503, "RERANKER_UNAVAILABLE", "无法加载本地 Reranker 模型。") from exc


def score_archive_candidates(*, query: str, contents: list[str]) -> list[float]:
    """批量计算查询与已校验原始 Chunk 的本地重排分数。"""
    if not contents:
        return []

    try:
        raw_scores = get_archive_reranker().predict(
            [(query, content) for content in contents]
        )
        scores = [float(score) for score in raw_scores]
    except AppError:
        raise
    except Exception as exc:
        # 不允许静默降级，否则固定集无法判断结果究竟来自哪一种排序策略。
        logger.exception("archive_reranker_predict_failed candidate_count=%s", len(contents))
        raise AppError(503, "RERANKER_UNAVAILABLE", "本地 Reranker 推理失败。") from exc

    if len(scores) != len(contents) or any(not math.isfinite(score) for score in scores):
        raise AppError(500, "RERANKER_RESULT_INVALID", "Reranker 返回了无效分数。")
    return scores
