"""验证 FR-042 项目档案助手的确定性评测器。"""

from pixie import Evaluable


def _evidence(*, excerpt: str = "cycle 3") -> dict[str, object]:
    """构造不含持久化标识和分数的安全证据结果。"""
    return {
        "results": [
            {
                "citation": "S1",
                "filename": "PX-BETA-timeline.txt",
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 8,
                "location_end": 9,
                "excerpt": excerpt,
            }
        ],
        "found": True,
    }


def _successful_evaluable(*, citation_excerpt: str = "cycle 3") -> Evaluable:
    """构造两轮成功、历史与审计一致的评测载荷。"""
    return Evaluable(
        eval_input=[{"name": "input_data", "value": {"messages": ["责任单位？", "周期？"]}}],
        eval_output=[
            {"name": "archive_agent_safe_tool_result", "value": _evidence(excerpt="North Star")},
            {
                "name": "archive_agent_tool_calls",
                "value": [
                    {
                        "tool_name": "search_confirmed_archive_evidence",
                        "status": "COMPLETED",
                        "error_code": None,
                        "attempt_count": 1,
                        "arguments_summary": {"query_provided": True, "query_length": 5},
                        "result_summary": {"found": True, "result_count": 1},
                    }
                ],
            },
            {
                "name": "archive_agent_routing_decision",
                "value": {
                    "answer_kind": "ANSWERED",
                    "answer_status": "ANSWERED",
                    "tool_call_count": 1,
                    "tool_names": ["search_confirmed_archive_evidence"],
                },
            },
            {
                "name": "archive_agent_response",
                "value": {
                    "answer_status": "ANSWERED",
                    "answer": "North Star",
                    "citations": [
                        {
                            "filename": "PX-BETA-timeline.txt",
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 8,
                            "location_end": 9,
                            "excerpt": "North Star",
                        }
                    ],
                },
            },
            {"name": "archive_agent_safe_tool_result__2", "value": _evidence()},
            {
                "name": "archive_agent_tool_calls__2",
                "value": [
                    {
                        "tool_name": "search_confirmed_archive_evidence",
                        "status": "COMPLETED",
                        "error_code": None,
                        "attempt_count": 1,
                        "arguments_summary": {"query_provided": True, "query_length": 3},
                        "result_summary": {"found": True, "result_count": 1},
                    }
                ],
            },
            {
                "name": "archive_agent_routing_decision__2",
                "value": {
                    "answer_kind": "ANSWERED",
                    "answer_status": "ANSWERED",
                    "tool_call_count": 1,
                    "tool_names": ["search_confirmed_archive_evidence"],
                },
            },
            {
                "name": "archive_agent_response__2",
                "value": {
                    "answer_status": "ANSWERED",
                    "answer": "第 3 个周期",
                    "citations": [
                        {
                            "filename": "PX-BETA-timeline.txt",
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 8,
                            "location_end": 9,
                            "excerpt": citation_excerpt,
                        }
                    ],
                },
            },
            {
                "name": "archive_agent_conversation_state",
                "value": {
                    "requested_turn_count": 2,
                    "completed_http_statuses": [200, 200],
                    "history_message_count": 4,
                    "history_roles": ["USER", "ASSISTANT", "USER", "ASSISTANT"],
                    "audit_record_count": 2,
                },
            },
        ],
        eval_metadata={
            "expected_turn_count": 2,
            "expected_answer_statuses": ["ANSWERED", "ANSWERED"],
            "expected_answer_kinds": ["ANSWERED", "ANSWERED"],
            "expected_tool_paths": [
                ["search_confirmed_archive_evidence"],
                ["search_confirmed_archive_evidence"],
            ],
            "expected_answer_fragments": [["North Star"], ["3"]],
        },
    )


def test_archive_agent_structural_contract_accepts_consistent_two_turn_result() -> None:
    """工具、引用、历史和审计一致的两轮结果应通过机械契约。"""
    from pixie_qa.evaluators import archive_agent_structural_contract

    result = archive_agent_structural_contract(_successful_evaluable())

    assert result.score == 1.0, result.reasoning


def test_archive_agent_structural_contract_rejects_citation_not_in_current_evidence() -> None:
    """最终引用无法精确映射当前轮安全候选时必须失败。"""
    from pixie_qa.evaluators import archive_agent_structural_contract

    result = archive_agent_structural_contract(
        _successful_evaluable(citation_excerpt="fabricated excerpt")
    )

    assert result.score == 0.0
    assert "引用" in result.reasoning


def test_archive_agent_trace_privacy_rejects_internal_ids_scores_and_tokens() -> None:
    """任何 Wrap 输出出现内部标识、分数或 Token 都必须失败。"""
    from pixie_qa.evaluators import archive_agent_trace_privacy

    safe = archive_agent_trace_privacy(_successful_evaluable())
    leaked = _successful_evaluable()
    leaked.eval_output.append(
        {
            "name": "unsafe",
            "value": {
                "document_id": "11111111-1111-4111-8111-111111111111",
                "score": 0.99,
                "access_token": "secret",
            },
        }
    )

    assert safe.score == 1.0
    result = archive_agent_trace_privacy(leaked)
    assert result.score == 0.0
    assert "document_id" in result.reasoning
    assert "score" in result.reasoning
    assert "access_token" in result.reasoning


def test_archive_agent_failure_contract_accepts_frozen_error_and_safe_audit() -> None:
    """受控失败应按元数据核对 HTTP、错误码、审计和未完成历史。"""
    from pixie_qa.evaluators import archive_agent_failure_contract

    evaluable = Evaluable(
        eval_input=[{"name": "input_data", "value": {"messages": ["测试失败"]}}],
        eval_output=[
            {
                "name": "archive_agent_failure_state",
                "value": {
                    "http_status": 503,
                    "error_code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
                    "tool_audit": [
                        {
                            "tool_name": "search_confirmed_archive_evidence",
                            "status": "FAILED",
                            "error_code": "ARCHIVE_AGENT_TOOL_TIMEOUT",
                        }
                    ],
                    "history_message_count": 0,
                    "history_roles": [],
                },
            }
        ],
        eval_metadata={
            "expected_http_status": 503,
            "expected_error_code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "expected_history_message_count": 0,
            "expected_tool_audit": [
                {
                    "tool_name": "search_confirmed_archive_evidence",
                    "status": "FAILED",
                    "error_code": "ARCHIVE_AGENT_TOOL_TIMEOUT",
                }
            ],
        },
    )

    result = archive_agent_failure_contract(evaluable)

    assert result.score == 1.0, result.reasoning


def test_archive_agent_failure_contract_accepts_any_safe_failed_audit_when_unfrozen() -> None:
    """未冻结完整审计列表时，至少一条合法失败审计即可满足契约。"""
    from pixie_qa.evaluators import archive_agent_failure_contract

    evaluable = Evaluable(
        eval_input=[{"name": "input_data", "value": {"messages": ["测试失败"]}}],
        eval_output=[
            {
                "name": "archive_agent_failure_state",
                "value": {
                    "http_status": 503,
                    "error_code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
                    "tool_audit": [
                        {
                            "tool_name": "search_confirmed_archive_evidence",
                            "status": "FAILED",
                            "error_code": "ARCHIVE_AGENT_TOOL_RESULT_INVALID",
                        }
                    ],
                    "history_message_count": 0,
                    "history_roles": [],
                },
            }
        ],
        eval_metadata={
            "expected_http_status": 503,
            "expected_error_code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "expected_history_message_count": 0,
        },
    )

    result = archive_agent_failure_contract(evaluable)

    assert result.score == 1.0, result.reasoning


def test_evidence_faithfulness_uses_strict_per_turn_aggregation() -> None:
    """多轮任一核心证据失败时不得以平均边界分计为通过。"""
    from pixie_qa.evaluators import archive_agent_evidence_faithfulness

    criteria = archive_agent_evidence_faithfulness._criteria

    assert "必须逐轮分别评分" in criteria
    assert "最终分数采用各轮最低分" in criteria
    assert "任一轮核心事实失败时总分必须低于 0.5" in criteria
