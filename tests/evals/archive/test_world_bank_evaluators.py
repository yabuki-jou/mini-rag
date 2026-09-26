"""验证世界银行答案评测器的冻结/扩展来源确定性口径。"""

import pytest
from pixie import Evaluable

from evals.archive.world_bank_evaluators import world_bank_answer_evaluator


def _evaluable(
    *,
    response: object,
    candidates: list[dict[str, object]],
    metadata: dict[str, object],
    execution: dict[str, object] | None = None,
) -> Evaluable:
    """构造不含真实资源标识的单题 Pixie 评测输入。"""
    return Evaluable(
        eval_input=[
            {
                "name": "archive_question_retrieval",
                "value": {"items": candidates},
            }
        ],
        eval_output=[
            *(
                [{"name": "archive_world_bank_execution", "value": execution}]
                if execution is not None
                else []
            ),
            {"name": "archive_question_response", "value": response},
        ],
        expectation={
            "answer_status": metadata.get("expected_answer_status"),
            "answer_fragments": metadata.get("expected_answer_fragments", []),
            "answer_alternatives": metadata.get("expected_answer_alternatives"),
            "frozen_target": metadata.get("frozen_target"),
            "extension_sources": metadata.get("extension_sources", []),
        },
        eval_metadata={"case_id": "SYNTHETIC-CASE", "source_group": "extension"},
    )


@pytest.mark.parametrize(
    "status",
    ["MODEL_FACTORY_FAILED", "MODEL_INVOKE_FAILED", "RESPONSE_PROCESSING_FAILED", "BATCH_HALTED"],
)
def test_execution_failure_is_explicit_zero_score_and_never_a_refusal(
    status: str,
) -> None:
    """执行故障优先判为零分失败，不得伪装成语义拒答或缺少响应。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=None,
            candidates=[],
            metadata=_metadata(
                expected_answer_status="REFUSED_NO_EVIDENCE",
                expected_answer_fragments=[],
                frozen_target=None,
                extension_sources=[],
            ),
            execution={
                "sample_index": 2,
                "model_role": "fr039_answer",
                "attempt_count": 1,
                "status": status,
                "exception": "DO_NOT_COPY",
                "question": "DO_NOT_COPY",
            },
        )
    )

    assert result.score == 0.0
    assert result.reasoning == "EXECUTION_FAILED"
    assert result.details == {
        "safe_error_code": "EXECUTION_FAILED",
        "execution_status": status,
        "sample_index": 2,
        "model_role": "fr039_answer",
        "attempt_count": 1,
    }
    assert "DO_NOT_COPY" not in str(result.details)


def test_completed_execution_observation_preserves_normal_answer_scoring() -> None:
    """成功执行观测不覆盖既有答案与引用质量评分。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(),
            candidates=[_candidate()],
            metadata=_metadata(),
            execution={
                "sample_index": 1,
                "model_role": "fr039_answer",
                "attempt_count": 1,
                "status": "COMPLETED",
            },
        )
    )

    assert result.score == 1.0


def _candidate(
    *, filename: str = "frozen-source.pdf", start: int = 16, excerpt: str = "approved amount 300"
) -> dict[str, object]:
    """生成最小定位候选，字段只含文件名、页码与测试摘录。"""
    return {
        "filename": filename,
        "location_type": "PDF_PAGE",
        "location_start": start,
        "location_end": start,
        "excerpt": excerpt,
    }


def _response(
    *,
    status: str = "ANSWERED",
    answer: str = "The approved amount is US$300 million.",
    citations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """生成与 FR-039 输出结构一致的确定性测试响应。"""
    return {
        "answer_status": status,
        "answer": answer,
        "citations": citations if citations is not None else [_candidate()],
    }


def _metadata(**overrides: object) -> dict[str, object]:
    """声明冻结页、扩展来源页和事实片段预期。"""
    value: dict[str, object] = {
        "expected_answer_status": "ANSWERED",
        "expected_answer_fragments": ["US$300 million"],
        "frozen_target": {"filename": "frozen-source.pdf", "page": 16},
        "extension_sources": [
            {"filename": "extension-source.pdf", "page": 4},
        ],
    }
    value.update(overrides)
    return value


def test_answered_response_checks_facts_candidate_mapping_and_separate_sources() -> None:
    """有据答案需满足预期片段，且引用来自候选并分别计量来源口径。"""
    candidates = [
        _candidate(),
        _candidate(filename="extension-source.pdf", start=4),
    ]
    citations = [
        _candidate(),
        _candidate(filename="extension-source.pdf", start=4),
    ]

    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(citations=citations),
            candidates=candidates,
            metadata=_metadata(),
        )
    )

    assert result.score == 1.0
    assert result.details["response_contract_passed"] is True
    assert result.details["citation_mapping_passed"] is True
    assert result.details["frozen_evidence"]["matched"] is True
    assert result.details["extension_source_diagnostic"]["matched_page_count"] == 1
    assert (
        result.details["extension_source_diagnostic"]["semantic_support"]
        == "NOT_AUTOMATICALLY_VERIFIED"
    )


def test_scoring_contract_comes_from_pixie_expectation_field() -> None:
    """数据构建器可把事实与来源预期放在 Pixie 原生 expectation 字段。"""
    evaluable = Evaluable(
        eval_input=[
            {"name": "archive_question_retrieval", "value": {"items": [_candidate()]}}
        ],
        eval_output=[
            {"name": "archive_question_response", "value": _response()},
        ],
        expectation={
            "answer_status": "ANSWERED",
            "answer_fragments": ["US$300 million"],
            "frozen_target": {"filename": "frozen-source.pdf", "page": 16},
            "extension_sources": [],
        },
        eval_metadata={"case_id": "SYNTHETIC-CASE", "source_group": "frozen"},
    )

    result = world_bank_answer_evaluator(evaluable)

    assert result.score == 1.0
    assert result.details["frozen_evidence"]["matched"] is True


def test_unmapped_citation_fails_with_static_safe_code() -> None:
    """引用定位不属于本题候选时失败，诊断不得回显摘录或标识。"""
    foreign_citation = _candidate(filename="not-a-candidate.pdf", start=99)
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(citations=[foreign_citation]),
            candidates=[_candidate()],
            metadata=_metadata(),
        )
    )

    assert result.score == 0.0
    assert result.details["citation_mapping_passed"] is False
    assert result.details["safe_error_code"] == "CITATION_NOT_IN_CANDIDATES"
    assert "approved amount 300" not in str(result.details)


def test_answered_response_without_citation_has_static_missing_code() -> None:
    """有据答案缺少引用时产生固定诊断码。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(citations=[]),
            candidates=[_candidate()],
            metadata=_metadata(),
        )
    )

    assert result.score == 0.0
    assert result.details["safe_error_code"] == "MISSING_CITATION"


def test_refusal_requires_fixed_text_and_no_citations() -> None:
    """无据拒答必须使用固定契约文案且引用为空。"""
    refused = _response(
        status="REFUSED_NO_EVIDENCE",
        answer="正式档案中没有足够依据。",
        citations=[],
    )
    metadata = _metadata(
        expected_answer_status="REFUSED_NO_EVIDENCE",
        expected_answer_fragments=[],
        frozen_target=None,
        extension_sources=[],
    )

    result = world_bank_answer_evaluator(
        _evaluable(response=refused, candidates=[], metadata=metadata)
    )

    assert result.score == 1.0
    assert result.details["refusal_contract_passed"] is True
    assert result.details["frozen_evidence"]["applicable"] is False


@pytest.mark.parametrize(
    "citation",
    [
        _candidate(),
        _candidate(filename="outside-candidate.pdf", start=99),
    ],
    ids=["mapped", "unmapped"],
)
def test_refusal_citations_are_not_applicable_to_mapping_and_contract_failure_wins(
    citation: dict[str, object],
) -> None:
    """拒答题的引用映射不适用，错误引用也不能遮蔽拒答契约失败。"""
    metadata = _metadata(
        expected_answer_status="REFUSED_NO_EVIDENCE",
        expected_answer_fragments=[],
        frozen_target=None,
        extension_sources=[],
    )
    response = _response(
        status="REFUSED_NO_EVIDENCE",
        answer="正式档案中没有足够依据。",
        citations=[citation],
    )

    result = world_bank_answer_evaluator(
        _evaluable(response=response, candidates=[_candidate()], metadata=metadata)
    )

    assert result.score == 0.0
    assert result.details["citation_mapping_passed"] is None
    assert result.details["safe_error_code"] == "ANSWER_CONTRACT_FAILED"
    assert result.reasoning == "ANSWER_CONTRACT_FAILED"


def test_answered_case_requires_at_least_one_expected_fact_fragment() -> None:
    """有据题缺少事实片段标注时不能被空条件判为通过。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(),
            candidates=[_candidate()],
            metadata=_metadata(expected_answer_fragments=[]),
        )
    )

    assert result.score == 0.0
    assert result.details["safe_error_code"] == "INVALID_EXPECTED_FACTS"


@pytest.mark.parametrize(
    "answer",
    ["US$250 million", "250 million US Dollars", "250百万美元", "2.5亿美元"],
)
def test_amount_alternative_group_accepts_equivalent_currency_expressions(
    answer: str,
) -> None:
    """金额契约允许同一金额的显式币种表达任选其一。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer=answer),
            candidates=[_candidate()],
            metadata=_metadata(
                expected_answer_fragments=[],
                expected_answer_alternatives=[
                    ["US$250 million", "250 million US Dollars", "250百万美元", "2.5亿美元"]
                ],
            ),
        )
    )

    assert result.score == 1.0
    assert result.details["answer_fragments_passed"] is True


@pytest.mark.parametrize("answer", ["US$300 million", "250"])
def test_amount_alternative_group_rejects_wrong_amount_or_amount_without_unit(
    answer: str,
) -> None:
    """金额预期不能被错误数值或没有币种单位的裸数字满足。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer=answer),
            candidates=[_candidate()],
            metadata=_metadata(
                expected_answer_fragments=[],
                expected_answer_alternatives=[
                    ["US$250 million", "250 million US Dollars", "250百万美元", "2.5亿美元"]
                ],
            ),
        )
    )

    assert result.score == 0.0
    assert result.details["safe_error_code"] == "ANSWER_CONTRACT_FAILED"


@pytest.mark.parametrize(
    "alternatives",
    [
        ["US$250 million"],
        [[]],
        [["US$250 million", "  "]],
        "US$250 million",
    ],
)
def test_malformed_answer_alternatives_use_static_expected_facts_error(
    alternatives: object,
) -> None:
    """空组或非字符串组等错误的金额契约只产生固定错误码。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(),
            candidates=[_candidate()],
            metadata=_metadata(expected_answer_alternatives=alternatives),
        )
    )

    assert result.score == 0.0
    assert result.details["safe_error_code"] == "INVALID_EXPECTED_FACTS"


def test_legacy_answer_fragments_remain_and_conditions() -> None:
    """旧样例的 answer_fragments 继续要求所有片段同时出现。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer="The approved amount is US$300 million."),
            candidates=[_candidate()],
            metadata=_metadata(expected_answer_fragments=["US$300 million", "approved"]),
        )
    )

    assert result.score == 1.0

    missing_one_fragment = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer="The approved amount is US$300 million."),
            candidates=[_candidate()],
            metadata=_metadata(expected_answer_fragments=["US$300 million", "disbursed"]),
        )
    )

    assert missing_one_fragment.score == 0.0
    assert missing_one_fragment.details["safe_error_code"] == "ANSWER_CONTRACT_FAILED"


def test_alternative_groups_are_and_conditions_and_empty_groups_are_optional() -> None:
    """等价表达组之间为 AND，空 alternatives 与旧契约兼容。"""
    both_groups = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer="Approved amount: 250 million US Dollars."),
            candidates=[_candidate()],
            metadata=_metadata(
                expected_answer_fragments=["Approved amount"],
                expected_answer_alternatives=[
                    ["US$250 million", "250 million US Dollars"],
                    ["USD", "US Dollars"],
                ],
            ),
        )
    )
    missing_group = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer="Approved amount: 250 million."),
            candidates=[_candidate()],
            metadata=_metadata(
                expected_answer_fragments=["Approved amount"],
                expected_answer_alternatives=[
                    ["US$250 million", "250 million US Dollars"],
                    ["USD", "US Dollars"],
                ],
            ),
        )
    )
    empty_alternatives = world_bank_answer_evaluator(
        _evaluable(
            response=_response(answer="Approved amount is US$300 million."),
            candidates=[_candidate()],
            metadata=_metadata(
                expected_answer_fragments=["US$300 million"],
                expected_answer_alternatives=[],
            ),
        )
    )

    assert both_groups.score == 1.0
    assert missing_group.score == 0.0
    assert missing_group.details["safe_error_code"] == "ANSWER_CONTRACT_FAILED"
    assert empty_alternatives.score == 1.0


def test_invalid_response_uses_static_safe_error_without_returning_output() -> None:
    """异常响应只产生固定错误码，不复制回答、异常或资源标识。"""
    secret_like_response = {
        "answer_status": "BROKEN",
        "answer": "MODEL_OUTPUT_MUST_NOT_BE_COPIED",
        "citations": "invalid",
        "internal_id": "11111111-2222-4333-8444-555555555555",
    }
    result = world_bank_answer_evaluator(
        _evaluable(
            response=secret_like_response,
            candidates=[],
            metadata=_metadata(),
        )
    )

    assert result.score == 0.0
    assert result.details["safe_error_code"] == "INVALID_RESPONSE_SHAPE"
    details = str(result.details)
    assert "MODEL_OUTPUT_MUST_NOT_BE_COPIED" not in details
    assert "11111111-2222-4333-8444-555555555555" not in details


def test_extension_match_does_not_replace_frozen_target_result() -> None:
    """扩展来源命中只作诊断，不能代替冻结目标证据命中。"""
    extension = _candidate(filename="extension-source.pdf", start=4)
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(citations=[extension]),
            candidates=[extension],
            metadata=_metadata(),
        )
    )

    assert result.details["frozen_evidence"]["matched"] is False
    assert result.details["extension_source_diagnostic"]["matched_page_count"] == 1
    assert result.score == 0.0


@pytest.mark.parametrize(
    "citation",
    [
        _candidate(filename="other-source.pdf", start=16),
        _candidate(filename="frozen-source.pdf", start=17),
    ],
    ids=["wrong-file", "page-outside-target-range"],
)
def test_nonmatching_frozen_file_or_page_range_is_rejected(
    citation: dict[str, object],
) -> None:
    """文件名不同或页范围未覆盖冻结目标时不能通过冻结评分。"""
    result = world_bank_answer_evaluator(
        _evaluable(
            response=_response(citations=[citation]),
            candidates=[citation],
            metadata=_metadata(),
        )
    )

    assert result.score == 0.0
    assert result.details["citation_mapping_passed"] is True
    assert result.details["frozen_evidence"]["matched"] is False


def test_missing_or_malformed_eval_input_uses_static_safe_error() -> None:
    """缺少唯一检索 wrap 输入时不得把引用判为来源有效。"""
    evaluable = Evaluable(
        eval_input=[{"name": "unexpected_input", "value": {"items": []}}],
        eval_output=[
            {"name": "archive_question_response", "value": _response()},
        ],
        eval_metadata=_metadata(),
    )

    result = world_bank_answer_evaluator(evaluable)

    assert result.score == 0.0
    assert result.details["safe_error_code"] == "MISSING_RETRIEVAL_INPUT"
