"""验证企业知识库 Agent 工具的 Schema、注入上下文和安全错误。"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agents.tools import AgentContextError, search_company_policy
from app.agents.tools import policy_tools
from app.core.errors import AppError


def visible_properties(agent_tool) -> set[str]:
    """读取实际提供给模型的 Tool Call JSON Schema 字段。"""
    schema = agent_tool.tool_call_schema.model_json_schema()
    return set(schema.get("properties", {}))


def test_policy_tool_schema_hides_authorization_fields() -> None:
    """模型只能生成查询文本，不能生成用户或知识库身份。"""
    properties = visible_properties(search_company_policy)
    assert properties == {"query"}
    assert "user_id" not in properties
    assert "kb_id" not in properties


def test_policy_tool_forwards_injected_scope_and_serializes_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """制度工具应把注入身份原样传给现有检索服务。"""
    user_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    retrieve_mock = Mock(
        return_value=[
            SimpleNamespace(
                chunk_id="a" * 64,
                document_id=document_id,
                document_name="项目管理制度.pdf",
                page=3,
                content="项目资料需要按照制度归档。",
                score=0.82,
            )
        ]
    )
    monkeypatch.setattr(policy_tools, "retrieve_chunks", retrieve_mock)

    result = search_company_policy.func(
        query="  项目资料如何归档？  ",
        state={"user_id": str(user_id), "kb_id": str(kb_id)},
    )

    retrieve_mock.assert_called_once_with(
        user_id=user_id,
        kb_id=kb_id,
        question="项目资料如何归档？",
    )
    assert result["found"] is True
    assert result["message"] is None
    assert result["results"][0]["document_id"] == str(document_id)
    assert result["results"][0]["page"] == 3


def test_policy_tool_returns_explicit_no_evidence_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有合格 Chunk 时必须明确拒答并返回空结果。"""
    monkeypatch.setattr(policy_tools, "retrieve_chunks", Mock(return_value=[]))
    result = search_company_policy.func(
        query="不存在的制度",
        state={"user_id": str(uuid4()), "kb_id": str(uuid4())},
    )
    assert result == {
        "query": "不存在的制度",
        "found": False,
        "message": "知识库中没有足够依据。",
        "results": [],
    }


def test_policy_tool_preserves_safe_retrieval_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """检索服务已经转换的安全业务错误不应被工具改写。"""
    safe_error = AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。")
    monkeypatch.setattr(
        policy_tools,
        "retrieve_chunks",
        Mock(side_effect=safe_error),
    )
    with pytest.raises(AppError) as raised:
        search_company_policy.func(
            query="项目制度",
            state={"user_id": str(uuid4()), "kb_id": str(uuid4())},
        )
    assert raised.value is safe_error


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"user_id": "invalid", "kb_id": str(uuid4())},
        {"user_id": str(uuid4()), "kb_id": "invalid"},
    ],
)
def test_policy_tool_rejects_missing_or_invalid_context(state: dict[str, str]) -> None:
    """缺少或伪造服务端身份时不得执行查询。"""
    with pytest.raises(AgentContextError):
        search_company_policy.func(query="项目制度", state=state)
