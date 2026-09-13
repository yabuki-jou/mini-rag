"""验证旧 RAG 聊天提示构造能力迁入 rag 业务域后的稳定行为。"""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.models import ChatMessage, MessageRole
from app.services.rag.prompting import (
    build_knowledge_context,
    build_prompt_messages,
    build_sources,
)
from app.services.rag.retrieval import RetrievedChunk


def make_chunks() -> list[RetrievedChunk]:
    """创建顺序固定的检索结果，便于核对引用与上下文编号。"""
    return [
        RetrievedChunk(
            chunk_id="1" * 64,
            document_id=uuid4(),
            document_name="员工手册.pdf",
            page=3,
            content="报销上限为三千元。",
            score=0.91,
        ),
        RetrievedChunk(
            chunk_id="2" * 64,
            document_id=uuid4(),
            document_name="差旅制度.pdf",
            page=8,
            content="出差需提前审批。",
            score=0.82,
        ),
    ]


def test_sources_and_context_keep_retrieval_order() -> None:
    """引用和知识上下文应按同一检索顺序使用连续来源编号。"""
    chunks = make_chunks()

    sources = build_sources(chunks)
    context = build_knowledge_context(chunks)

    assert [source.source_id for source in sources] == ["S1", "S2"]
    assert [source.document_name for source in sources] == [
        "员工手册.pdf",
        "差旅制度.pdf",
    ]
    assert context.index("[S1]") < context.index("[S2]")
    assert context.index("报销上限为三千元") < context.index("出差需提前审批")


def test_prompt_messages_put_context_history_and_question_in_stable_order() -> None:
    """消息应依次包含规则、上下文、历史角色和当前问题。"""
    history = [
        ChatMessage(
            session_id=uuid4(),
            role=MessageRole.USER,
            content="之前的问题",
        ),
        ChatMessage(
            session_id=uuid4(),
            role=MessageRole.ASSISTANT,
            content="之前的回答",
        ),
    ]

    messages = build_prompt_messages("当前的问题", make_chunks(), history)

    assert isinstance(messages[0], SystemMessage)
    assert "只能根据“本次知识库上下文”回答" in messages[0].content
    assert isinstance(messages[1], SystemMessage)
    assert "[S1]" in messages[1].content
    assert isinstance(messages[2], HumanMessage)
    assert messages[2].content == "之前的问题"
    assert isinstance(messages[3], AIMessage)
    assert messages[3].content == "之前的回答"
    assert isinstance(messages[-1], HumanMessage)
    assert messages[-1].content == "当前的问题"


def test_old_rag_agent_module_is_not_used_as_prompting_boundary() -> None:
    """提示构造应只从新的服务模块提供，不保留旧 Agent 模块入口。"""
    from pathlib import Path

    old_module = Path(__file__).parents[3] / "app" / "agents" / "rag_agent.py"
    chat_module = Path(__file__).parents[3] / "app" / "services" / "rag" / "chat.py"

    assert not old_module.exists()
    retired_import = ".".join(("app", "agents", "rag_agent"))
    assert retired_import not in chat_module.read_text(encoding="utf-8")
