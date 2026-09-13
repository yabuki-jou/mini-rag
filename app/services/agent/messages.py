"""提供 Agent Checkpoint 消息读取和来源转换能力。"""

import json
from json import JSONDecodeError
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.agents.admin.runtime import AdminAgentRuntime
from app.core.errors import AppError
from app.models import AgentSession, MessageRole
from app.schemas import AgentMessageRead, SourceRead


def get_checkpoint_snapshot(
    runtime: AdminAgentRuntime,
    thread_id: str,
) -> Any:
    """读取 Checkpoint，并把底层异常转换为安全错误代码。

    Args:
        runtime: 当前请求使用的 Agent Runtime。
        thread_id: AgentSession 中保存的稳定线程标识。

    Returns:
        Runtime 返回的 LangGraph StateSnapshot。

    Raises:
        AppError: Checkpoint 超时、连接失败或发生未知读取错误。
    """
    try:
        return runtime.get_state(thread_id=thread_id)
    except AppError:
        raise
    except TimeoutError as exc:
        raise AppError(
            503,
            "AGENT_CHECKPOINT_TIMEOUT",
            "Agent 会话状态读取超时。",
        ) from exc
    except ConnectionError as exc:
        raise AppError(
            503,
            "AGENT_CHECKPOINT_UNAVAILABLE",
            "Agent 会话状态暂时不可用。",
        ) from exc
    except Exception as exc:
        raise AppError(
            503,
            "AGENT_CHECKPOINT_FAILED",
            "Agent 会话状态读取失败。",
        ) from exc


def checkpoint_messages(runtime: AdminAgentRuntime, thread_id: str) -> list[BaseMessage]:
    """读取并验证一个 Agent 线程的消息列表。

    Args:
        runtime: 当前请求使用的 Agent Runtime。
        thread_id: AgentSession 中保存的稳定 Graph 线程标识。

    Returns:
        Checkpoint 中的 LangChain 消息列表；新线程返回空列表。

    Raises:
        AppError: Checkpoint 消息结构损坏时抛出。
    """
    snapshot = get_checkpoint_snapshot(runtime, thread_id)
    raw_messages = snapshot.values.get("messages", [])
    if not isinstance(raw_messages, list) or not all(
        isinstance(message, BaseMessage) for message in raw_messages
    ):
        raise AppError(500, "AGENT_CHECKPOINT_INVALID", "Agent 会话状态无效。")
    return raw_messages


def parse_tool_payload(message: ToolMessage) -> dict[str, Any] | None:
    """把 JSON ToolMessage 转换为字典，非 JSON 错误消息返回 None。

    Args:
        message: Graph 中的一条工具结果消息。

    Returns:
        JSON 对象工具结果；内容不是 JSON 对象时返回 None。
    """
    if not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
    except (JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def sources_from_tool_messages(messages: Iterable[BaseMessage]) -> list[SourceRead]:
    """提取制度检索工具返回的结构化引用。

    Args:
        messages: 当前回答之前需要检查的 Graph 消息。

    Returns:
        按检索顺序编号为 S1、S2 的来源列表。

    Raises:
        AppError: 制度工具返回了无法转换的来源字段时抛出。
    """
    sources: list[SourceRead] = []
    try:
        for message in messages:
            if not isinstance(message, ToolMessage) or message.name != "search_company_policy":
                continue
            payload = parse_tool_payload(message)
            if payload is None or payload.get("found") is not True:
                continue
            for result in payload.get("results", []):
                sources.append(
                    SourceRead(
                        source_id=f"S{len(sources) + 1}",
                        chunk_id=result["chunk_id"],
                        document_id=result["document_id"],
                        document_name=result["document_name"],
                        page=result["page"],
                        excerpt=result["content"],
                        score=result["score"],
                    )
                )
    except (KeyError, TypeError, ValidationError) as exc:
        raise AppError(500, "AGENT_SOURCE_DATA_INVALID", "Agent 引用数据无效。") from exc
    return sources


def last_answer(messages: Iterable[BaseMessage]) -> str:
    """读取当前执行中新产生的最后一条非空模型回答。

    Args:
        messages: 当前执行新增的 Graph 消息。

    Returns:
        去除两端空白后的助手自然语言回答。

    Raises:
        AppError: Graph 结束但没有产生有效文本回答时抛出。
    """
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            answer = message.content.strip()
            if answer:
                return answer
    raise AppError(502, "AGENT_RESPONSE_INVALID", "Agent 返回了无效回答。")


def read_agent_messages(
    agent_session: AgentSession,
    runtime: AdminAgentRuntime,
) -> list[AgentMessageRead]:
    """读取 Checkpoint 中用户可见的会话历史。

    Args:
        agent_session: 已通过当前用户所有权校验的会话。
        runtime: 用于读取对应线程 Checkpoint 的 Runtime。

    Returns:
        过滤 ToolMessage 和空 Tool Call AIMessage 后的消息列表。
    """
    messages = checkpoint_messages(runtime, agent_session.thread_id)
    result: list[AgentMessageRead] = []
    pending_sources: list[SourceRead] = []
    for message in messages:
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            content = message.content.strip()
            if content:
                result.append(AgentMessageRead(role=MessageRole.USER, content=content))
            pending_sources = []
        elif isinstance(message, ToolMessage):
            pending_sources.extend(sources_from_tool_messages([message]))
        elif isinstance(message, AIMessage) and isinstance(message.content, str):
            content = message.content.strip()
            if content:
                result.append(
                    AgentMessageRead(
                        role=MessageRole.ASSISTANT,
                        content=content,
                        sources=pending_sources,
                    )
                )
                pending_sources = []
    return result
