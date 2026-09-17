"""从既有正式捕获资料构建 FR-042 项目档案助手安全评测集。"""

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pixie.instrumentation.wrap import serialize_wrap_data

from app.services.archive.catalog import AgentCatalogPage
from evals.archive.reference_world import (
    reference_catalog_result,
    reference_evidence_result,
)
from scripts.generate_archive_v1_eval_data import NORMAL_RECORDS


SOURCE_PATH = (
    PROJECT_ROOT
    / "pixie_qa"
    / "results"
    / "d5-formal-20260908"
    / "captured-dataset.json"
)
OUTPUT_PATH = PROJECT_ROOT / "pixie_qa" / "datasets" / "archive-agent-mvp.json"
_FIXED_REFUSAL = "正式档案中没有足够依据。"
_FORMAL_DOCUMENT_TITLES = {
    str(record["filename"]): str(record["title"])
    for record in NORMAL_RECORDS
}
_SUCCESS_EVALUATORS = [
    "...",
    "pixie_qa/evaluators.py:archive_agent_structural_contract",
    "pixie_qa/evaluators.py:archive_agent_tool_path_quality",
    "pixie_qa/evaluators.py:archive_agent_evidence_faithfulness",
]


def _safe_evidence(entry: dict[str, Any]) -> list[dict[str, object]]:
    """剥离持久化字段，并用同源正式金标补齐未召回的直接证据。"""
    retrieval = next(
        item["value"]
        for item in entry["eval_input"]
        if item["name"] == "archive_question_retrieval"
    )
    document_refs: dict[str, str] = {}
    safe: list[dict[str, object]] = []
    for item in retrieval["items"][:8]:
        document_id = str(item["document_id"])
        document_ref = document_refs.setdefault(
            document_id,
            f"D{len(document_refs) + 1}",
        )
        safe.append(
            {
                "document_ref": document_ref,
                "filename": item["filename"],
                "document_title": _FORMAL_DOCUMENT_TITLES.get(str(item["filename"])),
                "location_type": item["location_type"],
                "location_start": item["location_start"],
                "location_end": item["location_end"],
                "excerpt": item["excerpt"],
            }
        )
    metadata = entry["eval_metadata"]
    expected_answer = metadata.get("expected_answer")
    expected_evidence = metadata.get("expected_evidence")
    if (
        metadata.get("expected_answer_status") == "ANSWERED"
        and isinstance(expected_answer, str)
        and expected_answer
        and expected_answer not in json.dumps(safe, ensure_ascii=False)
        and isinstance(expected_evidence, dict)
    ):
        filename = Path(str(expected_evidence["relative_path"])).name
        document_ref = next(
            (
                str(item["document_ref"])
                for item in safe
                if item["filename"] == filename
            ),
            f"D{len(set(document_refs.values())) + 1}",
        )
        for item in expected_evidence.get("items", []):
            safe.append(
                {
                    "document_ref": document_ref,
                    "filename": filename,
                    "document_title": _FORMAL_DOCUMENT_TITLES.get(filename),
                    "location_type": item["location_type"],
                    "location_start": item["location_start"],
                    "location_end": item["location_end"],
                    "excerpt": item["excerpt"],
                }
            )
    return safe


def _merge_evidence_worlds(
    *worlds: list[dict[str, object]],
) -> list[dict[str, object]]:
    """合并多轮候选并按文件重新生成无冲突的请求内引用。"""
    document_refs: dict[str, str] = {}
    seen: set[tuple[object, ...]] = set()
    merged: list[dict[str, object]] = []
    for item in (candidate for world in worlds for candidate in world):
        key = (
            item["filename"],
            item["location_type"],
            item["location_start"],
            item["location_end"],
            item["excerpt"],
        )
        if key in seen:
            continue
        seen.add(key)
        filename = str(item["filename"])
        document_ref = document_refs.setdefault(
            filename,
            f"D{len(document_refs) + 1}",
        )
        merged.append({**item, "document_ref": document_ref})
    return merged


def _catalog_from_evidence(evidence: list[dict[str, object]]) -> dict[str, object]:
    """从已捕获候选文件名构造仅供错误路由保护的安全目录投影。"""
    filenames = list(dict.fromkeys(str(item["filename"]) for item in evidence))
    items = [
        {
            "document_ref": f"A{index}",
            "filename": filename,
            "confirmed_at": None,
            "fields": {
                name: {
                    "value": None,
                    "source": None,
                    "has_source_evidence": False,
                }
                for name in (
                    "TITLE",
                    "DOCUMENT_TYPE",
                    "DOCUMENT_DATE",
                    "AUTHORING_ORGANIZATION",
                    "PROJECT_STAGE",
                )
            },
        }
        for index, filename in enumerate(filenames[:20], start=1)
    ]
    return serialize_wrap_data(
        AgentCatalogPage(page=1, page_size=20, total=len(items), items=items)
    )


def _world_inputs(
    *,
    catalog: object,
    evidence_by_call: list[list[dict[str, object]]],
    max_calls: int,
) -> list[dict[str, object]]:
    """为允许的工具顺序准备每个稳定 Wrap 名称的完整注入值。"""
    values: list[dict[str, object]] = []
    for index in range(1, max_calls + 1):
        suffix = "" if index == 1 else f"__{index}"
        evidence = evidence_by_call[min(index - 1, len(evidence_by_call) - 1)]
        values.extend(
            [
                {
                    "name": f"archive_agent_catalog_result{suffix}",
                    "value": deepcopy(catalog),
                },
                {
                    "name": f"archive_agent_evidence_retrieval{suffix}",
                    "value": deepcopy(evidence),
                },
            ]
        )
    return values


def _single_turn_entry(
    source_entry: dict[str, Any],
    *,
    index: int,
) -> dict[str, object]:
    """把 AV1-P02 单题转换为 Archive Agent 的安全评测条目。"""
    source_metadata = source_entry["eval_metadata"]
    category = str(source_metadata["category"])
    expected_status = str(source_metadata["expected_answer_status"])
    expected_answer = source_metadata.get("expected_answer")
    answer_kind = "ANSWERED" if category == "GROUNDED" else "REFUSED"
    fragments = [str(expected_answer)] if expected_answer else [_FIXED_REFUSAL]
    expected_evidence = source_metadata.get("expected_evidence")
    expected_filename = (
        Path(str(expected_evidence["relative_path"])).name
        if answer_kind == "ANSWERED" and isinstance(expected_evidence, dict)
        else None
    )
    evidence = _safe_evidence(source_entry)
    difficulty = (
        "routine"
        if index < 4
        else "moderate"
        if index < 6
        else "challenging"
    ) if category == "GROUNDED" else "challenging"
    capabilities = ["evidence_qa", "citations", "scope_audit"]
    if category in {"NO_EVIDENCE", "ISOLATION"}:
        capabilities.append("safe_refusal")
    return {
        "description": f"{source_metadata['case_id']} {category} 正式捕获候选",
        "input_data": {"messages": [source_entry["input_data"]["question"]]},
        "eval_input": _world_inputs(
            catalog=_catalog_from_evidence(evidence),
            evidence_by_call=[evidence],
            max_calls=2,
        ),
        "expectation": (
            f"应只依据当前候选回答并包含事实：{expected_answer}"
            if category == "GROUNDED"
            else f"应返回固定拒答：{_FIXED_REFUSAL}"
        ),
        "eval_metadata": {
            "case_id": source_metadata["case_id"],
            "category": category,
            "difficulty": difficulty,
            "source_provenance": "AV1_P02_CAPTURED_20260908",
            "evidence_world_basis": (
                "CAPTURED_TOP5"
                if source_metadata.get("public_coverage_match")
                or category != "GROUNDED"
                else "CAPTURED_TOP5_PLUS_FORMAL_GOLD"
            ),
            "capabilities": capabilities,
            "expected_turn_count": 1,
            "expected_answer_statuses": [expected_status],
            "expected_answer_kinds": [answer_kind],
            "expected_tool_paths": [["search_confirmed_archive_evidence"]],
            "expected_answer_fragments": [fragments],
            **(
                {
                    "expected_document_filenames": [expected_filename],
                    "expected_document_titles": [
                        _FORMAL_DOCUMENT_TITLES[expected_filename]
                    ],
                }
                if expected_filename is not None
                else {}
            ),
        },
        "evaluators": list(_SUCCESS_EVALUATORS),
    }


def _catalog_entries() -> list[dict[str, object]]:
    """构造两个来自参考 Trace 世界数据的目录场景。"""
    catalog = serialize_wrap_data(
        reference_catalog_result(
            page=1,
            page_size=10,
            document_type=None,
            project_stage=None,
        )
    )
    evidence = reference_evidence_result()
    cases = (
        (
            "CATALOG-01",
            "请列出本项目施工阶段、文件类型为合同的正式档案，第1页每页10份。",
            "routine",
            ["第1页", "1份", "PX-BETA-cover.txt"],
        ),
        (
            "CATALOG-02",
            "请按编制单位 North Star Build Lab 筛选正式档案，返回第一页。",
            "moderate",
            ["第1页", "North Star Build Lab", "PX-BETA-cover.txt"],
        ),
    )
    return [
        {
            "description": f"{case_id} 正式目录筛选与分页",
            "input_data": {"messages": [message]},
            "eval_input": _world_inputs(
                catalog=catalog,
                evidence_by_call=[evidence],
                max_calls=2,
            ),
            "expectation": "应使用目录工具，并按冻结分页模板返回当前页和五字段投影。",
            "eval_metadata": {
                "case_id": case_id,
                "category": "CATALOG",
                "difficulty": difficulty,
                "source_provenance": "FR042_REFERENCE_TRACE",
                "capabilities": ["catalog_navigation", "scope_audit"],
                "expected_turn_count": 1,
                "expected_answer_statuses": ["ANSWERED"],
                "expected_answer_kinds": ["CATALOG"],
                "expected_tool_paths": [["list_formal_archives"]],
                "expected_answer_fragments": [fragments],
            },
            "evaluators": [
                "...",
                "pixie_qa/evaluators.py:archive_agent_structural_contract",
                "pixie_qa/evaluators.py:archive_agent_tool_path_quality",
            ],
        }
        for case_id, message, difficulty, fragments in cases
    ]


def _multi_turn_entries(source_entries: list[dict[str, Any]]) -> list[dict[str, object]]:
    """构造参考 Trace 和两组正式捕获候选的多轮追问。"""
    reference_catalog = serialize_wrap_data(
        reference_catalog_result(
            page=1,
            page_size=20,
            document_type=None,
            project_stage=None,
        )
    )
    reference_evidence = reference_evidence_result()
    first = _safe_evidence(source_entries[0])
    second = _safe_evidence(source_entries[1])
    combined = _merge_evidence_worlds(first, second)
    return [
        {
            "description": "MULTI-01 责任单位后的省略主语周期追问",
            "input_data": {
                "messages": [
                    "PX-BETA 项目的归档责任单位是什么？",
                    "该单位安排在第几个周期复核归档索引？",
                ]
            },
            "eval_input": _world_inputs(
                catalog=reference_catalog,
                evidence_by_call=[reference_evidence, reference_evidence],
                max_calls=4,
            ),
            "expectation": "第一轮回答 North Star Build Lab，第二轮只依据新证据回答第 3 个周期。",
            "eval_metadata": {
                "case_id": "MULTI-01",
                "category": "MULTI_TURN",
                "difficulty": "challenging",
                "source_provenance": "FR042_REFERENCE_TRACE",
                "capabilities": ["evidence_qa", "multi_turn", "citations", "scope_audit"],
                "expected_turn_count": 2,
                "expected_answer_statuses": ["ANSWERED", "ANSWERED"],
                "expected_answer_kinds": ["ANSWERED", "ANSWERED"],
                "expected_tool_paths": [
                    ["search_confirmed_archive_evidence"],
                    ["search_confirmed_archive_evidence"],
                ],
                "expected_answer_fragments": [["North Star"], ["3"]],
                "expected_document_filenames": [
                    "PX-BETA-cover.txt",
                    "PX-BETA-timeline.txt",
                ],
                "expected_document_titles": [
                    "PX-BETA 归档责任说明",
                    "PX-BETA 归档索引复核计划",
                ],
            },
            "evaluators": [
                *_SUCCESS_EVALUATORS,
                "pixie_qa/evaluators.py:archive_agent_multiturn_freshness",
            ],
        },
        {
            "description": "MULTI-02 合同日期后的跨文件编制单位追问",
            "input_data": {
                "messages": [
                    source_entries[0]["input_data"]["question"],
                    "那设计说明的编制单位呢？",
                ]
            },
            "eval_input": _world_inputs(
                catalog=_catalog_from_evidence([*first, *second]),
                evidence_by_call=[combined],
                max_calls=4,
            ),
            "expectation": "第一轮回答 2025-03-18，第二轮只依据新候选回答北辰设计院。",
            "eval_metadata": {
                "case_id": "MULTI-02",
                "category": "MULTI_TURN",
                "difficulty": "challenging",
                "source_provenance": "AV1_P02_CAPTURED_20260908",
                "capabilities": ["evidence_qa", "multi_turn", "citations", "scope_audit"],
                "expected_turn_count": 2,
                "expected_answer_statuses": ["ANSWERED", "ANSWERED"],
                "expected_answer_kinds": ["ANSWERED", "ANSWERED"],
                "expected_tool_paths": [
                    ["search_confirmed_archive_evidence"],
                    ["search_confirmed_archive_evidence"],
                ],
                "expected_answer_fragments": [["2025-03-18"], ["北辰设计院"]],
                "expected_document_filenames": [
                    Path(
                        str(source_entries[0]["eval_metadata"]["expected_evidence"]["relative_path"])
                    ).name,
                    Path(
                        str(source_entries[1]["eval_metadata"]["expected_evidence"]["relative_path"])
                    ).name,
                ],
                "expected_document_titles": [
                    _FORMAL_DOCUMENT_TITLES[
                        Path(
                            str(
                                source_entries[0]["eval_metadata"]["expected_evidence"][
                                    "relative_path"
                                ]
                            )
                        ).name
                    ],
                    _FORMAL_DOCUMENT_TITLES[
                        Path(
                            str(
                                source_entries[1]["eval_metadata"]["expected_evidence"][
                                    "relative_path"
                                ]
                            )
                        ).name
                    ],
                ],
            },
            "evaluators": [
                *_SUCCESS_EVALUATORS,
                "pixie_qa/evaluators.py:archive_agent_multiturn_freshness",
            ],
        },
    ]


def _failure_entry(source_entries: list[dict[str, Any]]) -> dict[str, object]:
    """构造两个工具都会在输入形状处受控失败的质量条目。"""
    evidence = _safe_evidence(source_entries[0])
    invalid_evidence = [deepcopy(evidence[0]) for _ in range(9)]
    return {
        "description": "FAILURE-01 外部世界数据形状异常时受控失败且不写历史",
        "input_data": {"messages": ["请查询合同签订日期的原文证据。"]},
        "eval_input": _world_inputs(
            catalog={"invalid": True},
            evidence_by_call=[invalid_evidence],
            max_calls=2,
        ),
        "expectation": "应返回受控 503，不产生成功回答，并保留脱敏失败审计。",
        "eval_metadata": {
            "case_id": "FAILURE-01",
            "category": "CONTROLLED_FAILURE",
            "difficulty": "challenging",
            "source_provenance": "FR042_CONTROLLED_FAILURE",
            "capabilities": ["scope_audit", "safe_refusal"],
            "expected_http_status": 503,
            "expected_error_code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
            "expected_history_message_count": 0,
        },
        "evaluators": [
            "...",
            "pixie_qa/evaluators.py:archive_agent_failure_contract",
        ],
    }


def _validate_answer_bindings(entries: list[dict[str, object]]) -> None:
    """在真实模型运行前校验答案事实属于冻结的目标文档。"""
    for entry in entries:
        metadata = entry["eval_metadata"]
        if not isinstance(metadata, dict):
            continue
        expected_kinds = metadata.get("expected_answer_kinds")
        if not isinstance(expected_kinds, list) or any(
            kind != "ANSWERED" for kind in expected_kinds
        ):
            continue
        expected_fragments = metadata.get("expected_answer_fragments")
        expected_filenames = metadata.get("expected_document_filenames")
        expected_titles = metadata.get("expected_document_titles")
        if not isinstance(expected_fragments, list) or not isinstance(
            expected_filenames,
            list,
        ) or not isinstance(expected_titles, list) or not (
            len(expected_fragments) == len(expected_filenames) == len(expected_titles)
        ):
            raise ValueError(f"{metadata['case_id']} 缺少逐轮目标文档绑定。")
        evidence_worlds = [
            item["value"]
            for item in entry["eval_input"]
            if str(item["name"]).startswith("archive_agent_evidence_retrieval")
        ]
        for world in evidence_worlds:
            for fragments, filename, title in zip(
                expected_fragments,
                expected_filenames,
                expected_titles,
                strict=True,
            ):
                document_refs = {
                    item["document_ref"]
                    for item in world
                    if item["filename"] == filename
                    and item.get("document_title") == title
                }
                bound_text = json.dumps(
                    [
                        item
                        for item in world
                        if item["document_ref"] in document_refs
                    ],
                    ensure_ascii=False,
                )
                if not document_refs or any(
                    str(fragment) not in bound_text for fragment in fragments
                ):
                    raise ValueError(
                        f"{metadata['case_id']} 的答案事实未绑定到 {filename}。"
                    )


def build_dataset() -> dict[str, object]:
    """读取既有捕获并组合 17 条 FR-042 评测条目。"""
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    source_entries = list(source["entries"])
    if len(source_entries) != 12:
        raise ValueError("AV1-P02 捕获条目数量必须为 12。")
    entries = [
        _single_turn_entry(entry, index=index)
        for index, entry in enumerate(source_entries)
    ]
    entries.extend(_catalog_entries())
    entries.extend(_multi_turn_entries(source_entries))
    entries.append(_failure_entry(source_entries))
    _validate_answer_bindings(entries)
    return {
        "name": "archive-agent-mvp",
        "runnable": "pixie_qa/run_app.py:AppRunnable",
        "evaluators": [
            "pixie_qa/evaluators.py:archive_agent_trace_privacy",
        ],
        "entries": entries,
    }


def main() -> None:
    """生成格式化且只含脱敏评测资料的最终 JSON。"""
    dataset = build_dataset()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"generated {OUTPUT_PATH} with {len(dataset['entries'])} entries")


if __name__ == "__main__":
    main()
