"""企业行政 Agent 使用的公司制度检索工具。"""

from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import Field

from app.agents.tools.context import AgentContextError, require_state_uuid
from app.core.evaluation import eval_wrap
from app.services.rag.retrieval import retrieve_chunks


def _retrieve_policy_data(user_id: Any, kb_id: Any, query: str) -> list[dict[str, Any]]:
    """执行正式检索并转换为适合评测注入的纯数据结构。"""
    return [
        {
            "chunk_id": chunk.chunk_id,
            "document_id": str(chunk.document_id),
            "document_name": chunk.document_name,
            "page": chunk.page,
            "content": chunk.content,
            "score": chunk.score,
        }
        for chunk in retrieve_chunks(
            user_id=user_id,
            kb_id=kb_id,
            question=query,
        )
    ]


@tool
def search_company_policy(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2000,
            description="需要在公司制度知识库中检索的问题",
        ),
    ],
    state: Annotated[dict[str, Any], InjectedState],
) -> dict[str, Any]:
    """在当前用户已授权的知识库中检索公司制度原文。

    ``user_id`` 和 ``kb_id`` 只能由 Graph 状态注入，不能由模型生成，
    从而复用现有检索服务的用户隔离和知识库隔离规则。
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("制度检索问题不能为空。")

    user_id = require_state_uuid(state, "user_id")
    kb_id = require_state_uuid(state, "kb_id")

    results = eval_wrap(
        _retrieve_policy_data,
        purpose="input",
        name="policy_retrieval_result",
        description="当前授权知识库返回的制度检索 Chunk",
    )(user_id, kb_id, normalized_query)
    if not results:
        return {
            "query": normalized_query,
            "found": False,
            "message": "知识库中没有足够依据。",
            "results": [],
        }

    # 工具只返回检索到的原文和引用信息，最终自然语言回答仍由模型生成。
    return {
        "query": normalized_query,
        "found": True,
        "message": None,
        "results": results,
    }
