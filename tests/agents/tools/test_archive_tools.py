"""验证 FR-042 P04 档案只读工具的参数和安全边界。"""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from app.agents.tools.archive_tools import (
    ArchiveEvidenceRegistry,
    ArchiveToolRuntime,
    build_archive_tools,
    archive_evidence_registry_scope,
    serialize_tool_message,
)
from app.core.errors import AppError
from app.schemas.archive_retrieval import ArchiveRetrievalItemRead, ArchiveRetrievalResponse
from app.services.archive import catalog as catalog_service
from app.services.archive import retrieval as retrieval_service
from app.services.archive.catalog import AgentCatalogPage
from app.models import EvidenceLocationType


@pytest.fixture(autouse=True)
def _default_confirmed_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    """普通工具单测默认让已伪造候选保持可见，标题为空。"""
    monkeypatch.setattr(
        catalog_service,
        "list_agent_confirmed_document_titles",
        lambda *, document_ids, **_: {
            document_id: None for document_id in document_ids
        },
    )


def _runtime(registry=None) -> ArchiveToolRuntime:
    """构造只包含服务端范围和短生命周期 Session 工厂的工具运行时。"""
    return ArchiveToolRuntime(
        user_id=uuid4(),
        project_id=uuid4(),
        kb_id=uuid4(),
        session_factory=lambda: nullcontext(SimpleNamespace()),
        evidence_registry=registry,
    )


def _candidate(index: int) -> ArchiveRetrievalItemRead:
    """构造带内部字段的检索候选，供安全投影测试使用。"""
    return ArchiveRetrievalItemRead(
        chunk_id=f"{index:064x}",
        document_id=uuid4(),
        filename=f"档案-{index}.txt",
        location_type=EvidenceLocationType.TEXT_LINE_RANGE,
        location_start=index,
        location_end=index,
        excerpt=f"原文-{index}",
        score=0.99 - index / 100,
        reranker_score=0.88 - index / 100,
    )


def test_archive_tools_expose_only_whitelisted_model_parameters() -> None:
    """工具 Schema 不得出现服务端范围、Top-K 或目录内部引用参数。"""
    catalog_tool, evidence_tool = build_archive_tools(runtime=_runtime())
    assert set(catalog_tool.tool_call_schema.model_json_schema()["properties"]) == {
        "document_type",
        "project_stage",
        "document_date_from",
        "document_date_to",
        "document_date_is_null",
        "authoring_organization",
        "page",
        "page_size",
    }
    assert set(evidence_tool.tool_call_schema.model_json_schema()["properties"]) == {"query"}


def test_archive_evidence_tool_injects_scope_normalizes_query_and_fixes_top8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据工具只向服务传入服务端范围和规范化问题，并固定取八条。"""
    registry = {}
    runtime = _runtime(registry)
    captured: dict = {}
    candidates = [_candidate(index) for index in range(1, 11)]

    def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return ArchiveRetrievalResponse(
            items=candidates[:8], requested_top_k=8, returned_count=8
        )

    def fake_titles(**kwargs):
        captured["title_project_id"] = kwargs["project_id"]
        captured["title_document_ids"] = kwargs["document_ids"]
        return {
            candidate.document_id: "设计说明" if index == 0 else None
            for index, candidate in enumerate(candidates[:8])
        }

    monkeypatch.setattr(retrieval_service, "retrieve_archive_answer_candidates", fake_retrieve)
    monkeypatch.setattr(catalog_service, "list_agent_confirmed_document_titles", fake_titles)
    _, evidence_tool = build_archive_tools(runtime=runtime)
    result = evidence_tool.func(
        query="  项目资料如何归档？\r\n",
        state={},
        tool_call_id="call-1",
    )
    assert captured["user_id"] == runtime.user_id
    assert captured["project_id"] == runtime.project_id
    assert captured["kb_id"] == runtime.kb_id
    assert captured["query"] == "项目资料如何归档？"
    assert captured["session"] is not None
    assert captured["title_project_id"] == runtime.project_id
    assert captured["title_document_ids"] == {
        candidate.document_id for candidate in candidates[:8]
    }
    assert result["results"][0]["citation"] == "S1"
    assert result["results"][0]["document_title"] == "设计说明"
    assert result["results"][1]["document_title"] is None
    assert len(result["results"]) == 8
    second = evidence_tool.func(
        query="第二次查询",
        state={},
        tool_call_id="call-2",
    )
    assert second["results"][0]["citation"] == "S1"
    serialized = json.dumps(result, ensure_ascii=False)
    for candidate in candidates[:8]:
        assert str(candidate.document_id) not in serialized
        assert candidate.chunk_id not in serialized
        assert str(candidate.score) not in serialized
        assert str(candidate.reranker_score) not in serialized


def test_archive_catalog_tool_calls_service_with_injected_project_and_safe_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录工具只把服务端项目范围传给服务，并按调用内顺序生成 A 引用。"""
    runtime = _runtime(ArchiveEvidenceRegistry())
    captured: dict = {}

    def fake_catalog(**kwargs):
        captured.update(kwargs)
        return AgentCatalogPage(
            page=2,
            page_size=1,
            total=3,
            items=[
                {
                    "document_ref": "A1",
                    "filename": "目录.txt",
                    "confirmed_at": "2026-09-01T00:00:00+00:00",
                    "fields": {
                        "TITLE": {"value": "标题", "source": "MANUAL", "has_source_evidence": True},
                        "DOCUMENT_TYPE": {"value": "OTHER", "source": None, "has_source_evidence": False},
                        "DOCUMENT_DATE": {"value": None, "source": None, "has_source_evidence": False},
                        "AUTHORING_ORGANIZATION": {"value": "单位", "source": "AI", "has_source_evidence": True},
                        "PROJECT_STAGE": {"value": "OTHER_STAGE", "source": "MANUAL", "has_source_evidence": True},
                    },
                }
            ],
        )

    monkeypatch.setattr(catalog_service, "list_agent_formal_archives", fake_catalog)
    catalog_tool, _ = build_archive_tools(runtime=runtime)
    result = catalog_tool.func(
        document_type=None,
        project_stage=None,
        document_date_from=None,
        document_date_to=None,
        document_date_is_null=False,
        authoring_organization=None,
        page=2,
        page_size=1,
        state={},
        tool_call_id="catalog-call",
    )
    assert captured["project_id"] == runtime.project_id
    assert captured["document_type"] is None
    assert result["items"][0]["document_ref"] == "A1"
    assert result["page"] == 2
    assert str(runtime.user_id) not in json.dumps(result)
    assert str(runtime.kb_id) not in json.dumps(result)


def test_archive_tools_render_tool_message_and_clear_request_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ToolMessage 只含安全投影，原始候选在请求成功后统一清理。"""
    with archive_evidence_registry_scope() as registry:
        runtime = _runtime(registry)
        _, evidence_tool = build_archive_tools(runtime=runtime)
        candidate = _candidate(1)
        monkeypatch.setattr(
            retrieval_service,
            "retrieve_archive_answer_candidates",
            lambda **_: ArchiveRetrievalResponse(
                items=[candidate], requested_top_k=8, returned_count=1
            ),
        )
        monkeypatch.setattr(
            catalog_service,
            "list_agent_confirmed_document_titles",
            lambda **_: {candidate.document_id: "人工确认标题"},
        )
        payload = evidence_tool.func(query="证据", state={}, tool_call_id="call-success")
        message = serialize_tool_message("call-success", payload)
        assert isinstance(message, ToolMessage)
        assert "document_id" not in message.content
        assert "chunk_id" not in message.content
        assert "score" not in message.content
        assert "S1" in message.content
        assert "人工确认标题" in message.content
        assert registry["call-success"][0].document_ref == "D1"
        assert registry["call-success"][0].document_title == "人工确认标题"
        graph_input = json.dumps({"messages": [message.model_dump(mode="json")]})
        assert str(candidate.document_id) not in graph_input
        assert candidate.chunk_id not in graph_input
        assert str(candidate.score) not in graph_input
        assert str(candidate.reranker_score) not in graph_input
    assert not registry


def test_archive_evidence_tool_clears_failed_call_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据服务异常时不得遗留本次 Tool Call 的原始候选。"""
    registry = {}
    runtime = _runtime(registry)
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: (_ for _ in ()).throw(AppError(503, "VECTOR_UNAVAILABLE", "不可用")),
    )
    _, evidence_tool = build_archive_tools(runtime=runtime)
    with pytest.raises(AppError):
        evidence_tool.func(query="证据", state={}, tool_call_id="call-error")
    assert registry == {}


def test_archive_evidence_failure_observes_only_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据工具失败观测只能记录异常类型，不能记录异常正文或参数。"""
    observed: list[tuple[str, str, object]] = []

    def fake_wrap(data, *, purpose, name, description):
        assert description
        observed.append((purpose, name, data))
        return data

    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: (_ for _ in ()).throw(ValueError("sensitive-detail")),
    )
    monkeypatch.setattr("app.agents.tools.archive_tools.eval_wrap", fake_wrap)
    _, evidence_tool = build_archive_tools(runtime=_runtime({}))

    with pytest.raises(ValueError, match="sensitive-detail"):
        evidence_tool.func(query="原始查询", state={}, tool_call_id="call-secret")

    assert observed[-1] == (
        "state",
        "archive_agent_tool_failure",
        {"tool_name": "search_confirmed_archive_evidence", "exception_type": "ValueError"},
    )
    assert "sensitive-detail" not in json.dumps(observed[-1], ensure_ascii=False)
    assert "原始查询" not in json.dumps(observed[-1], ensure_ascii=False)


def test_archive_catalog_tool_rejects_page_size_over_20_and_unknown_fields() -> None:
    """目录超限和证据额外参数必须得到可识别的参数错误。"""
    catalog_tool, evidence_tool = build_archive_tools(runtime=_runtime())
    with pytest.raises(AppError, match="页大小"):
        catalog_tool.func(
            document_type=None,
            project_stage=None,
            document_date_from=None,
            document_date_to=None,
            document_date_is_null=False,
            authoring_organization=None,
            page=1,
            page_size=21,
            state={},
            tool_call_id="invalid",
        )
    with pytest.raises(ValidationError):
        evidence_tool.args_schema.model_validate(
            {"query": "证据", "state": {}, "tool_call_id": "invalid", "top_k": 3}
        )


def test_archive_evidence_tool_wraps_external_retrieval_in_function_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测输入必须替换外部检索函数，不能先执行真实 Chroma 查询。"""
    candidate = _candidate(1)
    observed: list[tuple[str, str, object]] = []

    def fake_wrap(data, *, purpose, name, description):
        observed.append((purpose, name, data))
        assert description
        if purpose == "input":
            return lambda **_kwargs: [
                {
                    "document_ref": "D1",
                    "filename": candidate.filename,
                    "location_type": candidate.location_type.value,
                    "location_start": candidate.location_start,
                    "location_end": candidate.location_end,
                    "excerpt": candidate.excerpt,
                }
            ]
        return data

    monkeypatch.setattr(
        "app.agents.tools.archive_tools.eval_wrap",
        fake_wrap,
        raising=False,
    )
    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        lambda **_: (_ for _ in ()).throw(AssertionError("真实检索不应执行")),
    )
    _, evidence_tool = build_archive_tools(runtime=_runtime({}))

    result = evidence_tool.func(query="项目事实", state={}, tool_call_id="eval-call")

    assert result["found"] is True
    assert [(purpose, name) for purpose, name, _ in observed] == [
        ("input", "archive_agent_evidence_retrieval"),
        ("state", "archive_agent_safe_tool_result"),
    ]
    assert callable(observed[0][2])


def test_archive_catalog_tool_wraps_external_catalog_in_function_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录评测输入必须在数据库调用前替换，并继续执行安全投影。"""
    observed: list[tuple[str, str, object]] = []
    page = AgentCatalogPage(page=1, page_size=20, total=0, items=[])

    def fake_wrap(data, *, purpose, name, description):
        observed.append((purpose, name, data))
        assert description
        if purpose == "input":
            return lambda **_kwargs: page
        return data

    monkeypatch.setattr(
        "app.agents.tools.archive_tools.eval_wrap",
        fake_wrap,
        raising=False,
    )
    monkeypatch.setattr(
        catalog_service,
        "list_agent_formal_archives",
        lambda **_: (_ for _ in ()).throw(AssertionError("真实目录查询不应执行")),
    )
    catalog_tool, _ = build_archive_tools(runtime=_runtime())

    result = catalog_tool.func(
        document_type=None,
        project_stage=None,
        document_date_from=None,
        document_date_to=None,
        document_date_is_null=False,
        authoring_organization=None,
        page=1,
        page_size=20,
        state={},
        tool_call_id="catalog-eval-call",
    )

    assert result == page.as_dict()
    assert [(purpose, name) for purpose, name, _ in observed] == [
        ("input", "archive_agent_catalog_result"),
        ("state", "archive_agent_safe_tool_result"),
    ]
    assert callable(observed[0][2])


def test_archive_evidence_eval_input_excludes_persistent_ids_and_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据注入边界只能捕获请求内文档引用和原文定位。"""
    candidate = _candidate(1)
    captured_input: list[object] = []

    def fake_retrieve(**_kwargs):
        return ArchiveRetrievalResponse(
            items=[candidate], requested_top_k=8, returned_count=1
        )

    def fake_wrap(data, *, purpose, name, description):
        assert description
        if purpose == "input":
            value = data(
                user_id=uuid4(),
                project_id=uuid4(),
                kb_id=uuid4(),
                query="证据",
                session=SimpleNamespace(),
            )
            captured_input.append(value)
            return lambda **_kwargs: value
        return data

    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        fake_retrieve,
    )
    monkeypatch.setattr(
        "app.agents.tools.archive_tools.eval_wrap",
        fake_wrap,
    )
    _, evidence_tool = build_archive_tools(runtime=_runtime({}))

    evidence_tool.func(query="证据", state={}, tool_call_id="safe-eval")

    serialized = json.dumps(captured_input, ensure_ascii=False, default=str)
    assert "document_ref" in serialized
    assert "document_title" in serialized
    for forbidden in (
        "document_id",
        "chunk_id",
        "score",
        str(candidate.document_id),
        candidate.chunk_id,
    ):
        assert forbidden not in serialized


def test_archive_agent_disables_nested_raw_retrieval_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent 证据边界应关闭底层含持久化标识和分数的原始观测。"""
    received: list[dict[str, object]] = []

    def fake_retrieve(**kwargs):
        received.append(kwargs)
        return ArchiveRetrievalResponse(items=[], requested_top_k=8, returned_count=0)

    monkeypatch.setattr(
        retrieval_service,
        "retrieve_archive_answer_candidates",
        fake_retrieve,
    )
    _, evidence_tool = build_archive_tools(runtime=_runtime({}))

    evidence_tool.func(query="没有证据的问题", state={}, tool_call_id="safe-trace")

    assert received[0]["observe_result"] is False
