"""为档案真实模型评测提供隔离的调用预算与工厂作用域。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping

from langchain_core.runnables import Runnable

from app.services.archive import questions as questions_module
from app.services.infrastructure import ai_models as ai_models_module


_MODEL_ROLES = {"fr039_answer"}
_EXECUTION_STATUSES = {
    "COMPLETED",
    "PRE_MODEL_FAILED",
    "MODEL_FACTORY_FAILED",
    "PRE_INVOKE_FAILED",
    "MODEL_INVOKE_FAILED",
    "RESPONSE_PROCESSING_FAILED",
    "BATCH_HALTED",
    "BUDGET_VIOLATION",
}


class AcceptanceError(RuntimeError):
    """表示验收运行未满足预算或模型客户端约束。"""


class BudgetViolation(AcceptanceError):
    """表示请求预留或模型尝试违反了硬预算。"""


class AttemptBudget:
    """记录模型尝试数，并在真实调用前执行批次和请求上限。"""

    def __init__(self, *, cap: int) -> None:
        """初始化单批次预算。

        Args:
            cap: 本批次允许的模型调用尝试总数。
        """
        if cap <= 0:
            raise AcceptanceError("模型请求预算必须为正数。")
        self.cap = cap
        self.attempted = 0
        self.reserved = 0
        self._request_remaining = 0
        self._active_sample: dict[str, str | int] | None = None
        self.attempt_events: list[dict[str, str | int]] = []
        self.execution_events: list[dict[str, str | int]] = []
        self._sample_attempt_start = 0
        self._execution_phase = "BEFORE_MODEL"
        self._current_sample_index: int | None = None

    @contextmanager
    def request(
        self,
        *,
        reservation: int,
        sample: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        """为单个评测样本预留调用次数，并在结束时释放未使用额度。

        Args:
            reservation: 本次请求可使用的最大模型调用数。
            sample: 可选样本元数据；只保留整数样本序号。
        """
        if self._request_remaining:
            raise BudgetViolation("模型预算不允许嵌套请求预留。")
        if reservation <= 0 or self.attempted + self.reserved + reservation > self.cap:
            raise BudgetViolation("模型调用预算预留将超过批次硬上限。")

        self.reserved += reservation
        self._request_remaining = reservation
        self._active_sample = self._safe_sample(sample)
        try:
            yield
        finally:
            self.reserved -= self._request_remaining
            self._request_remaining = 0
            self._active_sample = None

    def consume(self, *, model_role: str) -> dict[str, str | int]:
        """在进入模型 Runnable 前扣减预算，超限时阻止委托调用。

        Args:
            model_role: 当前模型调用在问答流程中的职责。

        Returns:
            不含问题、回答或证据正文的安全调用事件。
        """
        if model_role not in _MODEL_ROLES:
            raise BudgetViolation("未知模型职责不能进入真实评测。")
        if self._request_remaining <= 0 or self.attempted >= self.cap:
            raise BudgetViolation("真实模型调用超过请求或批次预算，已在请求前阻断。")

        self._request_remaining -= 1
        self.reserved -= 1
        self.attempted += 1
        event: dict[str, str | int] = {}
        if self._current_sample_index is not None:
            event["sample_index"] = self._current_sample_index
        event["model_role"] = model_role
        event["attempt"] = self.attempted
        self._execution_phase = "MODEL_INVOKE"
        self.attempt_events.append(event)
        return event

    def begin_sample(self, sample_index: int) -> None:
        """为当前样本建立仅含序号的阶段观测上下文。

        Args:
            sample_index: 本批次运行顺序生成的正整数序号。
        """
        self._current_sample_index = sample_index
        self._sample_attempt_start = self.attempted
        self._execution_phase = "BEFORE_MODEL"

    def set_execution_phase(self, phase: str) -> None:
        """更新内部阶段标记；只接受固定阶段名称。

        Args:
            phase: 固定白名单中的内部阶段名。
        """
        if phase not in {
            "BEFORE_MODEL",
            "MODEL_FACTORY",
            "MODEL_READY",
            "MODEL_INVOKE",
            "MODEL_INVOKE_SUCCEEDED",
        }:
            raise AcceptanceError("评测阶段不在固定白名单中。")
        self._execution_phase = phase

    def failure_status(self) -> str:
        """将最后可观测阶段映射为固定失败类别，不暴露异常细节。"""
        return {
            "BEFORE_MODEL": "PRE_MODEL_FAILED",
            "MODEL_FACTORY": "MODEL_FACTORY_FAILED",
            "MODEL_READY": "PRE_INVOKE_FAILED",
            "MODEL_INVOKE": "MODEL_INVOKE_FAILED",
            "MODEL_INVOKE_SUCCEEDED": "RESPONSE_PROCESSING_FAILED",
        }[self._execution_phase]

    def finish_sample(self, *, status: str) -> dict[str, str | int]:
        """追加当前样本的安全摘要，并返回该摘要供 Pixie 输出。

        Args:
            status: 固定白名单中的样本执行状态。
        """
        if status not in _EXECUTION_STATUSES:
            raise AcceptanceError("评测状态不在固定白名单中。")
        event: dict[str, str | int] = {
            "sample_index": self._current_sample_index or 0,
            "model_role": "fr039_answer",
            "attempt_count": self.attempted - self._sample_attempt_start,
            "status": status,
        }
        self.execution_events.append(event)
        self._active_sample = None
        self._current_sample_index = None
        return event

    def record_standalone_event(
        self, *, sample_index: int, status: str
    ) -> dict[str, str | int]:
        """记录未进入样本预算上下文的熔断或预算事件。

        Args:
            sample_index: 本批次运行顺序生成的正整数序号。
            status: 固定白名单中的样本执行状态。
        """
        if status not in _EXECUTION_STATUSES:
            raise AcceptanceError("评测状态不在固定白名单中。")
        event: dict[str, str | int] = {
            "sample_index": sample_index,
            "model_role": "fr039_answer",
            "attempt_count": 0,
            "status": status,
        }
        self.execution_events.append(event)
        return event

    @staticmethod
    def _safe_sample(sample: Mapping[str, Any] | None) -> dict[str, str | int] | None:
        """仅从样本信息中提取整数序号，不保留案例名或内容字段。"""
        if sample is None:
            return None
        safe: dict[str, str | int] = {}
        value = sample.get("sample_index", sample.get("sample"))
        if isinstance(value, int) and not isinstance(value, bool):
            safe["sample_index"] = value
        return safe


class BudgetedRunnable(Runnable[Any, Any]):
    """在单次同步模型调用前扣减共享批次预算。"""

    def __init__(self, delegate: Any, budget: AttemptBudget, *, model_role: str) -> None:
        """保存真实模型委托和共享预算。

        Args:
            delegate: 由生产模型工厂创建的聊天模型。
            budget: 当前评测批次共享的调用预算。
            model_role: 本模型调用在评测中的职责名称。
        """
        self._delegate = delegate
        self._budget = budget
        self._model_role = model_role

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """计入一次真实模型尝试并透传同步调用。"""
        self._budget.consume(model_role=self._model_role)
        try:
            if config is None:
                result = self._delegate.invoke(input, **kwargs)
            else:
                result = self._delegate.invoke(input, config=config, **kwargs)
        except Exception:
            self._budget.set_execution_phase("MODEL_INVOKE")
            raise
        self._budget.set_execution_phase("MODEL_INVOKE_SUCCEEDED")
        return result

    def with_structured_output(self, schema: Any, **kwargs: Any) -> BudgetedRunnable:
        """包装结构化输出 Runnable，确保后续 invoke 继续受预算限制。

        Args:
            schema: 生产问答期望模型返回的结构化模式。
            **kwargs: 透传给底层聊天模型结构化输出适配器的选项。
        """
        structured = self._delegate.with_structured_output(schema, **kwargs)
        return BudgetedRunnable(
            structured,
            self._budget,
            model_role=self._model_role,
        )

    def __getattr__(self, name: str) -> Any:
        """读取包装器未实现的只读模型属性。"""
        return getattr(self._delegate, name)


@contextmanager
def real_model_factory_scope(
    *, budget: AttemptBudget, get_chat_model: Callable[..., Any] | None = None
) -> Iterator[None]:
    """在作用域内包装 FR-039 模型工厂并确认关闭客户端自动重试。

    Args:
        budget: 本次评测共用的模型尝试预算。
        get_chat_model: 测试专用工厂；真实评测必须使用生产工厂。
    """
    factory = get_chat_model or ai_models_module.get_chat_model
    previous_questions = questions_module.get_chat_model
    previous_infrastructure = ai_models_module.get_chat_model

    def create_model(*, max_retries: int | None = None) -> BudgetedRunnable:
        budget.set_execution_phase("MODEL_FACTORY")
        if max_retries not in (None, 0):
            raise AcceptanceError("真实模型工厂必须显式设置 max_retries=0。")
        model = factory(max_retries=0)
        if get_chat_model is None:
            client_retries = getattr(model, "max_retries", None)
            root_client_retries = getattr(
                getattr(model, "root_client", None), "max_retries", None
            )
            if client_retries != 0 or root_client_retries != 0:
                raise AcceptanceError("无法确认真实 DeepSeek 客户端已关闭隐藏重试。")
        budget.set_execution_phase("MODEL_READY")
        return BudgetedRunnable(model, budget, model_role="fr039_answer")

    questions_module.get_chat_model = create_model
    ai_models_module.get_chat_model = create_model
    try:
        yield
    finally:
        questions_module.get_chat_model = previous_questions
        ai_models_module.get_chat_model = previous_infrastructure


__all__ = [
    "AcceptanceError",
    "AttemptBudget",
    "BudgetedRunnable",
    "BudgetViolation",
    "real_model_factory_scope",
]
