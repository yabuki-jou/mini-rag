"""实现项目档案助手的受控工具循环与可信最终投影。"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agents.archive.history import project_complete_archive_messages
from app.agents.archive.prompts import ARCHIVE_AGENT_SYSTEM_PROMPT
from app.agents.archive.state import ArchiveAgentState
from app.agents.tools.archive_tools import (
    ArchiveEvidenceRegistry,
    ArchiveToolRuntime,
    build_archive_tools,
    serialize_tool_message,
)
from app.core.errors import AppError
from app.schemas import ArchiveAgentCitationRead
from app.schemas.archive_question import ArchiveAnswerStatus
from app.services.archive.catalog import AgentCatalogPage, render_agent_catalog_text
from app.services.archive.questions import (
    ArchiveAnswerJudgmentError,
    judge_archive_answer,
)


_REFUSAL_ANSWER = "正式档案中没有足够依据。"


@dataclass(frozen=True)
class ArchiveToolEvent:
    """保存一次真实工具调用的请求内脱敏观测。

    Attributes:
        tool_call_id: 当前模型工具调用的关联标识。
        tool_name: 实际执行的工具名称。
        status: 工具调用的完成状态。
        error_code: 失败时的稳定错误代码，成功时为 ``None``。
        attempt_count: 本次工具调用包含的实际尝试次数。
        duration_ms: 工具调用耗时，单位为毫秒。
        arguments_summary: 不含参数值的脱敏参数摘要。
        result_summary: 不含业务正文的脱敏结果摘要。
    """

    tool_call_id: str
    tool_name: str
    status: str
    error_code: str | None
    attempt_count: int
    duration_ms: float
    arguments_summary: dict[str, object] | None
    result_summary: dict[str, object] | None


@dataclass(frozen=True)
class ArchiveTurnResult:
    """保存 Graph 当前轮次的可信响应候选和脱敏工具事件。

    Attributes:
        answer_status: 对外回答状态，例如已回答或无依据拒答。
        answer: 面向客户端的最终回答文本。
        citations: 面向客户端的脱敏引用列表。
        answer_kind: 当前回答的业务类型，例如目录结果或拒答。
        tool_call_count: 当前轮次成功或失败的工具调用总次数。
        tool_events: 当前轮次的脱敏工具观测序列。
    """

    answer_status: ArchiveAnswerStatus
    answer: str
    citations: tuple[ArchiveAgentCitationRead, ...]
    answer_kind: str
    tool_call_count: int
    tool_events: tuple[ArchiveToolEvent, ...]


class ArchiveAgentExecutionError(Exception):
    """项目档案 Graph 中性失败的共同基类。"""

    def __init__(
        self,
        message: str,
        *,
        tool_events: Sequence[ArchiveToolEvent] = (),
    ) -> None:
        """保存对客户端安全的错误信息和本轮脱敏工具事件。

        Args:
            message: 供服务层记录或转换的内部错误摘要，不应包含异常正文。
            tool_events: 当前轮次已经产生的脱敏工具观测；默认表示尚未产生事件。
        """
        super().__init__(message)
        self.tool_events = tuple(tool_events)


class ArchiveAgentModelOutputError(ArchiveAgentExecutionError):
    """表示工具调用数量、名称或标识不符合冻结协议。"""


class ArchiveAgentDependencyError(ArchiveAgentExecutionError):
    """表示模型、工具或判定依赖最终失败。"""


def _is_retryable(error: BaseException) -> bool:
    """只识别异常链中的连接或超时失败。

    Args:
        error: 需要沿原因链检查的原始异常。

    Returns:
        异常链包含连接或超时异常时返回 ``True``。
    """
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _tool_error_code(error: BaseException) -> str:
    """将工具最终失败投影为冻结的安全错误码。

    Args:
        error: 工具调用最终抛出的异常。

    Returns:
        面向内部审计事件的稳定错误代码。
    """
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, TimeoutError):
            return "ARCHIVE_AGENT_TOOL_TIMEOUT"
        if isinstance(current, ConnectionError):
            return "ARCHIVE_AGENT_TOOL_CONNECTION_FAILED"
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return "ARCHIVE_AGENT_TOOL_FAILED"


def _invoke_model(model: Any, messages: list[BaseMessage]) -> AIMessage:
    """对模型连接或超时失败额外尝试一次。

    Args:
        model: 支持 ``invoke`` 的模型客户端。
        messages: 发送给模型的系统、历史和当前轮消息。

    Returns:
        模型返回的 ``AIMessage``。

    Raises:
        ArchiveAgentModelOutputError: 模型返回的对象不是 ``AIMessage``。
        ArchiveAgentDependencyError: 模型依赖在允许的重试后仍不可用。
    """
    for attempt in range(2):
        try:
            response = model.invoke(messages)
            if not isinstance(response, AIMessage):
                raise ArchiveAgentModelOutputError("模型没有返回 AIMessage。")
            return response
        except ArchiveAgentModelOutputError:
            raise
        except Exception as exc:
            if attempt == 0 and _is_retryable(exc):
                continue
            raise ArchiveAgentDependencyError("项目档案助手模型不可用。") from exc
    raise AssertionError("模型重试循环不应到达此处。")


def _safe_tool_error_message(tool_name: str, tool_call_id: str, error_code: str) -> BaseMessage:
    """构造不含异常正文的工具失败消息，供模型使用剩余额度修正。

    Args:
        tool_name: 发生失败的工具名称。
        tool_call_id: 当前模型工具调用的关联标识。
        error_code: 对模型公开的稳定工具错误代码。

    Returns:
        可安全追加到当前请求消息序列的工具消息。
    """
    return serialize_tool_message(
        tool_call_id,
        {"found": False, "error_code": error_code},
        tool_name=tool_name,
    )


def _argument_summary(tool_name: str, values: dict[str, Any]) -> dict[str, object]:
    """按冻结白名单生成不含参数值的工具参数摘要。

    Args:
        tool_name: 需要摘要的工具名称。
        values: 工具经过校验后的参数字典。

    Returns:
        只包含字段存在性、长度或分页信息的脱敏摘要。
    """
    if tool_name == "search_confirmed_archive_evidence":
        query = str(values.get("query", ""))
        return {"query_provided": bool(query), "query_length": len(query)}
    filter_names = [
        name
        for name in (
            "document_type",
            "project_stage",
            "document_date_from",
            "document_date_to",
            "authoring_organization",
        )
        if values.get(name) is not None
    ]
    return {
        "filter_names": filter_names,
        "page": int(values.get("page", 1)),
        "page_size": int(values.get("page_size", 20)),
    }


def _result_summary(payload: dict[str, object]) -> dict[str, object]:
    """生成不含目录值、文件名或摘录的工具结果摘要。

    Args:
        payload: 工具返回的安全投影结果。

    Returns:
        只包含是否有结果及结果数量的脱敏摘要。
    """
    results = payload.get("results")
    items = payload.get("items")
    if isinstance(results, list):
        result_count = len(results)
    elif isinstance(items, list):
        result_count = len(items)
    else:
        result_count = 0
    return {"found": result_count > 0, "result_count": result_count}


def _failed_tool_event(
    *,
    tool_call_id: str,
    tool_name: str,
    error: BaseException,
    attempt_count: int,
    started_at: float,
    values: dict[str, Any],
) -> ArchiveToolEvent:
    """构造不含异常正文与业务数据的工具失败事件。

    Args:
        tool_call_id: 失败模型工具调用的关联标识。
        tool_name: 失败的工具名称。
        error: 工具调用抛出的异常，用于推导稳定错误代码。
        attempt_count: 截止当前失败所使用的尝试次数。
        started_at: 本次工具调用开始时的性能计时值。
        values: 工具校验后的参数，用于构造脱敏摘要。

    Returns:
        可写入本轮结果的脱敏失败事件。
    """
    return ArchiveToolEvent(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        status="FAILED",
        error_code=_tool_error_code(error),
        attempt_count=attempt_count,
        duration_ms=(perf_counter() - started_at) * 1000,
        arguments_summary=_argument_summary(tool_name, values),
        result_summary=None,
    )


def _validate_context(state: ArchiveAgentState, runtime: ArchiveToolRuntime) -> None:
    """确认 Graph State 的三个范围与服务端闭包完全一致。

    Args:
        state: 当前 Graph State，包含服务端注入的范围字段。
        runtime: 保存可信用户、项目和知识库范围的工具运行时。

    Raises:
        ArchiveAgentDependencyError: State 范围缺失、格式无效或与运行时不一致。
    """
    expected = {
        "user_id": runtime.user_id,
        "project_id": runtime.project_id,
        "kb_id": runtime.kb_id,
    }
    for field_name, expected_value in expected.items():
        try:
            actual = UUID(str(state.get(field_name)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ArchiveAgentDependencyError("项目档案助手授权范围无效。") from exc
        if actual != expected_value:
            raise ArchiveAgentDependencyError("项目档案助手授权范围无效。")


def _current_question(messages: list[BaseMessage]) -> tuple[str, list[BaseMessage]]:
    """取得当前 HumanMessage，并投影此前完整轮次作为模型输入。

    Args:
        messages: 当前 Checkpoint 或 Graph State 中的消息序列。

    Returns:
        当前规范化问题，以及可安全交给模型的历史与当前问题。

    Raises:
        ArchiveAgentDependencyError: 缺少有效当前用户消息或问题长度超限。
    """
    current_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], HumanMessage)
        ),
        -1,
    )
    if current_index < 0 or not isinstance(messages[current_index].content, str):
        raise ArchiveAgentDependencyError("项目档案助手缺少当前用户消息。")
    question = messages[current_index].content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not question or len(question) > 2000:
        raise ArchiveAgentDependencyError("项目档案助手用户消息无效。")
    history = project_complete_archive_messages(messages[:current_index])
    return question, [*history, HumanMessage(content=question, id=messages[current_index].id)]


def _build_citations(
    candidates: Sequence[Any],
    citation_numbers: Sequence[int],
) -> tuple[ArchiveAgentCitationRead, ...]:
    """把公共判定编号映射为不含内部标识的当前引用。

    Args:
        candidates: 当前请求内服务端保留的证据候选序列。
        citation_numbers: 公共判定返回的从 1 开始的引用编号。

    Returns:
        与编号对应的脱敏客户端引用元组。
    """
    return tuple(
        ArchiveAgentCitationRead(
            filename=candidates[number - 1].filename,
            location_type=candidates[number - 1].location_type,
            location_start=candidates[number - 1].location_start,
            location_end=candidates[number - 1].location_end,
            excerpt=candidates[number - 1].excerpt,
        )
        for number in citation_numbers
    )


def build_archive_graph(
    model: Any,
    *,
    judge_model: Any,
    tool_runtime: ArchiveToolRuntime,
    result_sink: Callable[[ArchiveTurnResult], None],
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """构建只持久化当前 HumanMessage 与可信最终 AIMessage 的 Archive Graph。

    Args:
        model: 支持工具调用的主模型客户端。
        judge_model: 对检索候选进行最终证据判定的模型客户端。
        tool_runtime: 由服务端注入的用户、项目、知识库范围和 Session 工厂。
        result_sink: 接收当前轮可信结果的请求内回调，不写入 Graph State。
        checkpointer: 可选的 LangGraph Checkpoint 存储；为空时不持久化 Graph 状态。

    Returns:
        已编译的、只有一个受控轮次节点的 LangGraph。
    """
    tools = build_archive_tools(runtime=tool_runtime)
    tools_by_name: dict[str, BaseTool] = {tool.name: tool for tool in tools}
    model_with_tools = model.bind_tools(tools)

    def run_turn(state: ArchiveAgentState) -> dict[str, object]:
        """在单个 Graph 节点内执行最多两个顺序工具调用并最终化。

        Args:
            state: 当前轮次的 Graph State，包含消息和服务端范围字段。

        Returns:
            仅包含可持久化消息更新的 Graph State 增量。

        Raises:
            ArchiveAgentExecutionError: 模型输出、工具调用或证据判定不符合协议。
        """
        _validate_context(state, tool_runtime)
        all_messages = list(state.get("messages", []))
        question, visible_messages = _current_question(all_messages)
        # 工具调用过程只保留在本轮内，避免把未验证的 ToolMessage 或内部证据标识写入持久状态。
        ephemeral_messages: list[BaseMessage] = list(visible_messages)
        events: list[ArchiveToolEvent] = []
        successful_calls: list[tuple[str, str, dict[str, object]]] = []
        tool_call_count = 0
        seen_call_ids: set[str] = set()

        while True:
            try:
                decision = _invoke_model(
                    model_with_tools,
                    [SystemMessage(content=ARCHIVE_AGENT_SYSTEM_PROMPT), *ephemeral_messages],
                )
            except ArchiveAgentModelOutputError as exc:
                raise ArchiveAgentModelOutputError(
                    "模型输出不符合项目档案助手协议。",
                    tool_events=events,
                ) from exc
            except ArchiveAgentDependencyError as exc:
                raise ArchiveAgentDependencyError(
                    "项目档案助手模型不可用。",
                    tool_events=events,
                ) from exc
            if not decision.tool_calls:
                break
            if len(decision.tool_calls) != 1:
                raise ArchiveAgentModelOutputError(
                    "模型一次只能调用一个工具。",
                    tool_events=events,
                )
            call = decision.tool_calls[0]
            tool_name = str(call.get("name", "")).strip()
            tool_call_id = str(call.get("id", "")).strip()
            if tool_name not in tools_by_name or not tool_call_id or tool_call_id in seen_call_ids:
                raise ArchiveAgentModelOutputError(
                    "模型工具调用不符合协议。",
                    tool_events=events,
                )
            if tool_call_count >= 2:
                raise ArchiveAgentModelOutputError(
                    "模型超过单轮工具调用上限。",
                    tool_events=events,
                )
            seen_call_ids.add(tool_call_id)
            tool_call_count += 1
            tool = tools_by_name[tool_name]
            raw_args = call.get("args")
            # State 来自服务端，tool_call_id 来自当前工具调用消息；二者都不能被业务参数覆盖。
            input_values = {
                **(raw_args if isinstance(raw_args, dict) else {}),
                "state": dict(state),
                "tool_call_id": tool_call_id,
            }
            started_at = perf_counter()
            try:
                validated = tool.args_schema.model_validate(input_values)
            except ValidationError:
                event = ArchiveToolEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    status="FAILED",
                    error_code="ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID",
                    attempt_count=1,
                    duration_ms=(perf_counter() - started_at) * 1000,
                    arguments_summary=None,
                    result_summary=None,
                )
                events.append(event)
                ephemeral_messages.extend(
                    (decision, _safe_tool_error_message(tool_name, tool_call_id, event.error_code))
                )
                if tool_call_count >= 2:
                    raise ArchiveAgentDependencyError(
                        "项目档案助手工具参数无效。",
                        tool_events=events,
                    )
                continue

            # 先转换为普通 Python 值，再交给工具，避免 Pydantic 对象或注入对象进入外部调用。
            values = validated.model_dump(mode="python")
            attempt_count = 0
            payload: dict[str, object] | None = None
            while attempt_count < 2:
                attempt_count += 1
                try:
                    result = tool.func(**values)
                    if not isinstance(result, dict):
                        raise TypeError("tool result must be a dict")
                    payload = result
                    break
                except AppError as exc:
                    if exc.code == "ARCHIVE_AGENT_TOOL_ARGUMENT_INVALID":
                        event = ArchiveToolEvent(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            status="FAILED",
                            error_code=exc.code,
                            attempt_count=attempt_count,
                            duration_ms=(perf_counter() - started_at) * 1000,
                            arguments_summary=None,
                            result_summary=None,
                        )
                        events.append(event)
                        ephemeral_messages.extend(
                            (decision, _safe_tool_error_message(tool_name, tool_call_id, event.error_code))
                        )
                        if tool_call_count >= 2:
                            raise ArchiveAgentDependencyError(
                                "项目档案助手工具参数无效。",
                                tool_events=events,
                            ) from exc
                        break
                    if attempt_count == 1 and _is_retryable(exc):
                        continue
                    events.append(
                        _failed_tool_event(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            error=exc,
                            attempt_count=attempt_count,
                            started_at=started_at,
                            values=values,
                        )
                    )
                    raise ArchiveAgentDependencyError(
                        "项目档案助手工具不可用。",
                        tool_events=events,
                    ) from exc
                except Exception as exc:
                    if attempt_count == 1 and _is_retryable(exc):
                        continue
                    events.append(
                        _failed_tool_event(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            error=exc,
                            attempt_count=attempt_count,
                            started_at=started_at,
                            values=values,
                        )
                    )
                    raise ArchiveAgentDependencyError(
                        "项目档案助手工具不可用。",
                        tool_events=events,
                    ) from exc
            if payload is None:
                continue
            events.append(
                ArchiveToolEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    status="COMPLETED",
                    error_code=None,
                    attempt_count=attempt_count,
                    duration_ms=(perf_counter() - started_at) * 1000,
                    arguments_summary=_argument_summary(tool_name, values),
                    result_summary=_result_summary(payload),
                )
            )
            successful_calls.append((tool_name, tool_call_id, payload))
            # 只把安全投影交还给模型；原始候选留在请求级注册表，供服务端判定引用。
            ephemeral_messages.extend(
                (
                    decision,
                    serialize_tool_message(
                        tool_call_id,
                        payload,
                        tool_name=tool_name,
                    ),
                )
            )

        # 最终回答只依赖最后一次成功工具调用，防止目录结果或旧轮次证据污染当前答案。
        if not successful_calls:
            if events:
                raise ArchiveAgentDependencyError(
                    "项目档案助手工具调用未成功。",
                    tool_events=events,
                )
            answer_status = ArchiveAnswerStatus.REFUSED_NO_EVIDENCE
            answer = _REFUSAL_ANSWER
            citations: tuple[ArchiveAgentCitationRead, ...] = ()
            answer_kind = "REFUSED"
        else:
            tool_name, tool_call_id, payload = successful_calls[-1]
            if tool_name == "list_formal_archives":
                page = AgentCatalogPage(
                    page=int(payload["page"]),
                    page_size=int(payload["page_size"]),
                    total=int(payload["total"]),
                    items=list(payload["items"]),
                )
                answer_status = ArchiveAnswerStatus.ANSWERED
                answer = render_agent_catalog_text(page)
                citations = ()
                answer_kind = "CATALOG"
            else:
                candidates = list(tool_runtime.registry().get(tool_call_id) or ())
                if not candidates:
                    answer_status = ArchiveAnswerStatus.REFUSED_NO_EVIDENCE
                    answer = _REFUSAL_ANSWER
                    citations = ()
                    answer_kind = "REFUSED"
                else:
                    for judge_attempt in range(2):
                        try:
                            decision_result = judge_archive_answer(
                                question=question,
                                candidates=candidates,
                                model=judge_model,
                            )
                            break
                        except ArchiveAnswerJudgmentError as exc:
                            if judge_attempt == 0 and exc.retryable:
                                continue
                            raise ArchiveAgentDependencyError(
                                "项目档案助手证据判定不可用。",
                                tool_events=events,
                            ) from exc
                    else:
                        raise AssertionError("证据判定重试循环不应到达此处。")
                    answer_status = decision_result.answer_status
                    answer = decision_result.answer
                    citations = (
                        _build_citations(candidates, decision_result.citation_numbers)
                        if answer_status == ArchiveAnswerStatus.ANSWERED
                        else ()
                    )
                    answer_kind = (
                        "ANSWERED"
                        if answer_status == ArchiveAnswerStatus.ANSWERED
                        else "REFUSED"
                    )

        # result_sink 保存含引用的可信结果，但 Graph 返回值只包含可安全持久化的最终消息。
        turn_result = ArchiveTurnResult(
            answer_status=answer_status,
            answer=answer,
            citations=citations,
            answer_kind=answer_kind,
            tool_call_count=tool_call_count,
            tool_events=tuple(events),
        )
        result_sink(turn_result)
        return {
            "messages": [AIMessage(content=answer)],
            "tool_call_count": tool_call_count,
            "answer_kind": answer_kind,
        }

    builder = StateGraph(ArchiveAgentState)
    builder.add_node("archive_turn", run_turn)
    builder.add_edge(START, "archive_turn")
    builder.add_edge("archive_turn", END)
    return builder.compile(checkpointer=checkpointer)
