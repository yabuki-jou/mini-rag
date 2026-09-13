"""验证制度 Agent 评审器的确定性契约和语义评审器注册。"""

from types import SimpleNamespace

from evals.policy_agent.evaluators import (
    policy_agent_contract,
    policy_clarification_refusal_routing_safety,
    policy_evidence_faithfulness,
)


def _item(name: str, value: object) -> SimpleNamespace:
    """构造与 Pixie 观测项兼容的最小对象。"""
    return SimpleNamespace(name=name, value=value)


def _evaluable(
    *, response: dict, calls: list[dict], results: list[dict], metadata: dict | None = None
):
    """构造确定性契约评审所需的最小评测载荷。"""
    return SimpleNamespace(
        eval_metadata=metadata or {"expected_tools": ["search_company_policy"]},
        eval_input=[_item("policy_retrieval_result", results)],
        eval_output=[_item("agent_tool_calls", calls), _item("agent_response", response)],
    )


def test_policy_agent_contract_accepts_grounded_response() -> None:
    """合法工具、响应状态和本轮来源应通过契约检查。"""
    result = policy_agent_contract(
        _evaluable(
            calls=[{"name": "search_company_policy", "arguments": {"query": "报销"}}],
            response={
                "status": "COMPLETED",
                "answer": "按制度执行。",
                "request_id": "req-1",
                "sources": [{"document_name": "报销制度.pdf", "page": 2}],
            },
            results=[{"document_name": "报销制度.pdf", "page": 2, "content": "报销制度"}],
        )
    )

    assert result.score == 1.0


def test_policy_agent_contract_rejects_identity_and_untrusted_source() -> None:
    """工具参数不得携带身份，来源也不得超出本轮检索结果。"""
    result = policy_agent_contract(
        _evaluable(
            calls=[
                {
                    "name": "search_company_policy",
                    "arguments": {"query": "报销", "user_id": "forged"},
                }
            ],
            response={
                "status": "COMPLETED",
                "answer": "按制度执行。",
                "request_id": "req-1",
                "sources": [{"document_name": "伪造制度.pdf", "page": 99}],
            },
            results=[{"document_name": "报销制度.pdf", "page": 2, "content": "报销制度"}],
        )
    )

    assert result.score == 0.0


def test_policy_agent_contract_accepts_declared_tool_sequences() -> None:
    """允许评测条目声明无工具或制度工具两种安全路由。"""
    result = policy_agent_contract(
        _evaluable(
            calls=[],
            response={
                "status": "COMPLETED",
                "answer": "当前请求不在制度查询范围内。",
                "request_id": "req-2",
                "sources": [],
            },
            results=[],
            metadata={
                "expected_tools": ["unexpected_tool"],
                "allowed_tool_sequences": [[], ["search_company_policy"]],
            },
        )
    )

    assert result.score == 1.0
    assert "allowed_tool_sequences=[[], ['search_company_policy']]" in result.reasoning
    assert "tool_match_mode='allowed'" in result.reasoning


def test_policy_agent_semantic_evaluators_are_registered() -> None:
    """语义评审器应分别覆盖证据忠实性和路由安全。"""
    assert callable(policy_evidence_faithfulness)
    assert callable(policy_clarification_refusal_routing_safety)
