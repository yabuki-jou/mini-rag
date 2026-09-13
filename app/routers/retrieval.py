"""提供知识库范围内的向量检索测试接口。"""

from uuid import UUID

from fastapi import APIRouter

from app.dependencies import CurrentUserDep, OwnedKnowledgeBaseDep
from app.schemas import (
    RetrievalResultRead,
    RetrievalTestRequest,
    RetrievalTestResponse,
)
from app.services.rag.retrieval import retrieve_chunks


router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["retrieval"])


@router.post("/retrieval-test", response_model=RetrievalTestResponse)
def retrieval_test_endpoint(
    current_user: CurrentUserDep,
    knowledge_base: OwnedKnowledgeBaseDep,
    kb_id: UUID,
    payload: RetrievalTestRequest,
) -> RetrievalTestResponse:
    """在当前用户拥有的知识库中执行向量检索测试。

    Args:
        current_user: 已通过请求头身份校验的当前用户。
        knowledge_base: 已通过当前用户所有权校验的知识库。
        kb_id: 路径中的知识库 UUID。
        payload: 包含自然语言问题的请求体。

    Returns:
        清理后的问题和通过阈值过滤的 Top-N 检索结果。

    Raises:
        AppError: 由检索服务返回的 Embedding、Chroma 或结果转换错误。
    """
    # knowledge_base 依赖会先验证路径中的 kb_id 是否属于当前用户。

    # 路由只传入已验证的用户、知识库和问题，不自行执行检索或分数过滤。
    retrieved_chunks = retrieve_chunks(
        user_id=current_user.id,
        kb_id=kb_id,
        question=payload.question,
    )

    # 将服务层内部结果转换成受 Pydantic 校验的 HTTP 响应结构。
    retrieval_result_reads: list[RetrievalResultRead] = [
        RetrievalResultRead.model_validate(chunk)
        for chunk in retrieved_chunks
    ]

    # 响应只包含原问题和实际进入最终 Top-N 的合格 Chunk。
    return RetrievalTestResponse(
        question=payload.question,
        results=retrieval_result_reads,
    )
