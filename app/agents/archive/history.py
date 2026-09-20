"""投影项目档案助手可见的完整对话轮次。"""

from collections.abc import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def project_complete_archive_messages(
    messages: Iterable[BaseMessage],
) -> list[BaseMessage]:
    """只保留非空 HumanMessage 与随后可信最终 AIMessage 组成的完整轮次。

    Args:
        messages: 从 Checkpoint 或当前 Graph State 读取的原始消息序列。

    Returns:
        可安全重新交给模型的完整对话轮次；工具消息、空消息和未完成轮次会被隐藏。
    """
    projected: list[BaseMessage] = []
    pending_human: HumanMessage | None = None
    # 只有成对的用户消息和无工具调用最终回答才可恢复，避免显示或复用半轮异常状态。
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
