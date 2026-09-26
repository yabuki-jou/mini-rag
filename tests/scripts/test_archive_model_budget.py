"""验证 FR-039 专用模型尝试预算和导入隔离。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.archive_model_budget import (
    AcceptanceError,
    AttemptBudget,
    BudgetedRunnable,
    real_model_factory_scope,
)


ROOT = Path(__file__).resolve().parents[2]


def test_import_does_not_load_fastapi_app_or_install_application_logging() -> None:
    """预算模块导入必须保持纯粹，不触发 app.main 的日志副作用。"""
    code = (
        "import logging, sys; "
        "before = tuple(logging.getLogger().handlers); "
        "import scripts.archive_model_budget; "
        "assert 'app.main' not in sys.modules; "
        "assert tuple(logging.getLogger().handlers) == before; "
        "assert not any(getattr(h, '_mini_rag_handler', False) "
        "for h in logging.getLogger().handlers)"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0


def test_attempt_budget_blocks_delegate_before_exceeding_request_reservation() -> None:
    """单请求只预留一次，第二次 invoke 必须在委托前被阻断。"""
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, value, **_kwargs):
            self.calls += 1
            return value

    budget = AttemptBudget(cap=21)
    delegate = Delegate()
    runnable = BudgetedRunnable(delegate, budget, model_role="fr039_answer")

    with budget.request(reservation=1):
        assert runnable.invoke("one") == "one"
        with pytest.raises(AcceptanceError):
            runnable.invoke("two")

    assert delegate.calls == 1
    assert budget.attempted == 1
    assert budget.reserved == 0


def test_execution_events_only_contain_fixed_safe_fields() -> None:
    """运行观测只保留序号、白名单角色/状态和真实尝试数。"""
    budget = AttemptBudget(cap=2)
    with budget.request(
        reservation=1,
        sample={"sample": 4, "case_id": "DO_NOT_KEEP", "question": "DO_NOT_KEEP"},
    ):
        budget.begin_sample(4)
        budget.consume(model_role="fr039_answer")
        budget.finish_sample(status="MODEL_INVOKE_FAILED")

    assert budget.execution_events == [
        {
            "sample_index": 4,
            "model_role": "fr039_answer",
            "attempt_count": 1,
            "status": "MODEL_INVOKE_FAILED",
        }
    ]
    assert "DO_NOT_KEEP" not in str(budget.execution_events)


def test_structured_output_runnable_remains_inside_budget_guard() -> None:
    """生产问答的结构化输出转换后仍须经过预算包装器。"""
    class StructuredDelegate:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, value, **_kwargs):
            self.calls += 1
            return value

    class ChatDelegate(StructuredDelegate):
        def with_structured_output(self, _schema, **_kwargs):
            return StructuredDelegate()

    budget = AttemptBudget(cap=1)
    wrapped = BudgetedRunnable(
        ChatDelegate(), budget, model_role="fr039_answer"
    )
    with budget.request(reservation=1):
        structured = wrapped.with_structured_output(dict)
        assert structured.invoke("structured") == "structured"

    assert budget.attempted == 1


def test_batch_cap_rejects_reservation_before_request_body() -> None:
    """批次预算无法完整预留时，请求体和模型委托均不应运行。"""
    budget = AttemptBudget(cap=1)
    delegate = BudgetedRunnable(
        type("Delegate", (), {"invoke": lambda self, value, **_kwargs: value})(),
        budget,
        model_role="fr039_answer",
    )
    with budget.request(reservation=1):
        assert delegate.invoke("first") == "first"

    with pytest.raises(AcceptanceError):
        with budget.request(reservation=1):
            pytest.fail("超限请求体不应执行")

    assert budget.attempted == 1
    assert budget.reserved == 0


def test_real_model_scope_sets_zero_retries_and_restores_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-039 工厂明确关闭客户端重试，并在异常退出后恢复原函数。"""
    from app.services.archive import questions as questions_module
    from app.services.infrastructure import ai_models as ai_models_module

    previous_questions_factory = questions_module.get_chat_model
    previous_runtime_factory = ai_models_module.get_chat_model
    calls: list[dict[str, object]] = []

    class FakeDelegate:
        def invoke(self, value, **_kwargs):
            return value

    def fake_factory(**kwargs):
        calls.append(kwargs)
        return FakeDelegate()

    monkeypatch.setattr(ai_models_module, "get_chat_model", previous_runtime_factory)
    budget = AttemptBudget(cap=1)
    with pytest.raises(RuntimeError):
        with real_model_factory_scope(budget=budget, get_chat_model=fake_factory):
            with budget.request(reservation=1):
                model = questions_module.get_chat_model()
                assert model.invoke("safe test") == "safe test"
                raise RuntimeError("expected test sentinel")

    assert calls == [{"max_retries": 0}]
    assert questions_module.get_chat_model is previous_questions_factory
    assert ai_models_module.get_chat_model is previous_runtime_factory
    assert budget.attempted == 1
    assert budget.reserved == 0
