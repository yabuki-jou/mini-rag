"""验证 FR-042 P05 Archive Graph 的受控工具循环和可信最终投影。"""

from collections.abc import Sequence
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.archive.graph import (
    ArchiveAgentDependencyError,
    ArchiveAgentModelOutputError,
)
from app.agents.archive.history import project_complete_archive_messages
from app.agents.archive.runtime import build_archive_runtime
from app.models import EvidenceLocationType
from app.schemas.archive_question import ArchiveAnswerStatus
from app.schemas.archive_retrieval import ArchiveRetrievalItemRead, ArchiveRetrievalResponse
from app.services.archive import catalog as catalog_service
from app.services.archive import retrieval as retrieval_service
from app.services.archive.catalog import AgentCatalogPage


@pytest.fixture(autouse=True)
def _default_confirmed_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Graph 单测中的伪造检索候选默认属于当前正式范围。"""
    monkeypatch.setattr(
        catalog_service,
        "list_agent_confirmed_document_titles",
        lambda *, document_ids, **_: {
            document_id: None for document_id in document_ids
        },
    )


class StubChatModel:
    """按顺序返回消息或异常，并记录每次模型输入。"""

    def __init__(self, responses: Sequence[AIMessage | Exception]):
        self.responses = list(responses)
        self.invocations: list[list[Any]] = []
        self.bound_tools: tuple[Any, ...] = ()

    def bind_tools(self, tools: Sequence[Any]) -> "StubChatModel":
        """记录模型可见工具并返回当前桩。"""
        self.bound_tools = tuple(tools)
        return self

    def invoke(self, messages: Any) -> AIMessage:
        """消费一个预设结果。"""
        self.invocations.append(list(messages) if not isinstance(messages, str) else [messages])
        if not self.responses:
            raise AssertionError("模拟模型没有剩余响应。")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _tool_call(name: str, call_id: str, args: dict[str, Any]) -> AIMessage:
    """构造一个模型工具调用消息。"""
    return AIMessage(
        content="模型自由文本不得成为最终回答",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _candidate(index: int = 1) -> ArchiveRetrievalItemRead:
    """构造含内部标识和分数的原始候选。"""
    return ArchiveRetrievalItemRead(
        chunk_id=f"{index:064x}",
        document_id=uuid4(),
        filename=f"档案-{index}.txt",
        location_type=EvidenceLocationType.TEXT_LINE_RANGE,
        location_start=index,
        location_end=index,
        excerpt=f"原文证据-{index}",
        score=0.91,
        reranker_score=0.87,
    )


def _catalog_page(*, empty: bool = False) -> AgentCatalogPage:
    """构造目录工具的安全分页结果。"""
    return AgentCatalogPage(
        page=1,
        page_size=20,
        total=0 if empty else 1,
        items=[] if empty else [
            {
                "document_ref": "A1",
                "filename": "目录档案.txt",
                "confirmed_at": "2026-09-15T00:00:00+00:00",
                "fields": {
                    "TITLE": {"value": "施工方案", "source": "MANUAL", "has_source_evidence": True},
                    "DOCUMENT_TYPE": {"value": "CONSTRUCTION", "source": "MANUAL", "has_source_evidence": True},
                    "DOCUMENT_DATE": {"value": None, "source": None, "has_source_evidence": False},
                    "AUTHORING_ORGANIZATION": {"value": "示例单位", "source": "AI", "has_source_evidence": True},
                    "PROJECT_STAGE": {"value": "CONSTRUCTION", "source": "MANUAL", "has_source_evidence": True},
                },
            }
        ],
    )


def _runtime(tmp_path, model: StubChatModel, judge_model: StubChatModel):
    """使用隔离 Checkpoint 和无数据库操作的 Session 工厂创建 Runtime。"""
    return build_archive_runtime(
        user_id=uuid4(),
        project_id=uuid4(),
        kb_id=uuid4(),
        session_factory=lambda: nullcontext(SimpleNamespace()),
        model=model,
        judge_model=judge_model,
        checkpoint_path=tmp_path / f"archive-{uuid4()}.db",
    )


def _invoke(runtime, question: str = "查询项目档案"):
    """以 Runtime 绑定的可信范围执行一轮。"""
    return runtime.invoke(
        {
            "messages": [HumanMessage(content=question)],
            "user_id": str(runtime.user_id),
            "project_id": str(runtime.project_id),
            "kb_id": str(runtime.kb_id),
            "tool_call_count": 0,
        },
        thread_id=f"thread-{uuid4()}",
    )


def test_zero_tool_call_is_fixed_refusal_and_ignores_model_text(tmp_path) -> None:
    """没有成功工具结果时不得把模型自由文本作为回答。"""
    model = StubChatModel([AIMessage(content="我猜项目已经验收。")])
    judge = StubChatModel([])
    with _runtime(tmp_path, model, judge) as runtime:
        result = _invoke(runtime)

    assert result.turn.answer_status == ArchiveAnswerStatus.REFUSED_NO_EVIDENCE
    assert result.turn.answer == "正式档案中没有足够依据。"
    assert result.turn.citations == ()
    assert result.turn.answer_kind == "REFUSED"
    assert result.state["tool_call_count"] == 0
    assert [message.content for message in result.state["messages"]] == [
        "查询项目档案",
        "正式档案中没有足够依据。",
    ]
    assert judge.invocations == []


@pytest.mark.parametrize(
    ("tool_name", "args", "expected_text"),
    [
        ("list_formal_archives", {}, "第 1 页，本页 1 份，共 1 份："),
        ("search_confirmed_archive_evidence", {"query": "  编制单位是谁？\r\n"}, "示例单位"),
    ],
)
def test_single_tool_call_uses_trusted_final_projection(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    args: dict[str, Any],
    expected_text: str,
) -> None:
    """目录和证据分支都必须由服务端最终化并只持久一条最终 AIMessage。"""
    candidate = _candidate()
    monkeypatch.setattr(catalog_service, "list_agent_formal_archives", lambda **_: _catalog_page())
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: ArchiveRetrievalResponse(items=[candidate], requested_top_k=8, returned_count=1),
    )
    model = StubChatModel([
        _tool_call(tool_name, "call-1", args),
        AIMessage(content="未经判定的工具循环文本"),
    ])
    judge = StubChatModel([
        AIMessage(
            content='{"decision":"ANSWERED","answer":"编制单位是示例单位。","citation_numbers":[1]}'
        )
    ])
    with _runtime(tmp_path, model, judge) as runtime:
        result = _invoke(runtime, "编制单位是谁？")

    assert expected_text in result.turn.answer
    assert result.turn.answer_status == ArchiveAnswerStatus.ANSWERED
    assert result.state["tool_call_count"] == 1
    assert result.state["messages"][-1].content == result.turn.answer
    assert sum(isinstance(message, AIMessage) for message in result.state["messages"]) == 1
    assert not any(isinstance(message, ToolMessage) for message in result.state["messages"])
    if tool_name == "search_confirmed_archive_evidence":
        assert result.turn.citations[0].filename == candidate.filename
        assert set(result.turn.citations[0].model_dump()) == {
            "filename",
            "location_type",
            "location_start",
            "location_end",
            "excerpt",
        }
    else:
        assert result.turn.citations == ()
        assert judge.invocations == []


@pytest.mark.parametrize(
    ("first_tool", "second_tool", "expected_kind"),
    [
        ("list_formal_archives", "search_confirmed_archive_evidence", "ANSWERED"),
        ("search_confirmed_archive_evidence", "list_formal_archives", "CATALOG"),
    ],
)
def test_last_successful_tool_alone_controls_two_call_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    first_tool: str,
    second_tool: str,
    expected_kind: str,
) -> None:
    """两次顺序调用不得合并结果，只有最后一次成功调用决定最终投影。"""
    candidate = _candidate()
    monkeypatch.setattr(catalog_service, "list_agent_formal_archives", lambda **_: _catalog_page())
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: ArchiveRetrievalResponse(items=[candidate], requested_top_k=8, returned_count=1),
    )
    args_by_tool = {
        "list_formal_archives": {},
        "search_confirmed_archive_evidence": {"query": "证据"},
    }
    model = StubChatModel([
        _tool_call(first_tool, "call-1", args_by_tool[first_tool]),
        _tool_call(second_tool, "call-2", args_by_tool[second_tool]),
        AIMessage(content="完成"),
    ])
    judge = StubChatModel([
        AIMessage(
            content='{"decision":"ANSWERED","answer":"只依据最后一次证据。","citation_numbers":[1]}'
        )
    ])
    with _runtime(tmp_path, model, judge) as runtime:
        result = _invoke(runtime, "原始用户问题")

    assert result.turn.answer_kind == expected_kind
    assert result.state["tool_call_count"] == 2
    if expected_kind == "ANSWERED":
        assert result.turn.answer == "只依据最后一次证据。"
        assert len(judge.invocations) == 1
        assert "原始用户问题" in judge.invocations[0][0]
    else:
        assert result.turn.answer.startswith("第 1 页")
        assert result.turn.citations == ()
        assert judge.invocations == []


def test_empty_evidence_and_empty_catalog_short_circuit_without_judge(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两类空结果都必须使用固定文案，空证据不得调用判定模型。"""
    monkeypatch.setattr(catalog_service, "list_agent_formal_archives", lambda **_: _catalog_page(empty=True))
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: ArchiveRetrievalResponse(items=[], requested_top_k=8, returned_count=0),
    )
    evidence_model = StubChatModel([
        _tool_call("search_confirmed_archive_evidence", "empty-evidence", {"query": "无据"}),
        AIMessage(content="完成"),
    ])
    judge = StubChatModel([])
    with _runtime(tmp_path, evidence_model, judge) as runtime:
        evidence = _invoke(runtime)
    assert evidence.turn.answer == "正式档案中没有足够依据。"
    assert evidence.turn.answer_status == ArchiveAnswerStatus.REFUSED_NO_EVIDENCE
    assert judge.invocations == []

    catalog_model = StubChatModel([
        _tool_call("list_formal_archives", "empty-catalog", {}),
        AIMessage(content="完成"),
    ])
    with _runtime(tmp_path, catalog_model, StubChatModel([])) as runtime:
        catalog = _invoke(runtime)
    assert catalog.turn.answer == "当前项目没有可见的正式档案。"
    assert catalog.turn.answer_status == ArchiveAnswerStatus.ANSWERED


@pytest.mark.parametrize("kind", ["multiple", "unknown", "third"])
def test_model_tool_call_budget_rejects_invalid_output_before_forbidden_execution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """多调用、未知工具和第三次调用都必须在对应工具执行前停止。"""
    catalog_calls: list[dict[str, Any]] = []
    evidence_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        catalog_service,
        "list_agent_formal_archives",
        lambda **kwargs: catalog_calls.append(kwargs) or _catalog_page(),
    )
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **kwargs: evidence_calls.append(kwargs)
        or ArchiveRetrievalResponse(items=[_candidate()], requested_top_k=8, returned_count=1),
    )
    if kind == "multiple":
        response = AIMessage(
            content="",
            tool_calls=[
                {"name": "list_formal_archives", "args": {}, "id": "a", "type": "tool_call"},
                {"name": "search_confirmed_archive_evidence", "args": {"query": "x"}, "id": "b", "type": "tool_call"},
            ],
        )
        responses = [response]
    elif kind == "unknown":
        responses = [_tool_call("delete_archive", "unknown", {})]
    else:
        responses = [
            _tool_call("list_formal_archives", "first", {}),
            _tool_call("search_confirmed_archive_evidence", "second", {"query": "证据"}),
            _tool_call("list_formal_archives", "third", {}),
        ]
    with _runtime(tmp_path, StubChatModel(responses), StubChatModel([])) as runtime:
        with pytest.raises(ArchiveAgentModelOutputError):
            _invoke(runtime)

    if kind in {"multiple", "unknown"}:
        assert catalog_calls == []
        assert evidence_calls == []
    else:
        assert len(catalog_calls) == 1
        assert len(evidence_calls) == 1


def test_invalid_arguments_consume_budget_and_allow_one_correction(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一次参数无效要留下安全失败事件，并只允许第二次工具调用修正。"""
    evidence_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **kwargs: evidence_calls.append(kwargs)
        or ArchiveRetrievalResponse(items=[_candidate()], requested_top_k=8, returned_count=1),
    )
    model = StubChatModel([
        _tool_call("list_formal_archives", "invalid", {"page_size": 21}),
        _tool_call("search_confirmed_archive_evidence", "corrected", {"query": "证据"}),
        AIMessage(content="完成"),
    ])
    judge = StubChatModel([
        AIMessage(content='{"decision":"ANSWERED","answer":"修正后回答。","citation_numbers":[1]}')
    ])
    with _runtime(tmp_path, model, judge) as runtime:
        result = _invoke(runtime)

    assert result.state["tool_call_count"] == 2
    assert [event.status for event in result.turn.tool_events] == ["FAILED", "COMPLETED"]
    assert result.turn.tool_events[0].error_code == "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID"
    assert len(evidence_calls) == 1


def test_model_and_tool_retry_only_transient_failures(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接/超时各只额外尝试一次，且内部重试不增加模型工具调用数。"""
    catalog_attempts = 0

    def transient_catalog(**_: Any) -> AgentCatalogPage:
        nonlocal catalog_attempts
        catalog_attempts += 1
        if catalog_attempts == 1:
            raise ConnectionError("temporary catalog failure")
        return _catalog_page()

    monkeypatch.setattr(catalog_service, "list_agent_formal_archives", transient_catalog)
    model = StubChatModel([
        TimeoutError("temporary model failure"),
        _tool_call("list_formal_archives", "retry-tool", {}),
        AIMessage(content="完成"),
    ])
    with _runtime(tmp_path, model, StubChatModel([])) as runtime:
        result = _invoke(runtime)

    assert catalog_attempts == 2
    assert len(model.invocations) == 3
    assert result.state["tool_call_count"] == 1
    assert len(result.turn.tool_events) == 1
    assert result.turn.tool_events[0].attempt_count == 2


def test_judge_model_retries_one_transient_failure_without_new_tool_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据判定模型连接失败只额外尝试一次且不重复检索。"""
    evidence_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **kwargs: evidence_calls.append(kwargs)
        or ArchiveRetrievalResponse(
            items=[_candidate()],
            requested_top_k=8,
            returned_count=1,
        ),
    )
    model = StubChatModel([
        _tool_call("search_confirmed_archive_evidence", "judge-retry", {"query": "证据"}),
        AIMessage(content="完成"),
    ])
    judge = StubChatModel([
        ConnectionError("temporary judge failure"),
        AIMessage(
            content='{"decision":"ANSWERED","answer":"重试后回答。","citation_numbers":[1]}'
        ),
    ])

    with _runtime(tmp_path, model, judge) as runtime:
        result = _invoke(runtime, "原始问题")

    assert result.turn.answer == "重试后回答。"
    assert result.state["tool_call_count"] == 1
    assert len(evidence_calls) == 1
    assert len(judge.invocations) == 2


def test_final_transient_failure_returns_neutral_dependency_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第二次仍失败时不得根据异常或模型文本生成回答。"""
    attempts = 0

    def unavailable(**_: Any) -> AgentCatalogPage:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("secret timeout detail")

    monkeypatch.setattr(catalog_service, "list_agent_formal_archives", unavailable)
    model = StubChatModel([_tool_call("list_formal_archives", "failed", {})])
    with _runtime(tmp_path, model, StubChatModel([])) as runtime:
        with pytest.raises(ArchiveAgentDependencyError) as exc_info:
            _invoke(runtime)
    assert attempts == 2
    assert len(model.invocations) == 1
    assert len(exc_info.value.tool_events) == 1
    event = exc_info.value.tool_events[0]
    assert event.status == "FAILED"
    assert event.error_code == "ARCHIVE_AGENT_TOOL_TIMEOUT"
    assert event.attempt_count == 2
    assert event.arguments_summary == {
        "filter_names": [],
        "page": 1,
        "page_size": 20,
    }
    assert event.result_summary is None


def test_model_failure_after_tool_preserves_completed_event(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具成功后的模型最终失败仍要交给调用方已有安全事件。"""
    monkeypatch.setattr(
        catalog_service,
        "list_agent_formal_archives",
        lambda **_: _catalog_page(),
    )
    model = StubChatModel([
        _tool_call("list_formal_archives", "completed-before-model-failure", {}),
        TimeoutError("temporary final decision failure"),
        TimeoutError("final decision failure"),
    ])

    with _runtime(tmp_path, model, StubChatModel([])) as runtime:
        with pytest.raises(ArchiveAgentDependencyError) as exc_info:
            _invoke(runtime)

    assert len(exc_info.value.tool_events) == 1
    assert exc_info.value.tool_events[0].status == "COMPLETED"


def test_complete_round_projection_discards_incomplete_and_internal_messages() -> None:
    """历史和下一轮模型输入只保留完整 Human/最终 AIMessage 对。"""
    messages = [
        HumanMessage(content="孤立问题"),
        ToolMessage(content="{}", tool_call_id="orphan"),
        HumanMessage(content="完整问题"),
        _tool_call("list_formal_archives", "internal", {}),
        ToolMessage(content='{"found":true}', tool_call_id="internal"),
        AIMessage(content="可信最终回答"),
        HumanMessage(content="尾部未完成问题"),
    ]

    projected = project_complete_archive_messages(messages)

    assert [message.content for message in projected] == ["完整问题", "可信最终回答"]
    assert all(isinstance(message, (HumanMessage, AIMessage)) for message in projected)
