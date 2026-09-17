"""定义项目档案助手可写入 Checkpoint 的状态。"""

from typing import NotRequired

from langgraph.graph import MessagesState


class ArchiveAgentState(MessagesState):
    """保存可见消息、可信范围和当前轮次的最小控制状态。"""

    user_id: NotRequired[str]
    project_id: NotRequired[str]
    kb_id: NotRequired[str]
    tool_call_count: NotRequired[int]
    answer_kind: NotRequired[str]
