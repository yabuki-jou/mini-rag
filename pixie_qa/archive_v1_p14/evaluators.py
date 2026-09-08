"""D5 智慧档案问答评测器及 AV1-P02 固定集门槛辅助。"""

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pixie import Evaluation, Evaluable, create_agent_evaluator


_RESPONSE_NAME = "archive_question_response"
_DECISION_NAME = "archive_question_decision"
_SUFFICIENCY_NAME = "archive_evidence_sufficiency_decision"
_REQUIRED_CITATION_FIELDS = {
    "chunk_id",
    "document_id",
    "filename",
    "location_type",
    "location_start",
    "location_end",
    "excerpt",
    "score",
}
_P02_CATEGORIES = {"GROUNDED", "NO_EVIDENCE", "ISOLATION"}
_FIXED_REFUSAL_TEXT = "正式档案中没有足够依据。"


def _get_output(evaluable: Evaluable, name: str) -> Any:
    """按名称读取生产问答服务通过 ``eval_wrap`` 记录的输出。"""
    for item in evaluable.eval_output:
        if item.name == name:
            return item.value
    return None


def _normalize_text(value: object) -> str:
    """以保守规则统一全角字符、大小写和空白，不改变事实字符。"""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"\s+", "", normalized)


def _citation_matches_expected(
    citation: Mapping[str, object], expected_evidence: object
) -> bool:
    """判断引用是否覆盖标注文件、定位范围和全部原文摘录。"""
    if not isinstance(expected_evidence, Mapping):
        return False
    relative_path = expected_evidence.get("relative_path")
    expected_filename = str(relative_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not expected_filename or str(citation.get("filename")) != expected_filename:
        return False
    expected_items = expected_evidence.get("items")
    if not isinstance(expected_items, list) or not expected_items:
        return False
    for expected in expected_items:
        if not isinstance(expected, Mapping):
            return False
        try:
            location_type = citation["location_type"]
            location_type = getattr(location_type, "value", location_type)
            same_location = str(location_type) == str(expected["location_type"])
            contains_location = (
                int(citation["location_start"])
                <= int(expected["location_start"])
                <= int(expected["location_end"])
                <= int(citation["location_end"])
            )
            contains_excerpt = _normalize_text(expected["excerpt"]) in _normalize_text(
                citation["excerpt"]
            )
        except (KeyError, TypeError, ValueError):
            return False
        if not (same_location and contains_location and contains_excerpt):
            return False
    return True


def _p02_assessment(evaluable: Evaluable) -> dict[str, object]:
    """生成单题 AV1-P02 评测结果，供 evaluator 和聚合器共用。"""
    metadata = evaluable.eval_metadata or {}
    response = _get_output(evaluable, _RESPONSE_NAME)
    category = metadata.get("category")
    case_id = metadata.get("case_id") if isinstance(metadata.get("case_id"), str) else None
    candidate_complete = metadata.get("candidate_pool_complete") is True
    latency = metadata.get("retrieval_latency_ms")
    latency_valid = isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency >= 0
    answer_status = response.get("answer_status") if isinstance(response, Mapping) else None
    answer = response.get("answer") if isinstance(response, Mapping) else None
    citations = response.get("citations") if isinstance(response, Mapping) else None
    citations_valid = isinstance(citations, list)
    expected_answer = metadata.get("expected_answer")
    expected_evidence = metadata.get("expected_evidence")
    answer_match = (
        category == "GROUNDED"
        and isinstance(expected_answer, str)
        and bool(expected_answer.strip())
        and isinstance(answer, str)
        and _normalize_text(expected_answer) in _normalize_text(answer)
    )
    citation_match = (
        category == "GROUNDED"
        and citations_valid
        and any(
            isinstance(citation, Mapping)
            and _citation_matches_expected(citation, expected_evidence)
            for citation in citations
        )
    )
    refusal_shape = (
        category in {"NO_EVIDENCE", "ISOLATION"}
        and answer_status == "REFUSED_NO_EVIDENCE"
        and answer == _FIXED_REFUSAL_TEXT
        and citations_valid
        and not citations
    )
    category_valid = category in _P02_CATEGORIES
    entry_passed = candidate_complete and latency_valid and (
        (category == "GROUNDED" and answer_status == "ANSWERED" and answer_match and citation_match)
        or (category in {"NO_EVIDENCE", "ISOLATION"} and refusal_shape)
    )
    reasons: list[str] = []
    if not category_valid:
        reasons.append("category 无效")
    if not candidate_complete:
        reasons.append("candidate_pool_complete 不是 true")
    if not latency_valid:
        reasons.append("retrieval_latency_ms 不是非负数")
    if category == "GROUNDED":
        if answer_status != "ANSWERED":
            reasons.append(f"answer_status={answer_status!r}，期望 ANSWERED")
        if not answer_match:
            reasons.append("answer 未包含 expected_answer 的保守归一化文本")
        if not citation_match:
            reasons.append("没有 citation 匹配 expected_evidence")
    elif category in {"NO_EVIDENCE", "ISOLATION"} and not refusal_shape:
        reasons.append("无据/隔离题必须严格返回固定拒答文案且 citations 为空")
    if entry_passed:
        reasons.append("单题 AV1-P02 门槛通过")
    return {
        "case_id": case_id,
        "category": category,
        "candidate_pool_complete": candidate_complete,
        "retrieval_latency_ms": latency if latency_valid else None,
        "answer_status": answer_status,
        "answer_contains_expected": answer_match,
        "citation_matches_expected": citation_match,
        "entry_passed": entry_passed,
        "reasoning": "；".join(reasons),
    }


def archive_v1_p02_quality_gate(evaluable: Evaluable) -> Evaluation:
    """检查单个 AV1-P02 固定问题是否满足类别和候选完整性门槛。"""
    assessment = _p02_assessment(evaluable)
    return Evaluation(
        score=1.0 if assessment["entry_passed"] else 0.0,
        reasoning=str(assessment["reasoning"]),
        details={
            key: value
            for key, value in assessment.items()
            if key not in {"reasoning", "entry_passed"}
        },
    )


def aggregate_archive_v1_p02_results(
    evaluables: Sequence[Evaluable],
) -> dict[str, object]:
    """聚合 12 条 AV1-P02 评测载荷并计算固定质量门。"""
    assessments = [_p02_assessment(evaluable) for evaluable in evaluables]
    case_ids = [item["case_id"] for item in assessments if isinstance(item["case_id"], str)]
    unique_case_id_count = len(set(case_ids))
    category_counts = {
        category: sum(item["category"] == category for item in assessments)
        for category in sorted(_P02_CATEGORIES)
    }
    complete_count = sum(item["candidate_pool_complete"] is True for item in assessments)
    grounded_match_count = sum(
        item["category"] == "GROUNDED"
        and item["answer_contains_expected"] is True
        and item["citation_matches_expected"] is True
        for item in assessments
    )
    no_evidence_refusal_count = sum(
        item["category"] == "NO_EVIDENCE" and item["entry_passed"] is True
        for item in assessments
    )
    isolation_refusal_count = sum(
        item["category"] == "ISOLATION" and item["entry_passed"] is True
        for item in assessments
    )
    latencies = sorted(
        item["retrieval_latency_ms"]
        for item in assessments
        if isinstance(item["retrieval_latency_ms"], (int, float))
    )
    p95 = None
    if latencies:
        p95 = latencies[max(0, min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1))]
    end_to_end_latencies = sorted(
        value
        for evaluable in evaluables
        for value in (
            _get_output(evaluable, "archive_question_end_to_end_latency_ms"),
        )
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    )
    end_to_end_p95 = None
    if end_to_end_latencies:
        end_to_end_p95 = end_to_end_latencies[
            max(0, min(len(end_to_end_latencies) - 1, math.ceil(len(end_to_end_latencies) * 0.95) - 1))
        ]
    quality_gate_passed = (
        len(assessments) == 12
        and unique_case_id_count == 12
        and complete_count == 12
        and category_counts["GROUNDED"] == 8
        and category_counts["NO_EVIDENCE"] == 2
        and category_counts["ISOLATION"] == 2
        and grounded_match_count >= 7
        and no_evidence_refusal_count == 2
        and isolation_refusal_count == 2
    )
    return {
        "question_count": len(assessments),
        "unique_case_id_count": unique_case_id_count,
        "candidate_pool_complete_question_count": complete_count,
        "grounded_question_count": category_counts["GROUNDED"],
        "no_evidence_question_count": category_counts["NO_EVIDENCE"],
        "isolation_question_count": category_counts["ISOLATION"],
        "grounded_answer_and_citation_count": grounded_match_count,
        "no_evidence_refusal_count": no_evidence_refusal_count,
        "isolation_refusal_count": isolation_refusal_count,
        "retrieval_latency_p95_ms": p95,
        "question_end_to_end_latency_p95_ms": end_to_end_p95,
        "quality_gate_passed": quality_gate_passed,
        "entries": [
            {
                key: value
                for key, value in item.items()
                if key != "reasoning"
            }
            for item in assessments
        ],
    }


def archive_answer_contract(evaluable: Evaluable) -> Evaluation:
    """检查最终充分性决策、回答状态和引用数组之间的确定性契约。"""
    response = _get_output(evaluable, _RESPONSE_NAME)
    decision = _get_output(evaluable, _DECISION_NAME)
    sufficiency = _get_output(evaluable, _SUFFICIENCY_NAME)
    problems: list[str] = []

    if not isinstance(response, dict):
        problems.append("缺少 archive_question_response 对象")
        response = {}
    status = response.get("answer_status")
    if status not in {"ANSWERED", "REFUSED_NO_EVIDENCE"}:
        problems.append("answer_status 不是受支持的枚举值")
    if not isinstance(response.get("answer"), str) or not response["answer"].strip():
        problems.append("answer 必须是非空字符串")
    citations = response.get("citations")
    if not isinstance(citations, list):
        problems.append("citations 必须是数组")
        citations = []
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            problems.append(f"citations[{index}] 不是对象")
            continue
        missing = sorted(_REQUIRED_CITATION_FIELDS - citation.keys())
        if missing:
            problems.append(f"citations[{index}] 缺少字段: {','.join(missing)}")

    if not isinstance(decision, dict):
        problems.append("缺少 archive_question_decision 对象")
        decision = {}
    has_evidence = decision.get("has_evidence")
    count = decision.get("retrieved_item_count")
    if not isinstance(has_evidence, bool):
        problems.append("decision.has_evidence 必须是布尔值")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        problems.append("decision.retrieved_item_count 必须是非负整数")
    if isinstance(has_evidence, bool) and isinstance(count, int) and not isinstance(count, bool):
        if has_evidence != (count > 0):
            problems.append("decision.has_evidence 与候选数量不一致")

    if not isinstance(sufficiency, dict):
        problems.append("缺少 archive_evidence_sufficiency_decision 对象")
        sufficiency = {}
    sufficient = sufficiency.get("sufficient")
    selected_numbers = sufficiency.get("selected_citation_numbers")
    sufficiency_count = sufficiency.get("retrieved_item_count")
    if not isinstance(sufficient, bool):
        problems.append("sufficiency.sufficient 必须是布尔值")
    if not isinstance(selected_numbers, list):
        problems.append("sufficiency.selected_citation_numbers 必须是数组")
        selected_numbers = []
    elif any(type(number) is not int or number < 1 for number in selected_numbers):
        problems.append("selected_citation_numbers 必须是唯一的正整数")
    elif len(set(selected_numbers)) != len(selected_numbers):
        problems.append("selected_citation_numbers 不能重复")
    if isinstance(sufficiency_count, int) and not isinstance(sufficiency_count, bool):
        if isinstance(count, int) and not isinstance(count, bool) and sufficiency_count != count:
            problems.append("sufficiency.retrieved_item_count 与候选数量不一致")
        if any(number > sufficiency_count for number in selected_numbers):
            problems.append("selected_citation_numbers 超出候选范围")
    elif sufficiency_count is not None:
        problems.append("sufficiency.retrieved_item_count 必须是非负整数")

    if status == "ANSWERED":
        if sufficient is not True:
            problems.append("ANSWERED 必须对应 sufficient=true")
        if not citations:
            problems.append("ANSWERED 必须带至少一条引用")
        if len(citations) != len(selected_numbers):
            problems.append("ANSWERED 的 citations 数量必须等于 selected_citation_numbers 数量")
    elif status == "REFUSED_NO_EVIDENCE":
        if sufficient is not False:
            problems.append("REFUSED_NO_EVIDENCE 必须对应 sufficient=false")
        if selected_numbers:
            problems.append("REFUSED_NO_EVIDENCE 的 selected_citation_numbers 必须为空")
        if citations:
            problems.append("REFUSED_NO_EVIDENCE 的引用必须为空")

    return Evaluation(
        score=1.0 if not problems else 0.0,
        reasoning="契约检查通过。" if not problems else "；".join(problems),
        details={"problem_count": len(problems)},
    )


archive_evidence_faithfulness = create_agent_evaluator(
    name="ArchiveD5EvidenceFaithfulness",
    criteria=(
        "检查 archive_question_response 与 archive_question_prompt_evidence。"
        "回答中的每个业务断言都必须能在本轮候选摘录中找到直接支持，引用编号应对应实际摘录，"
        "不得把相近主题、常识或模型补全当作证据；引用对象还应与最终 response 的 citations 一致。"
    ),
)


archive_refusal_quality = create_agent_evaluator(
    name="ArchiveD5RefusalQuality",
    criteria=(
        "重点评审 eval_metadata 标为缺少直接证据的案例：即使候选分数很高，若摘录没有回答问题"
        "所需的关键事实，回答也必须明确说明正式档案证据不足，不得把相近事实改写成答案、编造金额/日期"
        "等细节或伪造引用。若问题有直接证据，则确认回答没有错误地拒答。"
    ),
)


__all__ = [
    "archive_answer_contract",
    "archive_evidence_faithfulness",
    "archive_refusal_quality",
    "archive_v1_p02_quality_gate",
    "aggregate_archive_v1_p02_results",
]
