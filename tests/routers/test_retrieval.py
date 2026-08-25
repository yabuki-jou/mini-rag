"""验证 ``app.routers.retrieval`` 的 HTTP 契约转换。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.routers import retrieval as retrieval_router
from app.schemas import RetrievalTestRequest
from app.services.retrieval_service import RetrievedChunk


def test_retrieval_endpoint_converts_internal_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由必须把内部检索结果转换为既有响应契约。"""
    user_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    internal_result = RetrievedChunk(
        chunk_id="a" * 64,
        document_id=document_id,
        document_name="policy.pdf",
        page=1,
        content="制度正文",
        score=0.75,
    )
    monkeypatch.setattr(retrieval_router, "retrieve_chunks", lambda **_: [internal_result])

    response = retrieval_router.retrieval_test_endpoint(
        current_user=SimpleNamespace(id=user_id),
        knowledge_base=SimpleNamespace(id=kb_id),
        kb_id=kb_id,
        payload=RetrievalTestRequest(question="  制度问题  "),
    )

    assert response.question == "制度问题"
    assert len(response.results) == 1
    assert response.results[0].document_id == document_id
    assert response.results[0].score == 0.75
