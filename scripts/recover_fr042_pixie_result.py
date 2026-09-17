"""从 Pixie 已落盘 Trace 恢复被 Windows 终端编码中断的评测结果。"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

from pixie import Evaluable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "pixie_qa" / "datasets" / "archive-agent-mvp.json"
sys.path.insert(0, str(PROJECT_ROOT))


def _resolved_evaluators(
    dataset_evaluators: list[str],
    entry_evaluators: list[str],
) -> list[str]:
    """把条目中的继承占位符展开为实际评估器列表。"""
    resolved: list[str] = []
    for evaluator in entry_evaluators:
        if evaluator == "...":
            resolved.extend(dataset_evaluators)
        else:
            resolved.append(evaluator)
    return resolved


def _stable_named_rows(trace_rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    """把 Trace 中的模型跨度和非输入 Wrap 转为 Pixie 命名输出。"""
    counters: defaultdict[str, int] = defaultdict(int)
    rows: list[dict[str, object]] = []
    for trace_row in trace_rows:
        row_type = trace_row.get("type")
        if row_type == "llm_span_trace":
            base_name = f"llm_span_{trace_row.get('request_model', 'unknown')}"
            value = {key: value for key, value in trace_row.items() if key != "type"}
        elif row_type == "wrap" and trace_row.get("purpose") != "input":
            base_name = str(trace_row["name"])
            value = trace_row.get("data")
        else:
            continue
        counters[base_name] += 1
        suffix = "" if counters[base_name] == 1 else f"__{counters[base_name]}"
        rows.append({"name": f"{base_name}{suffix}", "value": value})
    return rows


def _load_evaluator(reference: str) -> object:
    """按数据集中的文件引用加载仓库评估器。"""
    module_path, attribute = reference.split(":", maxsplit=1)
    module_name = module_path.removesuffix(".py").replace("/", ".").replace("\\", ".")
    return getattr(import_module(module_name), attribute)


def _evaluation_row(reference: str, evaluator: object, evaluable: Evaluable) -> dict[str, object]:
    """执行机械评估器，语义评估器保留为待人工复核。"""
    if hasattr(evaluator, "_criteria"):
        return {
            "evaluator": getattr(evaluator, "_name"),
            "status": "pending",
            "criteria": getattr(evaluator, "_criteria"),
        }
    result = evaluator(evaluable)
    return {
        "evaluator": reference.split(":", maxsplit=1)[1],
        "score": result.score,
        "reasoning": result.reasoning,
    }


def _write_json(path: Path, value: object) -> None:
    """用稳定 UTF-8 和缩进写 JSON 文件。"""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    """用稳定 UTF-8 写 JSONL 文件。"""
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def recover(run_id: str) -> None:
    """恢复指定运行目录，并拒绝缺 Trace 或数量不一致的输入。"""
    result_root = PROJECT_ROOT / "pixie_qa" / "results" / run_id
    dataset_root = result_root / "dataset-0"
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    entries = dataset["entries"]
    trace_paths = sorted(
        dataset_root.glob("entry-*/trace.jsonl"),
        key=lambda path: int(path.parent.name.removeprefix("entry-")),
    )
    if len(trace_paths) != len(entries):
        raise ValueError(
            f"Trace 数量 {len(trace_paths)} 与数据集条目 {len(entries)} 不一致。"
        )

    started_at: str | None = None
    ended_at: str | None = None
    dataset_evaluators = list(dataset.get("evaluators", []))
    for index, (entry, trace_path) in enumerate(zip(entries, trace_paths, strict=True)):
        entry_dir = dataset_root / f"entry-{index}"
        trace_rows = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for trace_row in trace_rows:
            if trace_row.get("type") != "llm_span_trace":
                continue
            current_start = trace_row.get("started_at")
            current_end = trace_row.get("ended_at")
            if isinstance(current_start, str):
                started_at = min(started_at, current_start) if started_at else current_start
            if isinstance(current_end, str):
                ended_at = max(ended_at, current_end) if ended_at else current_end

        eval_input = [
            {"name": "input_data", "value": entry["input_data"]},
            *entry["eval_input"],
        ]
        eval_output = _stable_named_rows(trace_rows)
        evaluable = Evaluable(
            eval_input=eval_input,
            eval_output=eval_output,
            eval_metadata=entry.get("eval_metadata"),
        )
        evaluator_refs = _resolved_evaluators(
            dataset_evaluators,
            list(entry.get("evaluators", [])),
        )
        evaluations = [
            _evaluation_row(reference, _load_evaluator(reference), evaluable)
            for reference in evaluator_refs
        ]
        _write_json(
            entry_dir / "config.json",
            {
                "evaluators": evaluator_refs,
                "description": entry.get("description"),
                "expectation": entry.get("expectation"),
                "evalMetadata": entry.get("eval_metadata"),
            },
        )
        _write_jsonl(entry_dir / "eval-input.jsonl", eval_input)
        _write_jsonl(entry_dir / "eval-output.jsonl", eval_output)
        _write_jsonl(entry_dir / "evaluations.jsonl", evaluations)

    _write_json(
        dataset_root / "metadata.json",
        {
            "dataset": dataset["name"],
            "datasetPath": str(DATASET_PATH.relative_to(PROJECT_ROOT)),
            "runnable": dataset["runnable"],
        },
    )
    _write_json(
        result_root / "meta.json",
        {
            "testId": run_id,
            "command": "pixie test pixie_qa/datasets/archive-agent-mvp.json -v --no-open",
            "startedAt": started_at or datetime.now(timezone.utc).isoformat(),
            "endedAt": ended_at or datetime.now(timezone.utc).isoformat(),
            "recoveryNote": "真实调用完成后，Pixie 汇总输出因 Windows GBK 编码失败；本目录由已落盘 Trace 恢复。",
        },
    )
    print(f"recovered {len(entries)} entries in {result_root}")


def main() -> None:
    """读取命令行中的唯一运行 ID。"""
    if len(sys.argv) != 2:
        raise SystemExit("usage: recover_fr042_pixie_result.py <run_id>")
    recover(sys.argv[1])


if __name__ == "__main__":
    main()
