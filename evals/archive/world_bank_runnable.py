"""以 Pixie 输入边界驱动 FR-039 世界银行档案问答。"""

from __future__ import annotations

import asyncio
from typing import ClassVar
from uuid import UUID

import pixie
from pydantic import BaseModel, ConfigDict, Field

from app.core.evaluation import eval_wrap
from app.services.archive.questions import answer_archive_question
from scripts.archive_model_budget import (
    AcceptanceError,
    AttemptBudget,
    BudgetViolation,
    real_model_factory_scope,
)


ATTEMPT_CAP = 21
_REQUEST_RESERVATION = 1
_USER_ID = UUID("00000000-0000-4000-8000-0000000000d5")
_PROJECT_ID = UUID("00000000-0000-4000-8000-0000000000d6")
_KB_ID = UUID("00000000-0000-4000-8000-0000000000d7")


class WorldBankQuestionArgs(BaseModel):
    """定义唯一可由 Pixie 作为模型用户问题传入的字段。

    Attributes:
        question: 本轮用户原问题；候选证据只能经检索 Wrap 注入。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


class WorldBankQuestionRunnable(pixie.Runnable[WorldBankQuestionArgs]):
    """串行调用生产 FR-039 问答入口，并限制真实模型尝试总数。

    Attributes:
        _semaphore: 同一评测实例的异步单并发闸门。
        _budget: 当前批次共享的真实模型尝试预算。
        _sample_index: 当前批次的单调安全样本序号。
        _halted: 首个非预算执行故障后阻止后续条目运行。
        attempt_cap: 由 Runnable 类型固定、不可经评测输入修改的批次上限。
    """

    attempt_cap: ClassVar[int] = ATTEMPT_CAP
    _semaphore: asyncio.Semaphore
    _budget: AttemptBudget
    _sample_index: int
    _halted: bool
    halt_on_failure: ClassVar[bool] = False

    @classmethod
    def create(cls) -> WorldBankQuestionRunnable:
        """创建使用类型级批次上限的单并发运行实例。"""
        instance = cls()
        instance._semaphore = asyncio.Semaphore(1)
        instance._budget = AttemptBudget(cap=cls.attempt_cap)
        instance._sample_index = 0
        instance._halted = False
        return instance

    async def run(self, args: WorldBankQuestionArgs) -> None:
        """运行生产问答；检索候选由 `archive_question_retrieval` 注入。

        Args:
            args: 仅包含用户原问题的 Pixie 输入。
        """
        if self.halt_on_failure:
            await self._run_focused(args)
            return

        async with self._semaphore:
            try:
                with self._budget.request(reservation=_REQUEST_RESERVATION):
                    # 预留先于工厂作用域和生产调用，预算耗尽时不会创建模型委托。
                    with real_model_factory_scope(budget=self._budget):
                        await asyncio.to_thread(
                            answer_archive_question,
                            user_id=_USER_ID,
                            project_id=_PROJECT_ID,
                            kb_id=_KB_ID,
                            question=args.question,
                            session=None,
                        )
            except AcceptanceError:
                eval_wrap(
                    {"error_code": "MODEL_ATTEMPT_REJECTED"},
                    purpose="output",
                    name="archive_world_bank_safe_error",
                    description="只包含固定代码的模型预算安全失败状态。",
                )
                raise
            except Exception:
                eval_wrap(
                    {"error_code": "RUNNABLE_FAILED"},
                    purpose="output",
                    name="archive_world_bank_safe_error",
                    description="不含回答或异常文本的固定运行失败状态。",
                )
                raise RuntimeError("RUNNABLE_FAILED") from None

    async def _run_focused(self, args: WorldBankQuestionArgs) -> None:
        """隔离聚焦评测故障并在首个非预算故障后熔断。

        Args:
            args: 仅包含当前 Pixie 条目的用户问题。
        """
        async with self._semaphore:
            self._sample_index += 1
            sample_index = self._sample_index
            if self._halted:
                event = self._budget.record_standalone_event(
                    sample_index=sample_index,
                    status="BATCH_HALTED",
                )
                self._emit_execution_event(event)
                return

            self._budget.begin_sample(sample_index)
            try:
                with self._budget.request(reservation=_REQUEST_RESERVATION):
                    with real_model_factory_scope(budget=self._budget):
                        await asyncio.to_thread(
                            answer_archive_question,
                            user_id=_USER_ID,
                            project_id=_PROJECT_ID,
                            kb_id=_KB_ID,
                            question=args.question,
                            session=None,
                        )
                    event = self._budget.finish_sample(status="COMPLETED")
            except BudgetViolation:
                try:
                    event = self._budget.finish_sample(status="BUDGET_VIOLATION")
                except AcceptanceError:
                    event = self._budget.record_standalone_event(
                        sample_index=sample_index,
                        status="BUDGET_VIOLATION",
                    )
                self._emit_execution_event(event)
                raise
            except Exception:
                status = self._budget.failure_status()
                event = self._budget.finish_sample(status=status)
                self._halted = True
                self._emit_execution_event(event)
            else:
                self._emit_execution_event(event)

    @staticmethod
    def _emit_execution_event(event: dict[str, str | int]) -> None:
        """将预算器已白名单化的运行摘要写入 Pixie 输出 Wrap。

        Args:
            event: 仅含安全样本序号、模型角色、尝试数和固定状态的摘要。
        """
        eval_wrap(
            event,
            purpose="output",
            name="archive_world_bank_execution",
            description="只包含样本序号、固定阶段码和尝试数的安全执行观测。",
        )


class WorldBankFocusedQuestionRunnable(WorldBankQuestionRunnable):
    """为本轮三题对照固定九次预算，复用同一 FR-039 服务边界。"""

    attempt_cap: ClassVar[int] = 9
    halt_on_failure: ClassVar[bool] = True


__all__ = [
    "ATTEMPT_CAP",
    "WorldBankQuestionArgs",
    "WorldBankFocusedQuestionRunnable",
    "WorldBankQuestionRunnable",
]
