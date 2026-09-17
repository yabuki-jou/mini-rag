"""投影项目档案助手可见的完整对话轮次。"""

from collections.abc import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def project_complete_archive_messages(
    messages: Iterable[BaseMessage],
) -> list[BaseMessage]:
    """只保留非空 HumanMessage 与随后可信最终 AIMessage 组成的完整轮次。"""
    projected: list[BaseMessage] = []
    pending_human: HumanMessage | None = None
    for message in messages:
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            content = message.content.strip()
            pending_human = (
                HumanMessage(content=content, id=message.id) if content else None
            )
            continue
        if (
            pending_human is not None
            and isinstance(message, AIMessage)
            and not message.tool_calls
            and isinstance(message.content, str)
            and message.content.strip()
        ):
            projected.extend(
                (
                    pending_human,
                    AIMessage(content=message.content.strip(), id=message.id),
                )
            )
            pending_human = None
    return projected
