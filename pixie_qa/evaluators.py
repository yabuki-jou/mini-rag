"""定义 FR-042 项目档案助手的机械与语义评测器。"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pixie import Evaluation, Evaluable, create_agent_evaluator


_FIXED_REFUSAL = "正式档案中没有足够依据。"
_ALLOWED_TOOLS = {
    "list_formal_archives",
    "search_confirmed_archive_evidence",
}
_FORBIDDEN_KEYS = {
    "owner_id",
    "user_id",
    "project_id",
    "kb_id",
    "session_id",
    "thread_id",
    "document_id",
    "chunk_id",
    "score",
    "reranker_score",
    "access_token",
    "refresh_token",
}
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def _item_name_value(item: object) -> tuple[str | None, object]:
    """兼容 Pixie NamedData 与测试字典并读取名称和值。"""
    if isinstance(item, Mapping):
        name = item.get("name")
        return (str(name) if isinstance(name, str) else None, item.get("value"))
    name = getattr(item, "name", None)
    return (
        str(name) if isinstance(name, str) else None,
        getattr(item, "value", None),
    )


def _named_outputs(evaluable: Evaluable, base_name: str) -> list[object]:
    """按原始顺序读取基础名称及其稳定编号副本。"""
    values: list[object] = []
    for item in evaluable.eval_output:
        name, value = _item_name_value(item)
        if name == base_name or (
            isinstance(name, str)
            and name.startswith(f"{base_name}__")
            and name.removeprefix(f"{base_name}__").isdigit()
        ):
            values.append(value)
    return values


def _normalize_text(value: object) -> str:
    """保守统一全半角、大小写和空白，供冻结片段机械核验。"""
    return re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(value)).casefold(),
    )


def _citation_key(value: object) -> tuple[object, ...] | None:
    """生成公开引用或安全候选可机械比较的五字段键。"""
    if not isinstance(value, Mapping):
        return None
    try:
        return (
            value["filename"],
            value["location_type"],
            int(value["location_start"]),
            int(value["location_end"]),
            value["excerpt"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _responses_with_last_safe_result(
    evaluable: Evaluable,
) -> list[tuple[object, object | None]]:
    """按 Trace 顺序把每轮响应关联到该轮最后一次安全工具结果。"""
    pairs: list[tuple[object, object | None]] = []
    last_safe: object | None = None
    for item in evaluable.eval_output:
        name, value = _item_name_value(item)
        if name == "archive_agent_safe_tool_result" or (
            isinstance(name, str)
            and name.startswith("archive_agent_safe_tool_result__")
        ):
            last_safe = value
        elif name == "archive_agent_response" or (
            isinstance(name, str) and name.startswith("archive_agent_response__")
        ):
            pairs.append((value, last_safe))
            last_safe = None
    return pairs


def _expected_list(metadata: Mapping[str, object], name: str) -> list[object] | None:
    """读取可选的逐轮 Ground Truth 数组。"""
    value = metadata.get(name)
    return value if isinstance(value, list) else None


def archive_agent_structural_contract(evaluable: Evaluable) -> Evaluation:
    """机械检查工具预算、响应形状、引用映射、历史和审计一致性。"""
    metadata = evaluable.eval_metadata or {}
    pairs = _responses_with_last_safe_result(evaluable)
    responses = [response for response, _ in pairs]
    routes = _named_outputs(evaluable, "archive_agent_routing_decision")
    tool_turns = _named_outputs(evaluable, "archive_agent_tool_calls")
    states = _named_outputs(evaluable, "archive_agent_conversation_state")
    problems: list[str] = []

    expected_turn_count = metadata.get("expected_turn_count", len(responses))
    if not isinstance(expected_turn_count, int) or isinstance(expected_turn_count, bool):
        problems.append("expected_turn_count 必须为整数")
        expected_turn_count = len(responses)
    if not (
        len(responses)
        == len(routes)
        == len(tool_turns)
        == expected_turn_count
    ):
        problems.append("响应、路由、工具观测数量与预期轮数不一致")

    expected_statuses = _expected_list(metadata, "expected_answer_statuses")
    expected_kinds = _expected_list(metadata, "expected_answer_kinds")
    expected_paths = _expected_list(metadata, "expected_tool_paths")
    expected_fragments = _expected_list(metadata, "expected_answer_fragments")
    audit_count = 0

    for index, ((response, safe_result), route, tool_events) in enumerate(
        zip(pairs, routes, tool_turns, strict=False)
    ):
        turn = index + 1
        if not isinstance(response, Mapping):
            problems.append(f"第 {turn} 轮缺少响应对象")
            continue
        if not isinstance(route, Mapping):
            problems.append(f"第 {turn} 轮缺少路由对象")
            continue
        if not isinstance(tool_events, list) or len(tool_events) > 2:
            problems.append(f"第 {turn} 轮工具观测不是最多两项的数组")
            tool_events = []
        audit_count += len(tool_events)
        tool_names: list[object] = []
        for event in tool_events:
            if not isinstance(event, Mapping):
                problems.append(f"第 {turn} 轮工具观测不是对象")
                continue
            tool_name = event.get("tool_name")
            tool_names.append(tool_name)
            if tool_name not in _ALLOWED_TOOLS:
                problems.append(f"第 {turn} 轮出现未注册工具")
            if event.get("status") not in {"COMPLETED", "FAILED"}:
                problems.append(f"第 {turn} 轮工具状态无效")
            if event.get("attempt_count") not in {1, 2}:
                problems.append(f"第 {turn} 轮工具重试次数无效")

        route_count = route.get("tool_call_count")
        if route_count != len(tool_events) or route.get("tool_names") != tool_names:
            problems.append(f"第 {turn} 轮路由工具数量或顺序与审计不一致")
        if expected_paths is not None and index < len(expected_paths):
            if tool_names != expected_paths[index]:
                problems.append(f"第 {turn} 轮工具路径不符合固定预期")

        status = response.get("answer_status")
        answer_kind = route.get("answer_kind")
        citations = response.get("citations")
        answer = response.get("answer")
        if status not in {"ANSWERED", "REFUSED_NO_EVIDENCE"}:
            problems.append(f"第 {turn} 轮回答状态无效")
        if route.get("answer_status") != status:
            problems.append(f"第 {turn} 轮路由状态与响应状态不一致")
        if not isinstance(answer, str) or not answer.strip():
            problems.append(f"第 {turn} 轮回答正文为空")
        if not isinstance(citations, list):
            problems.append(f"第 {turn} 轮 citations 不是数组")
            citations = []

        if expected_statuses is not None and index < len(expected_statuses):
            if status != expected_statuses[index]:
                problems.append(f"第 {turn} 轮回答状态不符合固定预期")
        if expected_kinds is not None and index < len(expected_kinds):
            if answer_kind != expected_kinds[index]:
                problems.append(f"第 {turn} 轮回答类型不符合固定预期")
        if expected_fragments is not None and index < len(expected_fragments):
            fragments = expected_fragments[index]
            if not isinstance(fragments, list) or any(
                _normalize_text(fragment) not in _normalize_text(answer)
                for fragment in fragments
            ):
                problems.append(f"第 {turn} 轮回答缺少固定事实片段")

        if answer_kind == "REFUSED":
            if status != "REFUSED_NO_EVIDENCE" or answer != _FIXED_REFUSAL or citations:
                problems.append(f"第 {turn} 轮拒答形状不符合冻结契约")
        elif answer_kind == "CATALOG":
            if status != "ANSWERED" or citations:
                problems.append(f"第 {turn} 轮目录响应状态或引用形状无效")
            if not isinstance(safe_result, Mapping) or not isinstance(
                safe_result.get("items"), list
            ):
                problems.append(f"第 {turn} 轮目录响应缺少当前安全目录结果")
        elif answer_kind == "ANSWERED":
            results = safe_result.get("results") if isinstance(safe_result, Mapping) else None
            candidate_keys = {
                key
                for item in results if (key := _citation_key(item)) is not None
            } if isinstance(results, list) else set()
            if status != "ANSWERED" or not citations:
                problems.append(f"第 {turn} 轮有据回答必须包含引用")
            for citation in citations:
                if _citation_key(citation) not in candidate_keys:
                    problems.append(f"第 {turn} 轮引用无法映射到当前安全证据")
        else:
            problems.append(f"第 {turn} 轮回答类型无效")

    if len(states) != 1 or not isinstance(states[0], Mapping):
        problems.append("缺少唯一会话状态观测")
    else:
        state = states[0]
        expected_roles = [
            role
            for _ in range(expected_turn_count)
            for role in ("USER", "ASSISTANT")
        ]
        if state.get("requested_turn_count") != expected_turn_count:
            problems.append("会话状态轮数与预期不一致")
        if state.get("completed_http_statuses") != [200] * expected_turn_count:
            problems.append("会话存在未完成的消息 HTTP 状态")
        if state.get("history_message_count") != expected_turn_count * 2:
            problems.append("历史消息数量与完整轮次不一致")
        if state.get("history_roles") != expected_roles:
            problems.append("历史角色顺序不是完整 USER/ASSISTANT 轮次")
        if state.get("audit_record_count") != audit_count:
            problems.append("工具审计总数与逐轮工具观测不一致")

    return Evaluation(
        score=1.0 if not problems else 0.0,
        reasoning="机械契约检查通过。" if not problems else "；".join(problems),
        details={"problem_count": len(problems)},
    )


def _collect_forbidden(value: object, found: set[str]) -> None:
    """递归查找 Wrap 输出中的禁用键、Token 形状和 UUID。"""
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_KEYS:
                found.add(key_text)
            _collect_forbidden(item, found)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _collect_forbidden(item, found)
        return
    if isinstance(value, str):
        if _UUID_PATTERN.search(value):
            found.add("UUID")
        if "Bearer " in value:
            found.add("Bearer token")


def archive_agent_trace_privacy(evaluable: Evaluable) -> Evaluation:
    """机械检查全部 Wrap 输出不含内部范围、持久化标识、分数或 Token。"""
    found: set[str] = set()
    for item in evaluable.eval_output:
        _, value = _item_name_value(item)
        _collect_forbidden(value, found)
    return Evaluation(
        score=1.0 if not found else 0.0,
        reasoning=(
            "Trace 脱敏检查通过。"
            if not found
            else f"Trace 出现禁止字段或值形状: {','.join(sorted(found))}"
        ),
        details={"forbidden_count": len(found)},
    )


def archive_agent_failure_contract(evaluable: Evaluable) -> Evaluation:
    """机械检查受控失败的 HTTP、错误码、工具审计和历史副作用。"""
    metadata = evaluable.eval_metadata or {}
    failures = _named_outputs(evaluable, "archive_agent_failure_state")
    responses = _named_outputs(evaluable, "archive_agent_response")
    problems: list[str] = []
    if len(failures) != 1 or not isinstance(failures[0], Mapping):
        problems.append("缺少唯一的失败状态观测")
        failure: Mapping[str, object] = {}
    else:
        failure = failures[0]
    if responses:
        problems.append("失败轮次不得产生成功响应观测")

    expected_status = metadata.get("expected_http_status")
    expected_error = metadata.get("expected_error_code")
    expected_history_count = metadata.get("expected_history_message_count")
    expected_audit = metadata.get("expected_tool_audit")
    if failure.get("http_status") != expected_status:
        problems.append("失败 HTTP 状态不符合固定预期")
    if failure.get("error_code") != expected_error:
        problems.append("失败业务错误码不符合固定预期")
    if failure.get("history_message_count") != expected_history_count:
        problems.append("失败后的历史数量不符合固定预期")
    history_roles = failure.get("history_roles")
    if not isinstance(history_roles, list) or len(history_roles) != failure.get(
        "history_message_count"
    ):
        problems.append("失败后的历史角色形状无效")

    audit = failure.get("tool_audit")
    if isinstance(expected_audit, list) and audit != expected_audit:
        problems.append("失败工具审计不符合固定预期")
    if not isinstance(audit, list):
        problems.append("失败工具审计不是数组")
    else:
        if expected_audit is None and not any(
            isinstance(item, Mapping) and item.get("status") == "FAILED"
            for item in audit
        ):
            problems.append("失败工具审计缺少 FAILED 记录")
        for item in audit:
            if not isinstance(item, Mapping):
                problems.append("失败工具审计项不是对象")
                continue
            if item.get("tool_name") not in _ALLOWED_TOOLS:
                problems.append("失败工具审计出现未注册工具")
            if item.get("status") not in {"COMPLETED", "FAILED"}:
                problems.append("失败工具审计状态无效")

    return Evaluation(
        score=1.0 if not problems else 0.0,
        reasoning="失败契约检查通过。" if not problems else "；".join(problems),
        details={"problem_count": len(problems)},
    )


archive_agent_tool_path_quality = create_agent_evaluator(
    name="ArchiveAgentToolPathQuality",
    criteria=(
        "结合 input_data、每轮 archive_agent_tool_calls、archive_agent_routing_decision 和安全工具结果，"
        "评审模型是否按用户意图选择目录或证据工具、调用顺序是否必要且不超过两次、目录筛选与分页是否"
        "符合问题；零工具调用不得直接陈述档案事实。不能仅因最终答案正确就忽略错误或多余的工具路径。"
    ),
)


archive_agent_evidence_faithfulness = create_agent_evaluator(
    name="ArchiveAgentEvidenceFaithfulness",
    criteria=(
        "逐轮比较 archive_agent_response 与该轮最后一次 archive_agent_safe_tool_result。最终回答的每个项目"
        "事实必须由候选 excerpt 直接支持，引用必须对应真实文件、位置和摘录；相近主题、目录字段、上轮证据"
        "或模型常识不能补成答案。证据不足时必须固定拒答，有直接证据时不应错误拒答。多轮案例必须逐轮"
        "分别评分，最终分数采用各轮最低分；任一轮核心事实失败时总分必须低于 0.5。"
    ),
)


archive_agent_multiturn_freshness = create_agent_evaluator(
    name="ArchiveAgentMultiTurnFreshness",
    criteria=(
        "对两轮案例检查第二轮是否理解省略主语并保留同一会话上下文，同时只依据第二轮最后一次成功工具"
        "结果回答；不得把第一轮候选直接当成第二轮证据。archive_agent_conversation_state 应显示完整可见轮次，"
        "第二轮工具路径、回答和引用应与第二轮问题一致。单轮案例按不适用通过。"
    ),
)


__all__ = [
    "archive_agent_evidence_faithfulness",
    "archive_agent_failure_contract",
    "archive_agent_multiturn_freshness",
    "archive_agent_structural_contract",
    "archive_agent_tool_path_quality",
    "archive_agent_trace_privacy",
]
