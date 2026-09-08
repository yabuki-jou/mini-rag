"""读取 P14-D3 安全结果并生成脱敏的 base/large 联合报告。"""

import argparse
import json
from pathlib import Path
from typing import Any

_SAFE = ("candidate_count", "chroma_candidate_count", "category", "case_id",
         "expected_candidate_rank", "expected_distance", "expected_in_chroma_top_10",
         "candidate_pool_complete", "standard_evidence_in_complete_top_30",
         "public_result_contains_expected_evidence", "matching_semantics_disagree", "candidate_kind")


def load_json(path: Path) -> dict[str, Any]:
    """读取对象 JSON；错误信息只包含字段错误，不泄露路径或正文。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("JSON 文件不可读取。") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON 顶层必须是对象。")
    return value


def _diagnostic(value: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """校验固定集诊断并生成排除 Reranker 字段的候选指纹。"""
    if value.get("stage") != "c4_a_candidate_pool":
        raise ValueError("diagnostic.stage 无效。")
    if value.get("reranker_query_mode", "c4_a") != "c4_a":
        raise ValueError("diagnostic.reranker_query_mode 无效。")
    rows = value.get("retrieval_diagnostics")
    if not isinstance(rows, list) or len(rows) != 12:
        raise ValueError("diagnostic 必须包含固定 12 题。")
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("candidate_count") != 30 or row.get("chroma_candidate_count") != 30:
            raise ValueError("每题候选数量必须为 30。")
        result.append(tuple((key, row.get(key)) for key in _SAFE) + tuple((key, row.get(key)) for key in ("nearest_candidate_distance", "nearest_incorrect_distance", "incorrect_candidate_kind")))
    return tuple(result)


def _metrics(value: dict[str, Any]) -> dict[str, Any]:
    """提取聚合质量与延迟指标，不复制未知字段。"""
    keys = ("grounded_returned_count", "grounded_public_coverage_hits", "grounded_candidate_pool_total",
            "no_evidence_rejected_count", "isolation_passed_count",
            "latency_p95_ms")
    if any(not isinstance(value.get(key), (int, float)) or isinstance(value.get(key), bool) for key in keys):
        raise ValueError("聚合结果缺少有效数值字段。")
    return {key: value[key] for key in keys}


def build_report(base: dict[str, Any], base_diag: dict[str, Any], large: dict[str, Any], large_diag: dict[str, Any]) -> dict[str, Any]:
    """构造联合决策报告；不标定或修改任何阈值。"""
    left, right = _diagnostic(base_diag), _diagnostic(large_diag)
    def gate(item: dict[str, Any]) -> dict[str, Any]:
        public = item.get("grounded_public_coverage_hits", 0) >= 7 and item.get("grounded_candidate_pool_total", 0) >= 8
        quality = public and item.get("no_evidence_rejected_count") == 2 and item.get("isolation_passed_count") == 2
        return {"public_coverage_passed": public, "no_evidence_passed": item.get("no_evidence_rejected_count") == 2,
                "isolation_passed": item.get("isolation_passed_count") == 2, "passed": quality}
    def perf(item: dict[str, Any]) -> dict[str, Any]:
        return {"latency_p95_ms": item.get("latency_p95_ms"), "passed": isinstance(item.get("latency_p95_ms"), (int, float)) and item["latency_p95_ms"] <= 8000}
    return {"base": _metrics(base), "large": _metrics(large), "candidate_snapshot_equal": left == right,
            "base_quality_gate": gate(base), "large_quality_gate": gate(large),
            "base_performance_gate": perf(base), "large_performance_gate": perf(large)}


def main() -> int:
    """运行安全文件联合检查。"""
    parser = argparse.ArgumentParser()
    for name in ("base-result", "base-diagnostic", "large-result", "large-diagnostic", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        report = build_report(*(load_json(getattr(args, key)) for key in ("base_result", "base_diagnostic", "large_result", "large_diagnostic")))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("P14-D3 输入字段错误。")
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
