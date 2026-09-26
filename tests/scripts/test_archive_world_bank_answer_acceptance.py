"""固定 World Bank 真实回答验收器的请求预算与脱敏约束。"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import archive_world_bank_answer_acceptance as acceptance


def test_sample_plan_has_exact_ten_cases_and_exact_worst_case_reservations() -> None:
    plan = acceptance.build_sample_plan()

    assert len(plan) == 10
    assert sum(item["case_id"] == "LUSHAN-01" for item in plan) == 6
    assert sum(item["case_id"] == "LUSHAN-04" for item in plan) == 2
    assert sum(item["case_id"] == "WB-UNSUPPORTED-DRAW-GATED-01" for item in plan) == 2
    assert sum(item["route"] == "FR039" for item in plan) == 5
    assert sum(item["route"] == "FR042" for item in plan) == 5
    assert sum(item["reservation"] for item in plan) == 45
    assert all(item["reservation"] == (1 if item["route"] == "FR039" else 8) for item in plan)
    assert all(
        item["require_tool_call"] is (item["route"] == "FR042")
        for item in plan
    )


def test_budget_counts_each_invoke_and_preserves_counting_through_bound_runnables() -> None:
    budget = acceptance.AttemptBudget(cap=45)

    class Runnable:
        def __init__(self, calls: list[object]) -> None:
            self.calls = calls

        def invoke(self, value: object, **kwargs: object) -> object:
            self.calls.append((value, kwargs))
            return "answer"

        def bind_tools(self, tools: list[object]) -> Runnable:
            self.calls.append(("bind_tools", tools))
            return self

        def bind(self, **kwargs: object) -> Runnable:
            self.calls.append(("bind", kwargs))
            return self

    calls: list[object] = []
    with budget.request(reservation=8):
        fr039_model = acceptance.BudgetedRunnable(
            Runnable(calls), budget, model_role="fr039_answer"
        )
        assert fr039_model.invoke("fr039 prompt") == "answer"
        fr042_model = acceptance.BudgetedRunnable(
            Runnable(calls), budget, model_role="fr042_evidence_judgment"
        )
        assert fr042_model.invoke("evidence judgment prompt") == "answer"
        bound = fr042_model.bind_tools(["tool"]).bind(stop=["END"])
        assert bound.invoke("fr042 prompt", config={"metadata": "kept"}) == "answer"

    assert budget.attempted == 3
    assert budget.reserved == 0
    assert [event["model_role"] for event in budget.attempt_events] == [
        "fr039_answer",
        "fr042_evidence_judgment",
        "fr042_graph_decision",
    ]
    assert [event["request_attempt"] for event in budget.attempt_events] == [1, 2, 3]
    assert calls == [
        ("fr039 prompt", {}),
        ("evidence judgment prompt", {}),
        ("bind_tools", ["tool"]),
        ("bind", {"stop": ["END"]}),
        ("fr042 prompt", {"config": {"metadata": "kept"}}),
    ]


def test_budget_rejects_invocation_beyond_request_reservation_before_delegate() -> None:
    budget = acceptance.AttemptBudget(cap=2)
    calls: list[object] = []

    class Runnable:
        def invoke(self, value: object) -> object:
            calls.append(value)
            return value

    with budget.request(reservation=1):
        model = acceptance.BudgetedRunnable(
            Runnable(), budget, model_role="fr039_answer"
        )
        assert model.invoke("first") == "first"
        with pytest.raises(acceptance.AcceptanceError):
            model.invoke("blocked")

    assert calls == ["first"]
    assert budget.attempted == 1


@pytest.mark.parametrize("transport_error_type", [TimeoutError, ConnectionError])
def test_retryable_exception_chain_reuses_logical_call_then_success_starts_new_call(
    transport_error_type: type[Exception],
) -> None:
    budget = acceptance.AttemptBudget(cap=5)

    class FlakyRunnable:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, _value: object) -> str:
            self.calls += 1
            if self.calls == 1:
                try:
                    raise transport_error_type("transport detail must not be recorded")
                except transport_error_type as error:
                    raise RuntimeError("transport wrapper") from error
            return "ok"

    with budget.request(reservation=5):
        model = acceptance.BudgetedRunnable(
            FlakyRunnable(), budget, model_role="fr042_graph_decision"
        )
        with pytest.raises(RuntimeError):
            model.invoke("first logical call")

        class SuccessfulRunnable:
            def invoke(self, _value: object) -> str:
                return "ok"

        evidence_model = acceptance.BudgetedRunnable(
            SuccessfulRunnable(), budget, model_role="fr042_evidence_judgment"
        )
        assert evidence_model.invoke("independent role call") == "ok"
        assert model.invoke("retry") == "ok"
        assert model.invoke("new logical call") == "ok"

    assert budget.attempted == 4
    events = budget.attempt_events
    assert events[0]["logical_call_id"] == events[2]["logical_call_id"]
    assert events[3]["logical_call_id"] != events[0]["logical_call_id"]
    assert [event["logical_attempt"] for event in events] == [1, 1, 2, 1]
    assert [event["completion_status"] for event in events] == [
        "retryable",
        "success",
        "success",
        "success",
    ]
    assert [event["model_role"] for event in events] == [
        "fr042_graph_decision",
        "fr042_evidence_judgment",
        "fr042_graph_decision",
        "fr042_graph_decision",
    ]
    assert [event["request_attempt"] for event in events] == [1, 2, 3, 4]
    assert "transport detail must not be recorded" not in str(events)


def test_non_retryable_exception_closes_logical_call_before_next_invoke() -> None:
    budget = acceptance.AttemptBudget(cap=3)

    class FlakyRunnable:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, _value: object) -> str:
            self.calls += 1
            if self.calls == 1:
                try:
                    raise ConnectionError("nested connection failure")
                except ConnectionError as error:
                    raise RuntimeError("FR039 wrapper") from error
            return "ok"

    with budget.request(reservation=3):
        model = acceptance.BudgetedRunnable(
            FlakyRunnable(), budget, model_role="fr039_answer"
        )
        with pytest.raises(RuntimeError):
            model.invoke("failed logical call")
        assert model.invoke("next logical call") == "ok"

    events = budget.attempt_events
    assert events[0]["logical_call_id"] != events[1]["logical_call_id"]
    assert [event["logical_attempt"] for event in events] == [1, 1]
    assert [event["completion_status"] for event in events] == ["final", "success"]


def test_judgment_role_uses_only_direct_builtin_connection_or_timeout_errors() -> None:
    budget = acceptance.AttemptBudget(cap=4)

    class SequenceRunnable:
        def __init__(self, outcomes: list[BaseException | None]) -> None:
            self.outcomes = outcomes

        def invoke(self, _value: object) -> str:
            outcome = self.outcomes.pop(0)
            if outcome is not None:
                raise outcome
            return "ok"

    try:
        raise TimeoutError("nested timeout")
    except TimeoutError as cause:
        chained_error = RuntimeError("judge wrapper")
        chained_error.__cause__ = cause
    judgment_error = acceptance.questions_module.ArchiveAnswerJudgmentError(
        "judgment wrapper", retryable=True
    )
    with budget.request(reservation=4):
        model = acceptance.BudgetedRunnable(
            SequenceRunnable(
                [
                    chained_error,
                    judgment_error,
                    ConnectionError("direct connection error"),
                    None,
                ]
            ),
            budget,
            model_role="fr042_evidence_judgment",
        )
        with pytest.raises(RuntimeError):
            model.invoke("chained error is not retryable for judge")
        with pytest.raises(acceptance.questions_module.ArchiveAnswerJudgmentError):
            model.invoke("judgment wrapper is not retryable")
        with pytest.raises(ConnectionError):
            model.invoke("direct builtin error is retryable")
        assert model.invoke("judge retry succeeds") == "ok"

    events = budget.attempt_events
    assert events[0]["logical_call_id"] != events[1]["logical_call_id"]
    assert events[1]["logical_call_id"] != events[2]["logical_call_id"]
    assert events[2]["logical_call_id"] == events[3]["logical_call_id"]
    assert [event["logical_attempt"] for event in events] == [1, 1, 1, 2]
    assert [event["completion_status"] for event in events] == [
        "final",
        "final",
        "retryable",
        "success",
    ]


@pytest.mark.parametrize(
    ("model_role", "expected_error"),
    [
        ("fr042_graph_decision", RuntimeError),
        ("fr042_evidence_judgment", ConnectionError),
    ],
)
def test_second_production_retry_attempt_is_final(
    model_role: str, expected_error: type[Exception]
) -> None:
    budget = acceptance.AttemptBudget(cap=3)

    class RepeatedFailureRunnable:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, _value: object) -> str:
            self.calls += 1
            if self.calls < 3:
                if model_role == "fr042_graph_decision":
                    try:
                        raise TimeoutError("graph transport failure")
                    except TimeoutError as cause:
                        raise RuntimeError("graph wrapper") from cause
                raise ConnectionError("direct judge connection failure")
            return "ok"

    with budget.request(reservation=3):
        model = acceptance.BudgetedRunnable(
            RepeatedFailureRunnable(), budget, model_role=model_role
        )
        for _ in range(2):
            with pytest.raises(expected_error):
                model.invoke("retry bound")
        assert model.invoke("new logical call after final failure") == "ok"

    events = budget.attempt_events
    assert events[0]["logical_call_id"] == events[1]["logical_call_id"]
    assert events[2]["logical_call_id"] != events[1]["logical_call_id"]
    assert [event["logical_attempt"] for event in events] == [1, 2, 1]
    assert [event["completion_status"] for event in events] == [
        "retryable",
        "final",
        "success",
    ]


def test_factory_patch_scope_restores_both_factories_even_when_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    originals = (lambda **_: "questions", lambda **_: "runtime")
    questions = SimpleNamespace(get_chat_model=originals[0])
    infrastructure = SimpleNamespace(get_chat_model=originals[1])
    monkeypatch.setattr(acceptance, "questions_module", questions)
    monkeypatch.setattr(acceptance, "ai_models_module", infrastructure)

    with pytest.raises(RuntimeError):
        with acceptance.real_model_factory_scope(
            budget=acceptance.AttemptBudget(cap=45),
            get_chat_model=lambda **_: SimpleNamespace(invoke=lambda _: None),
        ):
            assert questions.get_chat_model is not infrastructure.get_chat_model
            assert questions.get_chat_model().model_role == "fr039_answer"
            assert infrastructure.get_chat_model().model_role == "fr042_evidence_judgment"
            raise RuntimeError("scope exit")

    assert questions.get_chat_model is originals[0]
    assert infrastructure.get_chat_model is originals[1]


def test_refusal_requires_fixed_text_status_and_empty_citations() -> None:
    result = acceptance.assess_response(
        {
            "answer_status": "REFUSED_NO_EVIDENCE",
            "answer": "正式档案中没有足够依据。",
            "citations": [],
        },
        expected_status="REFUSED_NO_EVIDENCE",
    )

    assert result["passed"] is True
    assert result["fixed_refusal_text"] is True
    assert "answer" not in result
    assert acceptance.assess_response(
        {
            "answer_status": "REFUSED_NO_EVIDENCE",
            "answer": "无法确认。",
            "citations": [],
        },
        expected_status="REFUSED_NO_EVIDENCE",
    )["passed"] is False
    result.update(
        {
            "candidate_mapping_passed": None,
            "gate_matched": True,
            "tool_call_matched": True,
        }
    )
    assert acceptance.finalize_sample_quality(result, case_id="LUSHAN-04")["passed"] is True


def test_retrieval_preflight_reads_belarus_source_page_field(monkeypatch: pytest.MonkeyPatch) -> None:
    report = {
        "model_calls": 0,
        "cleanup": {
            "postgres_zero": True,
            "chroma_zero": True,
            "files_zero": True,
            "checkpoint_zero": True,
        },
        "source_manifest_checks": {"belarus_source_sha256_verified": True},
        "cases": [
            {
                "case_id": "LUSHAN-01",
                "production_top8": {"matches_reconstructed": True},
                "production_rerank": {"query_expression": "IBRD IDA"},
                "rankings": {"supplementary": [{"rank": 3, "public": True}]},
            },
            {
                "case_id": "WB-UNSUPPORTED-DRAW-GATED-01",
                "production_top8": {"matches_reconstructed": True},
                "production_rerank": {"query_expression": "IBRD IDA"},
                "rankings": {"supplementary": [{"rank": 7, "source_page": 16}]},
            },
        ],
    }
    monkeypatch.setattr(acceptance, "_read_json", lambda *_args, **_kwargs: report)
    verified = {
        "dataset": {},
        "lushan_manifest": {},
        "lushan_label": {},
        "belarus_manifest": {},
        "belarus_path": acceptance.Path("sources/belarus.pdf"),
    }

    assert set(acceptance._validate_retrieval_evidence(acceptance.Path("unused"), verified)) == {
        "LUSHAN-01",
        "WB-UNSUPPORTED-DRAW-GATED-01",
    }
    incomplete_verifier_result = dict(verified)
    incomplete_verifier_result.pop("belarus_path")
    with pytest.raises(acceptance.AcceptanceError, match="结构不完整"):
        acceptance._validate_retrieval_evidence(
            acceptance.Path("unused"), incomplete_verifier_result
        )
    report["cleanup"]["chroma_zero"] = False
    with pytest.raises(acceptance.AcceptanceError, match="清理"):
        acceptance._validate_retrieval_evidence(acceptance.Path("unused"), verified)


def test_candidate_trace_is_scoped_to_individual_repeated_requests() -> None:
    trace = acceptance.CandidateTrace()
    trace.record(
        case_id="LUSHAN-01",
        request_key="FR042-LUSHAN-01-1",
        query="q1",
        gate_question="g1",
        items=[],
    )
    trace.record(
        case_id="LUSHAN-01",
        request_key="FR042-LUSHAN-01-2",
        query="q2",
        gate_question="g2",
        items=[],
    )

    assert trace.assess_citations("FR042-LUSHAN-01-2", [{"filename": "stale.pdf"}])[
        "candidate_mapping_passed"
    ] is False


def test_answer_text_is_not_persisted_in_assessment() -> None:
    answer = "贷款批准金额为 US$300 million。请勿写入该完整正文。"
    assessment = acceptance.assess_response(
        {"answer_status": "ANSWERED", "answer": answer, "citations": []},
        expected_status="ANSWERED",
    )

    assert "answer" not in assessment
    assert assessment["amount_marker_present"] is True


def test_in_process_client_enters_lifespan_before_seed_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Client:
        def __init__(self, _app: object) -> None:
            events.append("constructed")

        def __enter__(self) -> Client:
            events.append("lifespan_started")
            return self

    monkeypatch.setattr(acceptance, "TestClient", Client)
    acceptance._make_in_process_api()

    assert events == ["constructed", "lifespan_started"]


def test_candidate_trace_records_only_safe_page_metadata_and_checks_citation_mapping() -> None:
    trace = acceptance.CandidateTrace()
    trace.record(
        case_id="LUSHAN-01",
        query="IBRD IDA",
        gate_question="世界银行贷款金额是多少？",
        items=[
            SimpleNamespace(
                filename="2016_project_appraisal_document.pdf",
                location_type=SimpleNamespace(value="PDF_PAGE"),
                location_start=16,
                location_end=16,
                excerpt="批准贷款金额为 US$300 million。",
                chunk_id="private-chunk-id",
                document_id="private-document-id",
            )
        ],
    )
    citation = {
        "filename": "2016_project_appraisal_document.pdf",
        "location_type": "PDF_PAGE",
        "location_start": 16,
        "location_end": 16,
        "excerpt": "批准贷款金额为 US$300 million。",
    }

    assessment = trace.assess_citations("LUSHAN-01", [citation])
    serialized = str(trace.safe_records)

    assert assessment["candidate_mapping_passed"] is True
    assert assessment["tool_call_matched"] is True
    assert assessment["gate_matched"] is True
    assert "private source text" not in serialized
    assert "private-chunk-id" not in serialized
    assert "private-document-id" not in serialized
    assert trace.safe_records[0]["gate_triggered"] is True


def test_unmapped_citation_fails_and_no_evidence_case_requires_empty_citations() -> None:
    trace = acceptance.CandidateTrace()
    trace.record(case_id="LUSHAN-01", query="q", gate_question="g", items=[])
    trace.record(
        case_id="LUSHAN-04",
        request_key="FR042-LUSHAN-04-with-citation",
        query="项目问题",
        gate_question="项目问题",
        items=[],
    )

    assert trace.assess_citations("LUSHAN-01", [{"filename": "other.pdf"}])[
        "candidate_mapping_passed"
    ] is False
    refusal_result = trace.assess_citations(
        "LUSHAN-04",
        [{"filename": "noise.pdf"}],
        request_key="FR042-LUSHAN-04-with-citation",
        require_tool_call=True,
        expected_gate_triggered=False,
    )
    assert refusal_result["candidate_mapping_passed"] is None
    assert refusal_result["tool_call_matched"] is True
    assert refusal_result["gate_matched"] is True
    assert refusal_result["citation_count"] == 1

    no_tool_refusal = trace.assess_citations(
        "LUSHAN-04",
        [],
        request_key="FR042-LUSHAN-04-no-tool",
        require_tool_call=True,
        expected_gate_triggered=False,
    )
    assert no_tool_refusal["tool_called"] is False
    assert no_tool_refusal["candidate_mapping_passed"] is None
    assert no_tool_refusal["tool_call_matched"] is False

    trace.record(
        case_id="LUSHAN-04",
        request_key="FR042-LUSHAN-04-with-tool",
        query="项目问题",
        gate_question="项目问题",
        items=[],
    )
    retrieved_empty_result = trace.assess_citations(
        "LUSHAN-04",
        [],
        request_key="FR042-LUSHAN-04-with-tool",
        require_tool_call=True,
        expected_gate_triggered=False,
    )
    assert retrieved_empty_result["tool_called"] is True
    assert retrieved_empty_result["candidate_mapping_passed"] is None
    assert retrieved_empty_result["tool_call_matched"] is True


def test_candidate_mapping_diagnostic_is_independent_of_gate_and_tool_quality_gates() -> None:
    trace = acceptance.CandidateTrace()
    trace.record(
        case_id="LUSHAN-01",
        query="贷款金额",
        gate_question="世界银行贷款金额是多少？",
        request_key="mapped-gate-mismatch",
        items=[
            SimpleNamespace(
                filename="evidence.pdf",
                location_type=SimpleNamespace(value="PDF_PAGE"),
                location_start=1,
                location_end=1,
                excerpt="批准贷款金额为 US$300 million。",
            )
        ],
    )
    assessment = trace.assess_citations(
        "LUSHAN-01",
        [
            {
                "filename": "evidence.pdf",
                "location_type": "PDF_PAGE",
                "location_start": 1,
                "location_end": 1,
                "excerpt": "批准贷款金额为 US$300 million。",
            }
        ],
        request_key="mapped-gate-mismatch",
        require_tool_call=True,
        expected_gate_triggered=False,
    )

    assert assessment["candidate_mapping_passed"] is True
    assert assessment["tool_call_matched"] is True
    assert assessment["gate_matched"] is False
    quality = acceptance.finalize_sample_quality(
        {
            **assessment,
            "status_matched": True,
            "amount_marker_present": True,
            "frozen_evidence_supported": True,
        },
        case_id="LUSHAN-01",
    )
    assert quality["passed"] is False


def test_unmapped_positive_citation_fails_even_when_tool_and_gate_match() -> None:
    trace = acceptance.CandidateTrace()
    trace.record(
        case_id="LUSHAN-01",
        query="贷款金额",
        gate_question="世界银行贷款金额是多少？",
        request_key="unmapped-matching-gates",
        items=[
            SimpleNamespace(
                filename="evidence.pdf",
                location_type=SimpleNamespace(value="PDF_PAGE"),
                location_start=1,
                location_end=1,
                excerpt="批准贷款金额为 US$300 million。",
            )
        ],
    )
    assessment = trace.assess_citations(
        "LUSHAN-01",
        [{"filename": "other.pdf", "location_type": "PDF_PAGE", "location_start": 1, "location_end": 1}],
        request_key="unmapped-matching-gates",
        require_tool_call=True,
        expected_gate_triggered=True,
    )

    assert assessment["candidate_mapping_passed"] is False
    assert assessment["tool_call_matched"] is True
    assert assessment["gate_matched"] is True
    quality = acceptance.finalize_sample_quality(
        {
            **assessment,
            "status_matched": True,
            "amount_marker_present": True,
            "frozen_evidence_supported": True,
        },
        case_id="LUSHAN-01",
    )
    assert quality["passed"] is False


@pytest.mark.parametrize(
    ("tool_call_matched", "gate_matched", "expected_passed"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_refusal_candidate_mapping_is_not_applicable_but_tool_and_gate_are_quality_gates(
    tool_call_matched: bool, gate_matched: bool, expected_passed: bool
) -> None:
    result = acceptance.finalize_sample_quality(
        {
            "status_matched": True,
            "fixed_refusal_text": True,
            "empty_citations": True,
            "candidate_mapping_passed": None,
            "tool_call_matched": tool_call_matched,
            "gate_matched": gate_matched,
        },
        case_id="LUSHAN-04",
    )

    assert result["passed"] is expected_passed


def test_fr039_keeps_frozen_evidence_quality_without_fr042_mapping_metric() -> None:
    result = acceptance.finalize_sample_quality(
        {
            "status_matched": True,
            "amount_marker_present": True,
            "frozen_evidence_supported": True,
        },
        case_id="LUSHAN-01",
    )

    assert result["passed"] is True
    assert "mapped_to_candidate" not in result


def test_cleanup_failure_summary_writes_only_safe_step_codes(
    tmp_path: acceptance.Path,
) -> None:
    error = RuntimeError("sensitive database detail and credential")
    error.cleanup_failed_steps = ("API_CLOSE", "sensitive raw failure")
    summary: dict[str, object] = {}
    result_path = tmp_path / "summary.json"

    acceptance._record_cleanup_failure(summary, error)
    acceptance._write_partial_result(result_path, summary)

    serialized = result_path.read_text(encoding="utf-8")
    assert summary["cleanup_failed_steps"] == ["API_CLOSE", "UNKNOWN"]
    assert "sensitive database detail" not in serialized
    assert "credential" not in serialized
    assert "sensitive raw failure" not in serialized


def test_unstructured_cleanup_failure_uses_fixed_unknown_code(
    tmp_path: acceptance.Path,
) -> None:
    error = RuntimeError("unstructured private exception")
    summary: dict[str, object] = {}
    result_path = tmp_path / "summary.json"

    acceptance._record_cleanup_failure(summary, error)
    acceptance._write_partial_result(result_path, summary)

    serialized = result_path.read_text(encoding="utf-8")
    assert summary["cleanup_failed_steps"] == ["UNKNOWN"]
    assert "unstructured private exception" not in serialized
