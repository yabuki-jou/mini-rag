"""把正式 D5 Pixie 结果目录汇总为安全的离线验收摘要。"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

from pixie import Evaluable

from pixie_qa.archive_v1_p14.evaluators import aggregate_archive_v1_p02_results


_P02_NAME = "archive_v1_p02_quality_gate"
_AGENT_NAMES = {"ArchiveD5EvidenceFaithfulness", "ArchiveD5RefusalQuality"}
_SAFE_ENTRY_KEYS = {
    "case_id", "category", "candidate_pool_complete", "retrieval_latency_ms",
    "answer_status", "answer_contains_expected", "citation_matches_expected", "entry_passed",
}


def _read_json(path: Path) -> dict[str, Any]:
    """读取一个 JSON 对象文件；参数 path 为结果目录内的文件路径。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 对象；参数 path 为结果目录内的 JSONL 文件路径。"""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} 每行必须是 JSON 对象")
            rows.append(value)
    return rows


def _evaluable(entry: Path) -> tuple[Evaluable, dict[str, int]]:
    """从单题目录构造 Evaluable；参数 entry 为一个 entry-* 目录。"""
    config = _read_json(entry / "config.json")
    metadata = config.get("evalMetadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"{entry.name} 缺少 evalMetadata")
    evaluations = _read_jsonl(entry / "evaluations.jsonl")
    if any(row.get("status") == "pending" for row in evaluations):
        raise ValueError(f"{entry.name} 含 pending 评测，不能汇总")
    p02 = [row for row in evaluations if str(row.get("evaluator", "")).endswith(_P02_NAME)]
    if len(p02) != 1:
        raise ValueError(f"{entry.name} 必须恰好包含一个 {_P02_NAME} 评分")
    score = p02[0].get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError(f"{entry.name} 的 {_P02_NAME} 评分无效")
    outputs = _read_jsonl(entry / "eval-output.jsonl")
    agent_counts = {name: 0 for name in _AGENT_NAMES}
    for row in evaluations:
        if row.get("evaluator") in agent_counts and row.get("score") == 1.0:
            agent_counts[row["evaluator"]] += 1
    # 聚合器不读取输入内容，但 Pixie 的 Evaluable 仍要求至少一个输入数据点。
    return Evaluable(
        eval_input=[{"name": "offline_result", "value": True}],
        eval_output=outputs,
        eval_metadata=metadata,
    ), agent_counts


def summarize_result_directory(result_dir: Path, output_path: Path) -> dict[str, Any]:
    """汇总一个 Pixie 结果目录并原子写出安全 JSON。

    参数 result_dir 为包含唯一 dataset-* 子目录的结果目录；参数 output_path 为调用方指定的摘要文件。
    """
    datasets = sorted(path for path in result_dir.glob("dataset-*") if path.is_dir())
    if len(datasets) != 1:
        raise ValueError("结果目录必须恰好包含一个 dataset-* 目录")
    entries = sorted(path for path in datasets[0].glob("entry-*") if path.is_dir())
    if len(entries) != 12:
        raise ValueError("结果目录必须恰好包含 12 个 entry-* 目录")
    evaluables: list[Evaluable] = []
    agent_totals = {name: 0 for name in _AGENT_NAMES}
    for entry in entries:
        evaluable, counts = _evaluable(entry)
        evaluables.append(evaluable)
        for name, count in counts.items():
            agent_totals[name] += count
    aggregate = aggregate_archive_v1_p02_results(evaluables)
    safe_entries = [{key: row.get(key) for key in _SAFE_ENTRY_KEYS if key in row} for row in aggregate["entries"]]
    summary = {key: value for key, value in aggregate.items() if key != "entries"}
    summary["entries"] = safe_entries
    summary["agent_evaluator_pass_counts"] = agent_totals
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return summary


def main() -> int:
    """运行命令行汇总；参数由 argparse 从调用方命令行读取。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path, help="Pixie test 结果目录")
    parser.add_argument("output", type=Path, help="安全汇总 JSON 输出文件")
    args = parser.parse_args()
    summarize_result_directory(args.result_dir, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
