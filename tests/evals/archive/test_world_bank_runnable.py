"""验证 FR-039 世界银行问答 Runnable 的确定性边界。"""

import asyncio
from contextlib import contextmanager
import threading
import time
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.archive import questions as questions_module
from app.services.infrastructure import ai_models as ai_models_module
from scripts.archive_model_budget import AcceptanceError
from evals.archive.world_bank_runnable import (
    ATTEMPT_CAP,
    WorldBankQuestionArgs,
    WorldBankQuestionRunnable,
)
from evals.archive import world_bank_runnable as runnable_module


def test_runnable_input_contains_only_the_user_question() -> None:
    """候选必须由 Pixie wrap 注入，不能成为模型用户输入字段。"""
    assert set(WorldBankQuestionArgs.model_fields) == {"question"}
    with pytest.raises(ValidationError):
        WorldBankQuestionArgs.model_validate(
            {"question": "测试问题", "candidates": [{"excerpt": "测试片段"}]}
        )


def test_runnable_has_fixed_batch_cap_and_serial_semaphore() -> None:
    """每个实例固定最多 21 次尝试，并将异步并发度限制为一。"""
    runnable = WorldBankQuestionRunnable.create()

    assert ATTEMPT_CAP == 21
    assert runnable._budget.cap == 21
    assert runnable._semaphore._value == 1


def test_focused_runnable_has_separate_nine_attempt_cap() -> None:
    """聚焦对照使用独立九次预算，旧扩展入口仍保持二十一次。"""
    focused_runnable_type = getattr(
        runnable_module, "WorldBankFocusedQuestionRunnable", None
    )

    assert focused_runnable_type is not None
    assert WorldBankQuestionRunnable.create()._budget.cap == 21
    assert focused_runnable_type.create()._budget.cap == 9


def test_focused_budget_exhaustion_blocks_before_factory_and_answer_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聚焦预算耗尽时必须在真实工厂作用域和回答服务前拒绝。"""
    focused_runnable_type = getattr(
        runnable_module, "WorldBankFocusedQuestionRunnable", None
    )
    assert focused_runnable_type is not None

    runnable = focused_runnable_type.create()
    runnable._budget.attempted = 9
    entered_factory_scope = False
    answer_called = False

    @contextmanager
    def forbidden_factory_scope(**_kwargs):
        nonlocal entered_factory_scope
        entered_factory_scope = True
        yield

    def forbidden_answer(**_kwargs):
        nonlocal answer_called
        answer_called = True

    monkeypatch.setattr(
        runnable_module, "real_model_factory_scope", forbidden_factory_scope
    )
    monkeypatch.setattr(runnable_module, "answer_archive_question", forbidden_answer)

    with pytest.raises(AcceptanceError):
        asyncio.run(runnable.run(WorldBankQuestionArgs(question="预算边界问题")))

    assert entered_factory_scope is False
    assert answer_called is False
    assert runnable._budget.attempted == 9
    assert runnable._budget.execution_events[-1] == {
        "sample_index": 1,
        "model_role": "fr039_answer",
        "attempt_count": 0,
        "status": "BUDGET_VIOLATION",
    }


@pytest.mark.parametrize(
    ("failure_point", "expected_status", "expected_attempts"),
    [
        ("pre_model", "PRE_MODEL_FAILED", 0),
        ("factory", "MODEL_FACTORY_FAILED", 0),
        ("invoke", "MODEL_INVOKE_FAILED", 1),
        ("after_invoke", "RESPONSE_PROCESSING_FAILED", 1),
    ],
)
def test_focused_failure_is_scored_safely_and_halts_later_samples(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    expected_status: str,
    expected_attempts: int,
) -> None:
    """聚焦批次将首个故障记为安全观测并阻止后续服务/模型调用。"""
    from evals.archive import world_bank_runnable as runnable_module

    focused_type = runnable_module.WorldBankFocusedQuestionRunnable
    observations: list[dict[str, object]] = []
    service_calls = 0

    def capture_wrap(data, *, purpose, name, description):
        if purpose == "output" and name == "archive_world_bank_execution":
            observations.append(data)
        return data

    class FailingModel:
        max_retries = 0
        root_client = type("RootClient", (), {"max_retries": 0})()

        def invoke(self, _input, **_kwargs):
            if failure_point == "invoke":
                raise RuntimeError("SENSITIVE_MODEL_EXCEPTION")
            return {"ok": True}

    def model_factory(**_kwargs):
        if failure_point == "factory":
            raise RuntimeError("SENSITIVE_FACTORY_EXCEPTION")
        return FailingModel()

    def answer(**_kwargs):
        nonlocal service_calls
        service_calls += 1
        if failure_point == "pre_model":
            raise RuntimeError("SENSITIVE_PRE_MODEL_EXCEPTION")
        questions_module.get_chat_model().invoke("SENSITIVE_PROMPT")
        if failure_point == "after_invoke":
            raise RuntimeError("SENSITIVE_RESPONSE_EXCEPTION")

    monkeypatch.setattr(runnable_module, "eval_wrap", capture_wrap)
    monkeypatch.setattr(ai_models_module, "get_chat_model", model_factory)
    monkeypatch.setattr(runnable_module, "answer_archive_question", answer)
    runnable = focused_type.create()

    async def run_two() -> None:
        await runnable.run(WorldBankQuestionArgs(question="PRIVATE_QUESTION_1"))
        await runnable.run(WorldBankQuestionArgs(question="PRIVATE_QUESTION_2"))

    asyncio.run(run_two())

    assert service_calls == 1
    assert [item["status"] for item in observations] == [
        expected_status,
        "BATCH_HALTED",
    ]
    assert observations[0]["attempt_count"] == expected_attempts
    assert observations[1]["attempt_count"] == 0
    assert "SENSITIVE_" not in str(observations)
    assert runnable._budget.attempted == expected_attempts


def test_legacy_runnable_still_raises_on_service_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧 21 次入口保持原先的整条 Runnable 失败行为。"""
    def fail(**_kwargs):
        raise RuntimeError("SENSITIVE_LEGACY_FAILURE")

    monkeypatch.setattr(runnable_module, "answer_archive_question", fail)
    runnable = WorldBankQuestionRunnable.create()

    with pytest.raises(RuntimeError, match="RUNNABLE_FAILED"):
        asyncio.run(runnable.run(WorldBankQuestionArgs(question="旧入口问题")))


def test_focused_ordinary_question_reserves_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聚焦入口对普通单题只预留一次，结束后释放未使用额度。"""
    focused_runnable_type = getattr(
        runnable_module, "WorldBankFocusedQuestionRunnable", None
    )
    assert focused_runnable_type is not None

    observed: list[tuple[int, int]] = []

    @contextmanager
    def inspect_factory_scope(*, budget, get_chat_model=None):
        observed.append((budget.reserved, budget._request_remaining))
        yield

    monkeypatch.setattr(
        runnable_module, "real_model_factory_scope", inspect_factory_scope
    )
    monkeypatch.setattr(
        runnable_module, "answer_archive_question", lambda **_kwargs: None
    )
    runnable = focused_runnable_type.create()

    asyncio.run(runnable.run(WorldBankQuestionArgs(question="一个普通问题")))

    assert observed == [(1, 1)]
    assert runnable._budget.reserved == 0
    assert runnable._budget.attempted == 0


def test_focused_success_emits_one_safe_execution_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功样本也输出序号、角色、实际尝试数和固定状态。"""
    from evals.archive import world_bank_runnable as runnable_module

    focused_type = runnable_module.WorldBankFocusedQuestionRunnable
    observations: list[dict[str, object]] = []
    runnable = focused_type.create()

    def capture_wrap(data, *, purpose, name, description):
        if purpose == "output" and name == "archive_world_bank_execution":
            observations.append(data)
        return data

    @contextmanager
    def no_model_scope(**_kwargs):
        yield

    def successful_answer(**_kwargs):
        runnable._budget.consume(model_role="fr039_answer")

    monkeypatch.setattr(runnable_module, "eval_wrap", capture_wrap)
    monkeypatch.setattr(runnable_module, "real_model_factory_scope", no_model_scope)
    monkeypatch.setattr(runnable_module, "answer_archive_question", successful_answer)

    asyncio.run(runnable.run(WorldBankQuestionArgs(question="仅用于确定性测试的问题")))

    assert observations == [
        {
            "sample_index": 1,
            "model_role": "fr039_answer",
            "attempt_count": 1,
            "status": "COMPLETED",
        }
    ]


def test_run_calls_production_question_service_and_uses_retrieval_wrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产问答入口应从唯一候选输入边界接收空候选并确定性拒答。"""
    wrapped: list[tuple[str, str]] = []
    calls: list[dict[str, object]] = []

    def fake_eval_wrap(data, *, purpose, name, description):
        if purpose == "input":
            wrapped.append((purpose, name))

            def injected_retrieval(**_scope):
                return {"items": [], "requested_top_k": 8, "returned_count": 0}

            return injected_retrieval
        if name == "archive_question_response":
            calls.append({"response": data})
        return data

    monkeypatch.setattr(questions_module, "eval_wrap", fake_eval_wrap)
    runnable = WorldBankQuestionRunnable.create()

    asyncio.run(runnable.run(WorldBankQuestionArgs(question="固定测试问题")))

    assert wrapped == [("input", "archive_question_retrieval")]
    assert len(calls) == 1
    assert calls[0]["response"]["answer_status"] == "REFUSED_NO_EVIDENCE"


def test_run_uses_fixed_server_scope_and_one_attempt_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runnable 只传固定服务端范围，并为每题预留一次模型尝试。"""
    observed: dict[str, object] = {}
    from evals.archive import world_bank_runnable as runnable_module

    @contextmanager
    def recording_scope(*, budget, get_chat_model=None):
        observed["budget"] = budget
        observed["test_factory_override"] = get_chat_model
        yield

    def fake_answer(**kwargs):
        observed["scope"] = (
            kwargs["user_id"], kwargs["project_id"], kwargs["kb_id"],
            kwargs["question"], kwargs["session"],
        )

    monkeypatch.setattr(runnable_module, "real_model_factory_scope", recording_scope)
    monkeypatch.setattr(runnable_module, "answer_archive_question", fake_answer)
    runnable = WorldBankQuestionRunnable.create()

    asyncio.run(runnable.run(WorldBankQuestionArgs(question="不含候选的用户问题")))

    assert observed["scope"] == (
        UUID("00000000-0000-4000-8000-0000000000d5"),
        UUID("00000000-0000-4000-8000-0000000000d6"),
        UUID("00000000-0000-4000-8000-0000000000d7"),
        "不含候选的用户问题",
        None,
    )
    assert observed["budget"] is runnable._budget
    assert observed["test_factory_override"] is None
    assert runnable._budget.reserved == 0


def test_budget_exhaustion_blocks_before_scope_and_answer_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 22 次尝试在进入模型工厂作用域和生产服务前被阻断。"""
    from evals.archive import world_bank_runnable as runnable_module

    runnable = WorldBankQuestionRunnable.create()
    runnable._budget.attempted = ATTEMPT_CAP
    entered_scope = False
    answer_called = False

    @contextmanager
    def forbidden_scope(**_kwargs):
        nonlocal entered_scope
        entered_scope = True
        yield

    def forbidden_answer(**_kwargs):
        nonlocal answer_called
        answer_called = True

    monkeypatch.setattr(runnable_module, "real_model_factory_scope", forbidden_scope)
    monkeypatch.setattr(runnable_module, "answer_archive_question", forbidden_answer)

    with pytest.raises(AcceptanceError):
        asyncio.run(runnable.run(WorldBankQuestionArgs(question="预算边界问题")))

    assert entered_scope is False
    assert answer_called is False
    assert runnable._budget.attempted == ATTEMPT_CAP


def test_semaphore_keeps_answer_service_calls_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个重叠 Pixie 调用也只能串行进入生产问答服务。"""
    from evals.archive import world_bank_runnable as runnable_module

    active = 0
    peak = 0
    lock = threading.Lock()

    @contextmanager
    def no_model_scope(**_kwargs):
        yield

    def observed_answer(**_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    monkeypatch.setattr(runnable_module, "real_model_factory_scope", no_model_scope)
    monkeypatch.setattr(runnable_module, "answer_archive_question", observed_answer)
    runnable = WorldBankQuestionRunnable.create()

    async def run_pair() -> None:
        await asyncio.gather(
            runnable.run(WorldBankQuestionArgs(question="问题一")),
            runnable.run(WorldBankQuestionArgs(question="问题二")),
        )

    asyncio.run(run_pair())

    assert peak == 1
    assert runnable._budget.attempted == 0


def test_real_factory_scope_requests_zero_retries_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实工厂适配层必须收到 max_retries=0，测试替身不得发出请求。"""
    from evals.archive import world_bank_runnable as runnable_module

    factory_calls: list[dict[str, object]] = []

    class FakeModel:
        max_retries = 0
        root_client = type("RootClient", (), {"max_retries": 0})()

        def invoke(self, _input, **_kwargs):
            return {"test_only": True}

    def fake_factory(**kwargs):
        factory_calls.append(kwargs)
        return FakeModel()

    monkeypatch.setattr(ai_models_module, "get_chat_model", fake_factory)
    runnable = WorldBankQuestionRunnable.create()

    def call_configured_factory(**kwargs):
        questions_module.get_chat_model().invoke("不发送网络请求")

    monkeypatch.setattr(runnable_module, "answer_archive_question", call_configured_factory)
    asyncio.run(runnable.run(WorldBankQuestionArgs(question="仅验证模型工厂配置")))

    assert factory_calls == [{"max_retries": 0}]
    assert runnable._budget.attempted == 1
