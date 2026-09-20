"""定义项目档案助手可写入 Checkpoint 的状态。"""

from typing import NotRequired

from langgraph.graph import MessagesState


class ArchiveAgentState(MessagesState):
    """保存可见消息、可信范围和当前轮次的最小控制状态。

    Attributes:
        user_id: 服务端注入并用于范围校验的用户 UUID 字符串。
        project_id: 服务端注入并用于范围校验的项目 UUID 字符串。
        kb_id: 服务端注入并用于范围校验的知识库 UUID 字符串。
        tool_call_count: 当前轮次已执行的工具调用次数。
        answer_kind: Graph 生成的回答类型标识。
    """

    user_id: NotRequired[str]
    project_id: NotRequired[str]
    kb_id: NotRequired[str]
    tool_call_count: NotRequired[int]
    answer_kind: NotRequired[str]
