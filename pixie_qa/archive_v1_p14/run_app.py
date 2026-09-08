"""以最小输入驱动智慧档案生产问答服务的 Pixie Runnable。"""

import asyncio
from time import perf_counter
from typing import Any
from uuid import UUID

import pixie
from pydantic import BaseModel, Field

from app.services.archive_question_service import answer_archive_question
from app.core.evaluation import eval_wrap


_D5_USER_ID = UUID("00000000-0000-4000-8000-0000000000d5")
_D5_PROJECT_ID = UUID("00000000-0000-4000-8000-0000000000d6")
_D5_KB_ID = UUID("00000000-0000-4000-8000-0000000000d7")


class ArchiveQuestionArgs(BaseModel):
    """定义 D5 Runnable 唯一允许改变的用户问题。"""

    question: str = Field(min_length=1, max_length=2000)


class ArchiveQuestionRunnable(pixie.Runnable[ArchiveQuestionArgs]):
    """串行调用生产问答服务，并由 Pixie 输入边界注入候选证据。"""

    _semaphore: asyncio.Semaphore

    @classmethod
    def create(cls) -> "ArchiveQuestionRunnable":
        """创建 D5 Runnable，并固定串行执行以避免共享观测状态交叉。"""
        instance = cls()
        instance._semaphore = asyncio.Semaphore(1)
        return instance

    async def run(self, args: ArchiveQuestionArgs) -> None:
        """把单个问题交给生产服务；检索函数由评测输入边界替换。"""
        async with self._semaphore:
            # 生产入口仍接收已验证上下文所需的三类 UUID；它们不属于用户可控输入，
            # 评测固定为虚构值，并把 session 留空交给 Pixie 注入的检索结果绕过。
            started_at = perf_counter()
            try:
                await asyncio.to_thread(
                    answer_archive_question,
                    user_id=_D5_USER_ID,
                    project_id=_D5_PROJECT_ID,
                    kb_id=_D5_KB_ID,
                    question=args.question,
                    session=None,
                )
            finally:
                # 在 finally 中记录，确保失败请求也能保留完整链路耗时，便于定位慢请求。
                eval_wrap(
                    (perf_counter() - started_at) * 1000.0,
                    purpose="output",
                    name="archive_question_end_to_end_latency_ms",
                    description="单个档案问题从生产问答入口开始到结束的端到端耗时（毫秒）。",
                )


__all__ = ["ArchiveQuestionArgs", "ArchiveQuestionRunnable"]
