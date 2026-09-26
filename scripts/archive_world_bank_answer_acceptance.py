"""使用真实 FastAPI 路由和 DeepSeek 执行有硬预算的答案/引用验收。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Any, Callable, Iterator, Mapping
from uuid import UUID, uuid4

_SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_PROJECT_ROOT))

from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable

from app.agents.tools import archive_tools
from app.core.config import PROJECT_ROOT
from app.main import app
from app.services.archive import questions as questions_module
from app.services.archive.evidence_matching import item_contains_expected_evidence
from app.services.archive import retrieval as retrieval_service
from app.services.infrastructure import ai_models as ai_models_module
from scripts.archive_lushan_persistence_acceptance import (
    _diagnostic_expected_evidence,
    _entry_map,
    _question,
    _read_json,
    _validate_question_answer,
)
from scripts.archive_v1_p14_acceptance import (
    AcceptanceError,
    P14Api,
    _require_object,
    _seed_confirmed_documents,
    parse_registered_user_id,
)
from scripts.archive_world_bank_retrieval_acceptance import (
    BELARUS_CORPUS_ROOT,
    CLEANUP_FAILURE_STEP_CODES,
    DATASET_PATH,
    _assert_empty_targets,
    _cleanup_and_verify,
    _make_combined_label,
    _register_tracked,
    verify_source_manifests,
)


REFUSAL_TEXT = "正式档案中没有足够依据。"
ATTEMPT_CAP = 45
WB_UNSUPPORTED_QUESTION = (
    "白俄罗斯 M6 交通走廊改善项目的世界银行贷款在项目完工时实际提款金额是多少？"
)
_LUSHAN_ANSWER_FRAGMENT = "US$300 million"


def build_sample_plan() -> list[dict[str, Any]]:
    """构造固定十样本，按端点和案例确定每请求最坏预算。"""
    plan: list[dict[str, Any]] = []
    for route in ("FR039", "FR042"):
        reservation = 1 if route == "FR039" else 8
        for sample_number in range(1, 4):
            plan.append(
                {
                    "case_id": "LUSHAN-01",
                    "route": route,
                    "sample": sample_number,
                    "reservation": reservation,
                    "require_tool_call": route == "FR042",
                }
            )
        for case_id in ("LUSHAN-04", "WB-UNSUPPORTED-DRAW-GATED-01"):
            plan.append(
                {
                    "case_id": case_id,
                    "route": route,
                    "sample": 1,
                    "reservation": reservation,
                    "require_tool_call": route == "FR042",
                }
            )
    return plan


class AttemptBudget:
    """为当前批次的真实模型请求尝试保留并执行硬上限。"""

    def __init__(self, *, cap: int = ATTEMPT_CAP) -> None:
        """初始化预算。

        Args:
            cap: 批次允许的模型 HTTP 尝试总数。
        """
        if cap <= 0:
            raise AcceptanceError("模型请求预算必须为正数。")
        self.cap = cap
        self.attempted = 0
        self.reserved = 0
        self._request_remaining = 0
        self._active_sample: dict[str, Any] | None = None
        self.attempt_events: list[dict[str, Any]] = []
        self._active_request_attempt = 0
        self._logical_call_sequence = 0
        self._retryable_calls: dict[str, tuple[str, int]] = {}

    @contextmanager
    def request(self, *, reservation: int, sample: Mapping[str, Any] | None = None) -> Iterator[None]:
        """原子预留单个 API 请求的最坏尝试数，并在结束时释放余量。

        Args:
            reservation: 当前请求允许占用的最大模型调用数。
        """
        if self._request_remaining:
            raise AcceptanceError("模型预算不允许嵌套请求预留。")
        if reservation <= 0 or self.attempted + self.reserved + reservation > self.cap:
            raise AcceptanceError("模型调用预算预留将超过批次硬上限。")
        self.reserved += reservation
        self._request_remaining = reservation
        self._active_sample = dict(sample or {})
        self._active_request_attempt = 0
        self._retryable_calls.clear()
        try:
            yield
        finally:
            unused = self._request_remaining
            self.reserved -= unused
            self._request_remaining = 0
            self._active_sample = None
            self._active_request_attempt = 0
            self._retryable_calls.clear()

    def consume(self, *, model_role: str) -> dict[str, Any]:
        """在进入真实模型 Runnable 前记账，超限时阻止调用。

        Args:
            model_role: 当前模型调用在验收流程中的职责。
        """
        if self._request_remaining <= 0 or self.attempted >= self.cap:
            raise AcceptanceError("真实模型调用超过本请求或批次预算，已在请求前阻断。")
        self._request_remaining -= 1
        self.reserved -= 1
        self.attempted += 1
        self._active_request_attempt += 1
        retry = self._retryable_calls.pop(model_role, None)
        if retry is None:
            self._logical_call_sequence += 1
            logical_call_id = f"logical-{self._logical_call_sequence:06d}"
            logical_attempt = 1
        else:
            logical_call_id, logical_attempt = retry
        event = dict(self._active_sample or {})
        event["model_role"] = model_role
        event["attempt"] = self.attempted
        event["request_attempt"] = self._active_request_attempt
        event["logical_call_id"] = logical_call_id
        event["logical_attempt"] = logical_attempt
        event["completion_status"] = "in_progress"
        self.attempt_events.append(event)
        return event

    def complete(self, event: dict[str, Any], *, status: str) -> None:
        """记录本次调用结果，并仅为可重试传输异常保留逻辑调用关联。

        Args:
            event: 本次调用对应的脱敏尝试事件。
            status: 本次调用的完成状态。
        """
        event["completion_status"] = status
        role = str(event["model_role"])
        if status == "retryable":
            self._retryable_calls[role] = (
                str(event["logical_call_id"]),
                int(event["logical_attempt"]) + 1,
            )
        else:
            self._retryable_calls.pop(role, None)


class BudgetedRunnable(Runnable[Any, Any]):
    """透明委托 LangChain Runnable，并在真实调用前消耗一次预算。"""

    def __init__(self, delegate: Any, budget: AttemptBudget, *, model_role: str) -> None:
        """保存实际模型 Runnable 和共享批次预算。

        Args:
            delegate: 由生产工厂创建的真实模型或其绑定 Runnable。
            budget: 当前运行共享的尝试预算。
            model_role: 此 Runnable 调用在 FR-039/FR-042 流程中的职责。
        """
        self._delegate = delegate
        self._budget = budget
        self.model_role = model_role

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """记账并透传一次同步模型调用。"""
        event = self._budget.consume(model_role=self.model_role)
        try:
            if config is None:
                result = self._delegate.invoke(input, **kwargs)
            else:
                result = self._delegate.invoke(input, config=config, **kwargs)
        except Exception as error:
            self._budget.complete(
                event,
                status="retryable" if _is_retryable_for_attempt(error, event) else "final",
            )
            raise
        self._budget.complete(event, status="success")
        return result

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """记账并透传一次异步模型调用。"""
        event = self._budget.consume(model_role=self.model_role)
        try:
            if config is None:
                result = await self._delegate.ainvoke(input, **kwargs)
            else:
                result = await self._delegate.ainvoke(input, config=config, **kwargs)
        except Exception as error:
            self._budget.complete(
                event,
                status="retryable" if _is_retryable_for_attempt(error, event) else "final",
            )
            raise
        self._budget.complete(event, status="success")
        return result

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """记账并透传一次流式模型调用。"""
        event = self._budget.consume(model_role=self.model_role)
        try:
            if config is None:
                chunks = self._delegate.stream(input, **kwargs)
            else:
                chunks = self._delegate.stream(input, config=config, **kwargs)
        except Exception as error:
            self._budget.complete(
                event,
                status="retryable" if _is_retryable_transport_error(error) else "final",
            )
            raise

        def tracked_chunks() -> Iterator[Any]:
            completed = False
            try:
                yield from chunks
            except Exception as error:
                self._budget.complete(
                    event,
                    status="retryable" if _is_retryable_for_attempt(error, event) else "final",
                )
                completed = True
                raise
            else:
                self._budget.complete(event, status="success")
                completed = True
            finally:
                if not completed:
                    self._budget.complete(event, status="final")

        return tracked_chunks()

    async def astream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """记账并透传一次异步流式模型调用。"""
        event = self._budget.consume(model_role=self.model_role)
        completed = False
        try:
            if config is None:
                async for chunk in self._delegate.astream(input, **kwargs):
                    yield chunk
            else:
                async for chunk in self._delegate.astream(input, config=config, **kwargs):
                    yield chunk
        except Exception as error:
            self._budget.complete(
                event,
                status="retryable" if _is_retryable_for_attempt(error, event) else "final",
            )
            completed = True
            raise
        else:
            self._budget.complete(event, status="success")
            completed = True
        finally:
            if not completed:
                self._budget.complete(event, status="final")

    def bind_tools(self, tools: Any, **kwargs: Any) -> BudgetedRunnable:
        """透明绑定工具，并让绑定后 Runnable 继续共享预算计数器。"""
        return BudgetedRunnable(
            self._delegate.bind_tools(tools, **kwargs),
            self._budget,
            model_role="fr042_graph_decision",
        )

    def bind(self, **kwargs: Any) -> BudgetedRunnable:
        """透明绑定模型参数，并让绑定后 Runnable 继续共享预算计数器。"""
        return BudgetedRunnable(
            self._delegate.bind(**kwargs), self._budget, model_role=self.model_role
        )

    def __getattr__(self, name: str) -> Any:
        """读取未包装的只读模型属性，保持 Runnable 元数据可见。"""
        return getattr(self._delegate, name)


def _is_retryable_for_attempt(error: BaseException, event: Mapping[str, Any]) -> bool:
    """按 FR-039/FR-042 生产重试规则判定本次模型失败是否可重试。

    Args:
        error: 当前 Runnable 调用抛出的异常。
        event: 当前调用的角色和逻辑尝试次数，不包含模型输入。
    """
    if int(event["logical_attempt"]) != 1:
        return False
    model_role = event["model_role"]
    if model_role == "fr042_evidence_judgment":
        return isinstance(error, (ConnectionError, TimeoutError))
    if model_role != "fr042_graph_decision":
        return False

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


@contextmanager
def real_model_factory_scope(
    *, budget: AttemptBudget, get_chat_model: Callable[..., Any] | None = None
) -> Iterator[None]:
    """仅在运行器作用域替换两个工厂，退出时无条件恢复原工厂。

    Args:
        budget: FR-039 与 FR-042 共用的批次预算。
        get_chat_model: 测试注入工厂；正式运行使用真实 DeepSeek 工厂。
    """
    factory = get_chat_model or ai_models_module.get_chat_model
    previous_questions = questions_module.get_chat_model
    previous_infrastructure = ai_models_module.get_chat_model

    def create_real_model(
        *, model_role: str, max_retries: int | None = None
    ) -> BudgetedRunnable:
        if max_retries not in (None, 0):
            raise AcceptanceError("真实模型工厂必须显式设置 max_retries=0。")
        model = factory(max_retries=0)
        if get_chat_model is None:
            client_retries = getattr(model, "max_retries", None)
            root_client_retries = getattr(getattr(model, "root_client", None), "max_retries", None)
            if client_retries != 0 or root_client_retries != 0:
                raise AcceptanceError("无法确认真实 DeepSeek 客户端已关闭隐藏重试。")
        return BudgetedRunnable(model, budget, model_role=model_role)

    def create_questions_model(*, max_retries: int | None = None) -> BudgetedRunnable:
        return create_real_model(max_retries=max_retries, model_role="fr039_answer")

    def create_runtime_model(*, max_retries: int | None = None) -> BudgetedRunnable:
        return create_real_model(
            max_retries=max_retries, model_role="fr042_evidence_judgment"
        )

    questions_module.get_chat_model = create_questions_model
    ai_models_module.get_chat_model = create_runtime_model
    try:
        yield
    finally:
        questions_module.get_chat_model = previous_questions
        ai_models_module.get_chat_model = previous_infrastructure


def _safe_item(item: Any) -> dict[str, Any]:
    """提取引用定位字段，不复制 Chunk ID、分数或摘录正文。"""
    return {
        "filename": str(item.filename),
        "location_type": str(getattr(item.location_type, "value", item.location_type)),
        "location_start": int(item.location_start),
        "location_end": int(item.location_end),
    }


class CandidateTrace:
    """在一次运行内留存引用核验所需候选，并只公开脱敏摘要。"""

    def __init__(self) -> None:
        """创建请求内候选映射和安全记录容器。"""
        self._items: dict[str, list[dict[str, Any]]] = {}
        self.safe_records: list[dict[str, Any]] = []

    def record(
        self,
        *,
        case_id: str,
        query: str,
        gate_question: str,
        items: list[Any],
        request_key: str | None = None,
    ) -> None:
        """保存候选原文仅供当前进程引用映射，并写入安全页码摘要。"""
        gate_triggered = retrieval_service._should_supplement_archive_candidates(gate_question)
        candidates = list(items)
        if len(candidates) > 8:
            raise AcceptanceError("FR-042 证据工具返回超过 Top-8 候选。")
        key = request_key or case_id
        self._items[key] = candidates
        self.safe_records.append(
            {
                "case_id": case_id,
                "request_key": key,
                "candidate_count": len(candidates),
                "gate_triggered": gate_triggered,
                "candidate_pages": [_safe_item(item) for item in candidates],
                "query_present": bool(query.strip()),
            }
        )

    def assess_citations(
        self,
        case_id: str,
        citations: list[dict[str, Any]],
        *,
        request_key: str | None = None,
        require_tool_call: bool = False,
        expected_gate_triggered: bool | None = None,
    ) -> dict[str, Any]:
        """确认每个公开引用映射到同一请求的 Top-8，并清点标准摘录支持。"""
        key = request_key or case_id
        tool_called = key in self._items
        record = next(
            (item for item in reversed(self.safe_records) if item.get("request_key") == key),
            None,
        )
        gate_triggered = bool(record and record.get("gate_triggered"))
        gate_matched = (
            expected_gate_triggered is None
            or gate_triggered is expected_gate_triggered
        )
        tool_call_matched = tool_called or not require_tool_call
        if case_id in {"LUSHAN-04", "WB-UNSUPPORTED-DRAW-GATED-01"}:
            return {
                "tool_called": tool_called,
                "gate_triggered": gate_triggered,
                "gate_matched": gate_matched,
                "tool_call_matched": tool_call_matched,
                "candidate_mapping_passed": None,
                "citation_count": len(citations),
            }
        candidates = self._items.get(key, [])
        mapped = bool(citations) and all(
            any(_citation_maps_to_candidate(citation, candidate) for candidate in candidates)
            for citation in citations
        )
        return {
            "tool_called": tool_called,
            "gate_triggered": gate_triggered,
            "gate_matched": gate_matched,
            "tool_call_matched": tool_call_matched,
            "candidate_mapping_passed": mapped,
            "citation_count": len(citations),
        }


def _citation_maps_to_candidate(citation: Mapping[str, Any], candidate: Any) -> bool:
    """按文件、位置和候选摘录覆盖关系验证公开引用映射。"""
    try:
        locator_matches = (
            citation.get("filename") == candidate.filename
            and citation.get("location_type")
            == str(getattr(candidate.location_type, "value", candidate.location_type))
            and int(citation.get("location_start")) <= int(candidate.location_start)
            and int(citation.get("location_end")) >= int(candidate.location_end)
        )
    except (TypeError, ValueError):
        return False
    expected = {
        "relative_path": str(candidate.filename),
        "items": [
            {
                "location_type": str(
                    getattr(candidate.location_type, "value", candidate.location_type)
                ),
                "location_start": int(candidate.location_start),
                "location_end": int(candidate.location_end),
                "excerpt": str(candidate.excerpt),
            }
        ],
    }
    return locator_matches and item_contains_expected_evidence(dict(citation), expected)


def assess_response(
    payload: Mapping[str, Any], *, expected_status: str
) -> dict[str, Any]:
    """检查公开状态、固定拒答文案及拒答空引用契约。"""
    answer = payload.get("answer")
    citations = payload.get("citations")
    status = getattr(payload.get("answer_status"), "value", payload.get("answer_status"))
    if not isinstance(answer, str) or not isinstance(citations, list):
        raise AcceptanceError("答案响应结构无效。")
    refusal = expected_status == "REFUSED_NO_EVIDENCE"
    answer_valid = answer == REFUSAL_TEXT if refusal else _LUSHAN_ANSWER_FRAGMENT in answer
    passed = status == expected_status and answer_valid and (not refusal or not citations)
    return {
        "status": str(status),
        "status_matched": status == expected_status,
        "answer_matched": answer_valid,
        "amount_marker_present": _LUSHAN_ANSWER_FRAGMENT in answer,
        "fixed_refusal_text": answer == REFUSAL_TEXT,
        "citation_count": len(citations),
        "empty_citations": not citations,
        "passed": passed,
    }


def finalize_sample_quality(assessment: Mapping[str, Any], *, case_id: str) -> dict[str, Any]:
    """分别应用有据冻结门或无据固定拒答门，避免无据样本被误判。"""
    result = dict(assessment)
    is_refusal_case = case_id != "LUSHAN-01"
    answer_gate = (
        result.get("fixed_refusal_text") is True
        and result.get("empty_citations") is True
        if is_refusal_case
        else result.get("amount_marker_present") is True
        and result.get("frozen_evidence_supported") is True
    )
    mapping_gate = (
        is_refusal_case
        or "candidate_mapping_passed" not in result
        or result.get("candidate_mapping_passed") is True
    )
    tool_call_gate = (
        "tool_call_matched" not in result
        or result.get("tool_call_matched") is True
    )
    gate_gate = "gate_matched" not in result or result.get("gate_matched") is True
    result["passed"] = bool(
        result.get("status_matched") is True
        and answer_gate
        and mapping_gate
        and tool_call_gate
        and gate_gate
    )
    return result


def _validate_retrieval_evidence(
    path: Path, verified_sources: Mapping[str, Any]
) -> dict[str, Any]:
    """要求 §5.2 真实检索结果与当前源清单、双路范围及候选支持结构吻合。"""
    report = _read_json(path, label="§5.2 真实检索摘要")
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise AcceptanceError("§5.2 摘要缺少案例结果，模型调用未启动。")
    cleanup = report.get("cleanup")
    required_cleanup = {
        "postgres_zero",
        "chroma_zero",
        "files_zero",
        "checkpoint_zero",
    }
    if (
        report.get("model_calls") != 0
        or not isinstance(cleanup, dict)
        or not required_cleanup.issubset(cleanup)
        or any(cleanup[key] is not True for key in required_cleanup)
    ):
        raise AcceptanceError("§5.2 模型调用或隔离清理核验不通过；模型调用未启动。")
    by_id = {item.get("case_id"): item for item in cases if isinstance(item, dict)}
    for case_id in ("LUSHAN-01", "WB-UNSUPPORTED-DRAW-GATED-01"):
        row = by_id.get(case_id)
        if not isinstance(row, dict) or row.get("production_top8", {}).get("matches_reconstructed") is not True:
            raise AcceptanceError("§5.2 生产 Top-8 与重建排序不符，模型调用未启动。")
        if row.get("production_rerank", {}).get("query_expression") != "IBRD IDA":
            raise AcceptanceError("§5.2 补充路证据缺失，模型调用未启动。")
    lushan = by_id["LUSHAN-01"].get("rankings", {}).get("supplementary", [])
    if not any(item.get("public") is True and item.get("rank", 99) <= 8 for item in lushan):
        raise AcceptanceError("LUSHAN-01 冻结证据未进入补充 Top-8，模型调用未启动。")
    belarus = by_id["WB-UNSUPPORTED-DRAW-GATED-01"].get("rankings", {}).get("supplementary", [])
    if not any(item.get("source_page") == 16 and item.get("rank", 99) <= 8 for item in belarus):
        raise AcceptanceError("Belarus 扩展来源第 16 页未进入补充 Top-8，模型调用未启动。")
    if report.get("source_manifest_checks", {}).get("belarus_source_sha256_verified") is not True:
        raise AcceptanceError("§5.2 Belarus 来源哈希未验证，模型调用未启动。")
    verified_source_keys = {
        "dataset",
        "lushan_manifest",
        "lushan_label",
        "belarus_manifest",
        "belarus_path",
    }
    if not verified_source_keys.issubset(verified_sources):
        raise AcceptanceError("当前来源清单校验结果结构不完整，模型调用未启动。")
    return by_id


def _case_question(case_id: str, entries: dict[str, dict[str, Any]]) -> str:
    """按固定评测条目返回原问题，扩展反向题使用已确认问题文本。"""
    if case_id == "WB-UNSUPPORTED-DRAW-GATED-01":
        return WB_UNSUPPORTED_QUESTION
    return _question(entries[case_id])


def _make_in_process_api() -> P14Api:
    """把现有 P14Api 请求契约接到进程内 FastAPI TestClient。"""
    api = P14Api.__new__(P14Api)
    api._client = TestClient(app)
    api._client.__enter__()
    api.headers = {}
    api.safe_code = "IN_PROCESS_HTTP_REQUEST"
    return api


def _run_one_sample(
    *,
    api: P14Api,
    budget: AttemptBudget,
    trace: CandidateTrace,
    row: Mapping[str, Any],
    question: str,
    entry: Mapping[str, Any] | None,
    project_id: str,
    agent_session_by_sample: dict[int, str],
) -> dict[str, Any]:
    """以原样路由请求执行一个样本，并返回不含证据原文的诊断。"""
    case_id = str(row["case_id"])
    route = str(row["route"])
    request_key = f"{route}-{case_id}-{int(row['sample'])}"
    expected_status = "ANSWERED" if case_id == "LUSHAN-01" else "REFUSED_NO_EVIDENCE"
    if route == "FR039":
        response = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/archive-questions",
                expected_statuses=(200,),
                payload={"question": question},
            ),
            step="FR-039 档案问答",
        )
    else:
        sample_number = int(row["sample"])
        session = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/agent-sessions",
                expected_statuses=(201,),
                payload={},
            ),
            step="FR-042 新会话",
        )
        session_id = str(session.get("id", ""))
        if not session_id:
            raise AcceptanceError("FR-042 未创建独立样本会话。")
        agent_session_by_sample[sample_number] = session_id
        original_retrieval = archive_tools.retrieval_service.retrieve_archive_answer_candidates

        def capture_retrieval(**kwargs: Any) -> Any:
            result = original_retrieval(**kwargs)
            trace.record(
                case_id=case_id,
                query=str(kwargs.get("query", "")),
                gate_question=str(kwargs.get("gate_question", "")),
                items=list(result.items),
                request_key=request_key,
            )
            return result

        archive_tools.retrieval_service.retrieve_archive_answer_candidates = capture_retrieval
        try:
            response = _require_object(
                api.request(
                    "POST",
                    f"/projects/{project_id}/agent-sessions/{session_id}/messages",
                    expected_statuses=(200,),
                    payload={"message": question},
                ),
                step="FR-042 项目档案助手",
            )
        finally:
            archive_tools.retrieval_service.retrieve_archive_answer_candidates = original_retrieval

    assessment = assess_response(response, expected_status=expected_status)
    citations = response.get("citations")
    if not isinstance(citations, list) or not all(isinstance(item, dict) for item in citations):
        raise AcceptanceError("回答引用结构无效。")
    if expected_status == "ANSWERED":
        if entry is None:
            raise AcceptanceError("冻结回答样本缺少标准证据标注。")
        expected = _diagnostic_expected_evidence(dict(entry))
        assessment["frozen_evidence_supported"] = any(
            item_contains_expected_evidence(citation, expected) for citation in citations
        )
    else:
        assessment["frozen_evidence_supported"] = False
    if route == "FR042":
        assessment.update(
            trace.assess_citations(
                case_id,
                citations,
                request_key=request_key,
                require_tool_call=bool(row["require_tool_call"]),
                expected_gate_triggered=case_id != "LUSHAN-04",
            )
        )
        if case_id == "LUSHAN-01":
            assessment["frozen_target_citation_supported"] = any(
                item_contains_expected_evidence(citation, _diagnostic_expected_evidence(dict(entry)))
                for citation in citations
            ) if entry is not None else False
    assessment = finalize_sample_quality(assessment, case_id=case_id)
    assessment.update(
        {
            "case_id": case_id,
            "route": route,
            "sample": int(row["sample"]),
            "citation_pages": [
                {
                    "filename": str(item.get("filename", "")),
                    "location_type": str(item.get("location_type", "")),
                    "location_start": item.get("location_start"),
                    "location_end": item.get("location_end"),
                    "excerpt_support": bool(
                        case_id == "LUSHAN-01"
                        and item_contains_expected_evidence(
                            item, _diagnostic_expected_evidence(dict(entry))
                        )
                    ) if entry is not None else False,
                }
                for item in citations
            ],
        }
    )
    return assessment


def _validate_result_file(path: Path) -> None:
    """只允许向忽略的验收目录写入新的安全摘要。"""
    target = path.resolve()
    allowed = (BELARUS_CORPUS_ROOT / "acceptance").resolve()
    if target.parent != allowed or not target.name.startswith("answer-acceptance-") or target.suffix != ".json" or target.exists():
        raise AcceptanceError("结果文件必须是验收忽略目录内未存在的新 JSON 文件。")


def _write_partial_result(path: Path, summary: Mapping[str, Any]) -> None:
    """持久化当前脱敏进度，使中断或后续失败不丢失已完成样本。"""
    path.write_text(
        json.dumps(dict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_cleanup_failure(summary: dict[str, Any], error: BaseException) -> None:
    """只把清理异常中允许的静态步骤码写入验收摘要。

    Args:
        summary: 当前待写入的脱敏验收摘要。
        error: 清理阶段捕获的异常对象。
    """
    raw_codes = getattr(error, "cleanup_failed_steps", None)
    if not isinstance(raw_codes, (tuple, list)) or not raw_codes:
        summary["cleanup_failed_steps"] = ["UNKNOWN"]
        return

    safe_codes: list[str] = []
    for raw_code in raw_codes:
        code = raw_code if isinstance(raw_code, str) else "UNKNOWN"
        safe_code = code if code in CLEANUP_FAILURE_STEP_CODES else "UNKNOWN"
        if safe_code not in safe_codes:
            safe_codes.append(safe_code)
    summary["cleanup_failed_steps"] = safe_codes or ["UNKNOWN"]


def run_answer_acceptance(*, retrieval_result: Path, result_file: Path) -> dict[str, Any]:
    """预检后经进程内真实路由执行固定十样本，并无条件清理隔离资源。"""
    _validate_result_file(result_file)
    _assert_empty_targets()
    sources = verify_source_manifests()
    _validate_retrieval_evidence(retrieval_result, sources)
    entries = _entry_map(sources["dataset"])
    plan = build_sample_plan()
    if len(plan) != 10 or sum(row["reservation"] for row in plan) != ATTEMPT_CAP:
        raise AcceptanceError("样本数或最坏预算与 §5.3 不符；模型调用未启动。")

    budget = AttemptBudget(cap=ATTEMPT_CAP)
    trace = CandidateTrace()
    api = _make_in_process_api()
    user_id: UUID | None = None
    registered_user: dict[str, UUID] = {}
    projects: dict[str, str] = {}
    seeded: list[tuple[str, str]] = []
    outcomes: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "evaluation": "world-bank-answer-and-citation",
        "sample_count": 0,
        "attempt_cap": ATTEMPT_CAP,
        "model_attempts": 0,
        "attempt_events": budget.attempt_events,
        "cases": outcomes,
        "agent_candidate_trace": trace.safe_records,
        "run_status": "running",
    }
    primary_error: BaseException | None = None
    try:
        user_id, _ = _register_tracked(
            api,
            run_tag=uuid4().hex[:12],
            on_registered=lambda value: registered_user.__setitem__("user_id", value),
        )
        _seed_confirmed_documents(
            api,
            document_label=_make_combined_label(sources),
            run_tag="world-bank-answer",
            project_ids=projects,
            seeded=seeded,
            evaluation_root=PROJECT_ROOT,
        )
        with real_model_factory_scope(budget=budget):
            for row in plan:
                try:
                    with budget.request(
                        reservation=int(row["reservation"]),
                        sample={
                            "case_id": str(row["case_id"]),
                            "route": str(row["route"]),
                            "sample": int(row["sample"]),
                        },
                    ):
                        outcome = _run_one_sample(
                            api=api,
                            budget=budget,
                            trace=trace,
                            row=row,
                            question=_case_question(str(row["case_id"]), entries),
                            entry=entries.get(str(row["case_id"])),
                            project_id=projects[
                                "lushan"
                                if str(row["case_id"]).startswith("LUSHAN-")
                                else "belarus"
                            ],
                            agent_session_by_sample={},
                        )
                except BaseException as case_error:
                    outcomes.append(
                        {
                            "case_id": str(row["case_id"]),
                            "route": str(row["route"]),
                            "sample": int(row["sample"]),
                            "outcome": "request_failed",
                            "error_type": type(case_error).__name__,
                            "safe_code": getattr(case_error, "safe_code", "RUNNER_ERROR"),
                            "http_status": getattr(case_error, "http_status", None),
                            "api_code": getattr(case_error, "api_code", None),
                            "attempts_so_far": budget.attempted,
                        }
                    )
                    summary["sample_count"] = len(outcomes)
                    summary["model_attempts"] = budget.attempted
                    summary["run_status"] = "stopped_on_request_failure"
                    summary["failed_case"] = str(row["case_id"])
                    _write_partial_result(result_file, summary)
                    raise case_error
                outcomes.append(outcome)
                summary["sample_count"] = len(outcomes)
                summary["model_attempts"] = budget.attempted
                # 质量失败是结果数据，批次继续；只保存脱敏回答和引用定位。
                _write_partial_result(result_file, summary)
        if len(outcomes) != 10 or budget.attempted > ATTEMPT_CAP:
            raise AcceptanceError("模型批次样本数或调用数越界。")
        summary["run_status"] = "completed"
    except BaseException as exc:
        primary_error = exc
        summary["model_attempts"] = budget.attempted
        summary["run_status"] = "failed"
        summary["error_type"] = type(exc).__name__
        _write_partial_result(result_file, summary)
    finally:
        trace._items.clear()
        try:
            summary["cleanup"] = _cleanup_and_verify(
                api=api,
                user_id=user_id or registered_user.get("user_id"),
                seeded=seeded,
                project_ids=projects,
            )
            summary["cleanup_verified"] = True
        except BaseException as cleanup_error:
            summary["cleanup_verified"] = False
            summary["cleanup_error_type"] = type(cleanup_error).__name__
            _record_cleanup_failure(summary, cleanup_error)
            primary_error = primary_error or cleanup_error
        summary["model_attempts"] = budget.attempted
        if primary_error is not None:
            summary["run_status"] = "failed"
        _write_partial_result(result_file, summary)
    if primary_error is not None:
        if isinstance(primary_error, AcceptanceError):
            raise primary_error
        raise AcceptanceError("答案验收失败；已保存部分结果且不会自动重跑。") from primary_error
    return summary


def main() -> int:
    """提供答案验收命令行入口。"""
    parser = argparse.ArgumentParser(description="运行 §5.3 World Bank 十样本真实答案验收。")
    parser.add_argument("--retrieval-result", type=Path, required=True, help="§5.2 脱敏真实检索结果 JSON。")
    parser.add_argument(
        "--result-file",
        type=Path,
        default=BELARUS_CORPUS_ROOT / "acceptance" / f"answer-acceptance-{uuid4().hex}.json",
        help="新建安全结果摘要路径。",
    )
    args = parser.parse_args()
    try:
        summary = run_answer_acceptance(
            retrieval_result=args.retrieval_result,
            result_file=args.result_file,
        )
    except AcceptanceError as exc:
        print(json.dumps({"status": "failed", "code": exc.safe_code}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "completed", "model_attempts": summary["model_attempts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
