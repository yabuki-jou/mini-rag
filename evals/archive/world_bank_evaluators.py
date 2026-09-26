"""为世界银行问答样例提供确定性响应与来源口径评分。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pixie import Evaluation, Evaluable


_RETRIEVAL_INPUT_NAME = "archive_question_retrieval"
_RESPONSE_OUTPUT_NAME = "archive_question_response"
_FIXED_REFUSAL = "正式档案中没有足够依据。"
_KNOWN_STATUSES = {"ANSWERED", "REFUSED_NO_EVIDENCE"}
_EXTENSION_MANUAL_STATUS = "NOT_AUTOMATICALLY_VERIFIED"
_EXECUTION_OUTPUT_NAME = "archive_world_bank_execution"
_EXECUTION_FAILURE_STATUSES = {
    "PRE_MODEL_FAILED",
    "MODEL_FACTORY_FAILED",
    "PRE_INVOKE_FAILED",
    "MODEL_INVOKE_FAILED",
    "RESPONSE_PROCESSING_FAILED",
    "BATCH_HALTED",
    "BUDGET_VIOLATION",
}


def _item_value(item: Any) -> tuple[str | None, Any]:
    """读取 Pixie 命名观测的名称和值。

    Args:
        item: Pixie 提供的 Wrap 输入或输出项。
    """
    if isinstance(item, Mapping):
        name = item.get("name")
        return (name if isinstance(name, str) else None), item.get("value")
    name = getattr(item, "name", None)
    return (name if isinstance(name, str) else None), getattr(item, "value", None)


def _named_values(items: Sequence[Any], name: str) -> list[Any]:
    """提取指定名称的观测值，并保留重复项供契约检查。

    Args:
        items: Pixie 提供的命名观测列表。
        name: 要读取的 Wrap 名称。
    """
    values: list[Any] = []
    for item in items:
        item_name, value = _item_value(item)
        if item_name == name:
            values.append(value)
    return values


def _normalize(value: object) -> str:
    """统一 Unicode 兼容字符、大小写和空白以便机械比较。

    Args:
        value: 需要按确定性规则比较的文本值。
    """
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"\s+", "", normalized)


def _field(value: Any, name: str) -> Any:
    """从映射或 Pydantic DTO 读取同名字段。

    Args:
        value: 输入映射或 DTO。
        name: 要读取的字段名。
    """
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _location_type(value: Any) -> str | None:
    """把位置枚举统一为可比较的静态字符串。

    Args:
        value: 含定位类型字段的候选或引用。
    """
    raw = _field(value, "location_type")
    normalized = getattr(raw, "value", raw)
    return normalized if isinstance(normalized, str) else None


def _candidate_items(value: Any) -> list[Any] | None:
    """从本题唯一检索 Wrap 中获取实际候选。

    Args:
        value: `archive_question_retrieval` Wrap 的注入值。
    """
    items = _field(value, "items")
    return items if isinstance(items, list) else None


def _citation_maps_to_candidate(citation: Any, candidates: Sequence[Any]) -> bool:
    """核对引用的文件、位置和摘录确实来自本题实际候选。

    Args:
        citation: 最终回答中的一条引用定位。
        candidates: 当前评测条目的实际检索候选。
    """
    filename = _field(citation, "filename")
    location = _location_type(citation)
    excerpt = _field(citation, "excerpt")
    try:
        start = int(_field(citation, "location_start"))
        end = int(_field(citation, "location_end"))
    except (TypeError, ValueError):
        return False
    if (
        not isinstance(filename, str)
        or not filename
        or not isinstance(excerpt, str)
        or not excerpt.strip()
    ):
        return False

    normalized_excerpt = _normalize(excerpt)
    for candidate in candidates:
        candidate_filename = _field(candidate, "filename")
        candidate_excerpt = _field(candidate, "excerpt")
        try:
            candidate_start = int(_field(candidate, "location_start"))
            candidate_end = int(_field(candidate, "location_end"))
        except (TypeError, ValueError):
            continue
        if (
            filename == candidate_filename
            and location is not None
            and location == _location_type(candidate)
            and candidate_start <= start <= end <= candidate_end
            and normalized_excerpt in _normalize(candidate_excerpt)
        ):
            return True
    return False


def _citation_matches_page(citation: Any, target: Any) -> bool:
    """判断已映射引用是否覆盖标注的 PDF 文件页码。

    Args:
        citation: 已验证来自本题候选的一条引用。
        target: 预期来源文件名和页码标注。
    """
    filename = _field(target, "filename")
    page = _field(target, "page")
    if not isinstance(filename, str) or type(page) is not int or page < 1:
        return False
    try:
        start = int(_field(citation, "location_start"))
        end = int(_field(citation, "location_end"))
    except (TypeError, ValueError):
        return False
    return (
        _field(citation, "filename") == filename
        and _location_type(citation) == "PDF_PAGE"
        and start <= page <= end
    )


def _failure(
    code: str,
    *,
    details: Mapping[str, object] | None = None,
) -> Evaluation:
    """构造只包含固定错误码的失败结果。

    Args:
        code: 白名单中的静态错误码。
        details: 可选的安全计数或布尔诊断字段。
    """
    safe_details: dict[str, object] = dict(details or {})
    safe_details["safe_error_code"] = code
    return Evaluation(score=0.0, reasoning=code, details=safe_details)


def world_bank_answer_evaluator(evaluable: Evaluable) -> Evaluation:
    """确定性检查响应、候选引用及冻结/扩展来源页。

    判分预期取自 Pixie `expectation`；`eval_metadata` 只用于案例分组，
    不参与冻结证据或扩展来源评分。
    `expectation` 接受 `answer_status`、`answer_fragments`、
    `answer_alternatives`、`frozen_target` 和 `extension_sources`。
    事实片段全部满足，且每组等价表达至少命中一个；来源页使用 `filename` 与 `page` 标识。

    Args:
        evaluable: 当前问题、检索 Wrap、问答输出与预期契约。
    """
    execution_values = _named_values(evaluable.eval_output, _EXECUTION_OUTPUT_NAME)
    if execution_values:
        if len(execution_values) != 1 or not isinstance(execution_values[0], Mapping):
            return _failure("INVALID_EXECUTION_OBSERVATION")
        execution = execution_values[0]
        status = execution.get("status")
        sample_index = execution.get("sample_index")
        attempt_count = execution.get("attempt_count")
        model_role = execution.get("model_role")
        if (
            status not in _EXECUTION_FAILURE_STATUSES | {"COMPLETED"}
            or type(sample_index) is not int
            or sample_index < 1
            or type(attempt_count) is not int
            or attempt_count < 0
            or model_role != "fr039_answer"
        ):
            return _failure("INVALID_EXECUTION_OBSERVATION")
        if status in _EXECUTION_FAILURE_STATUSES:
            return _failure(
                "EXECUTION_FAILED",
                details={
                    "execution_status": status,
                    "sample_index": sample_index,
                    "model_role": model_role,
                    "attempt_count": attempt_count,
                },
            )

    response_values = _named_values(evaluable.eval_output, _RESPONSE_OUTPUT_NAME)
    if len(response_values) != 1 or not isinstance(response_values[0], Mapping):
        return _failure("INVALID_RESPONSE_SHAPE")
    response = response_values[0]

    retrieval_values = _named_values(evaluable.eval_input, _RETRIEVAL_INPUT_NAME)
    if len(retrieval_values) != 1:
        return _failure("MISSING_RETRIEVAL_INPUT")
    candidates = _candidate_items(retrieval_values[0])
    if candidates is None:
        return _failure("INVALID_RETRIEVAL_INPUT")

    expectation = evaluable.expectation
    if not isinstance(expectation, Mapping):
        return _failure("INVALID_EXPECTATION")
    expected_status = expectation.get("answer_status")
    if expected_status not in _KNOWN_STATUSES:
        return _failure("INVALID_EXPECTED_STATUS")
    actual_status = response.get("answer_status")
    answer = response.get("answer")
    citations = response.get("citations")
    if (
        actual_status not in _KNOWN_STATUSES
        or not isinstance(answer, str)
        or not isinstance(citations, list)
    ):
        return _failure("INVALID_RESPONSE_SHAPE")

    answer_fragments = expectation.get("answer_fragments", [])
    if not isinstance(answer_fragments, list) or any(
        not isinstance(fragment, str) or not fragment.strip()
        for fragment in answer_fragments
    ):
        return _failure("INVALID_EXPECTED_FACTS")

    raw_alternatives = expectation.get("answer_alternatives")
    if raw_alternatives is None:
        answer_alternatives: list[list[str]] = []
    elif (
        isinstance(raw_alternatives, list)
        and all(
            isinstance(group, list)
            and group
            and all(isinstance(item, str) and item.strip() for item in group)
            for group in raw_alternatives
        )
    ):
        answer_alternatives = raw_alternatives
    else:
        return _failure("INVALID_EXPECTED_FACTS")

    if expected_status == "ANSWERED" and not answer_fragments and not answer_alternatives:
        return _failure("INVALID_EXPECTED_FACTS")

    status_matches = actual_status == expected_status
    if expected_status == "REFUSED_NO_EVIDENCE":
        refusal_passed = (
            status_matches and answer == _FIXED_REFUSAL and not citations
        )
        facts_passed = True
    else:
        refusal_passed = False
        facts_passed = bool(answer.strip()) and all(
            _normalize(fragment) in _normalize(answer)
            for fragment in answer_fragments
        ) and all(
            any(_normalize(alternative) in _normalize(answer) for alternative in group)
            for group in answer_alternatives
        )

    mapping_flags = [
        _citation_maps_to_candidate(citation, candidates)
        for citation in citations
    ]
    citation_mapping_passed: bool | None = (
        None
        if expected_status == "REFUSED_NO_EVIDENCE"
        else bool(citations) and all(mapping_flags)
    )

    frozen_target = expectation.get("frozen_target")
    if frozen_target is None:
        frozen_detail: dict[str, object] = {
            "applicable": False,
            "matched": None,
            "matched_page_count": 0,
        }
        frozen_passed = True
    elif not isinstance(frozen_target, Mapping):
        return _failure("INVALID_FROZEN_TARGET")
    else:
        mapped_citations = [
            citation
            for citation, mapped in zip(citations, mapping_flags, strict=True)
            if mapped
        ]
        frozen_matches = any(
            _citation_matches_page(citation, frozen_target)
            for citation in mapped_citations
        )
        frozen_detail = {
            "applicable": True,
            "matched": frozen_matches,
            "matched_page_count": int(frozen_matches),
        }
        frozen_passed = frozen_matches

    extension_sources = expectation.get("extension_sources", [])
    if not isinstance(extension_sources, list) or any(
        not isinstance(source, Mapping) for source in extension_sources
    ):
        return _failure("INVALID_EXTENSION_SOURCES")
    mapped_citations = [
        citation
        for citation, mapped in zip(citations, mapping_flags, strict=True)
        if mapped
    ]
    extension_match_count = sum(
        any(_citation_matches_page(citation, source) for citation in mapped_citations)
        for source in extension_sources
    )
    extension_detail = {
        "applicable": bool(extension_sources),
        "matched_page_count": extension_match_count,
        "expected_page_count": len(extension_sources),
        "semantic_support": _EXTENSION_MANUAL_STATUS,
    }

    response_contract_passed = status_matches and (
        refusal_passed
        if expected_status == "REFUSED_NO_EVIDENCE"
        else facts_passed and bool(citations)
    )
    score = float(
        response_contract_passed
        and citation_mapping_passed is not False
        and frozen_passed
    )
    safe_error_code = (
        "MISSING_CITATION"
        if expected_status == "ANSWERED" and not citations
        else "CITATION_NOT_IN_CANDIDATES"
        if expected_status == "ANSWERED" and citations and not citation_mapping_passed
        else "ANSWER_CONTRACT_FAILED"
        if not response_contract_passed
        else "FROZEN_EVIDENCE_NOT_MATCHED"
        if not frozen_passed
        else "OK"
    )
    return Evaluation(
        score=score,
        reasoning=safe_error_code,
        details={
            "safe_error_code": safe_error_code,
            "response_contract_passed": response_contract_passed,
            "answer_fragments_passed": facts_passed,
            "refusal_contract_passed": refusal_passed,
            "citation_mapping_passed": citation_mapping_passed,
            "frozen_evidence": frozen_detail,
            "extension_source_diagnostic": extension_detail,
        },
    )


__all__ = ["world_bank_answer_evaluator"]
