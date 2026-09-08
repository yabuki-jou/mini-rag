"""执行 P14-D2 的本地 Reranker 延迟与内存前置验证。"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import math
import os
import platform
import time
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Any

import torch
from sentence_transformers import CrossEncoder


Pair = tuple[str, str]


@dataclass(frozen=True)
class ProbeVariant:
    """表示一次固定的推理参数组合。"""

    name: str
    max_length: int | None
    batch_size: int


VARIANTS = {
    "default": ProbeVariant("default", None, 32),
    "max_length_256": ProbeVariant("max_length_256", 256, 32),
    "max_length_256_batch_8": ProbeVariant("max_length_256_batch_8", 256, 8),
    "max_length_256_batch_16": ProbeVariant("max_length_256_batch_16", 256, 16),
}


FIXED_QUERIES = (
    "星河项目施工总承包合同的签订日期是多少？",
    "星河项目设计说明的编制单位是什么？",
    "星河项目施工方案适用哪个项目阶段？",
    "第一次协调会议对设计变更作出了什么决定？",
    "星河项目竣工验收报告的结论是什么？",
    "云港仓储中心设备采购合同的供货单位是什么？",
    "云港仓储中心设计技术说明的版本号是什么？",
    "云港仓储中心消防验收报告的结论是什么？",
    "星河施工总承包合同约定的付款比例是多少？",
    "云港仓储中心的实际开工日期是什么？",
    "云港仓储中心消防验收报告的结论是什么？",
    "星河项目设计说明的编制单位是什么？",
)


def percentile(values: list[float], quantile: float) -> float:
    """使用线性插值计算固定样本的分位数。"""
    if not values:
        raise ValueError("分位数计算至少需要一个样本。")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("分位数必须位于 0 到 1 之间。")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def build_prediction_kwargs(max_length: int | None) -> dict[str, Any]:
    """构造 sentence-transformers 5.x 的单次序列长度覆盖参数。"""
    if max_length is None:
        return {}
    if max_length <= 0:
        raise ValueError("max_length 必须为正整数。")
    return {
        "processing_kwargs": {
            "text": {"max_length": max_length, "truncation": True},
        }
    }


def build_fixed_pairs(
    *, query_count: int = 12, candidate_count: int = 30
) -> list[list[Pair]]:
    """生成不写入外部存储的固定长度候选 pairs。"""
    if query_count != len(FIXED_QUERIES):
        raise ValueError("D2 延迟探针必须使用固定 12 道问题。")
    if candidate_count != 30:
        raise ValueError("D2 延迟探针必须使用固定 30 个候选。")

    pairs: list[list[Pair]] = []
    for query_index, query in enumerate(FIXED_QUERIES, start=1):
        question_pairs: list[Pair] = []
        for candidate_index in range(1, candidate_count + 1):
            # 延迟探针只关心输入长度和批量规模，不将这些片段用于质量判定。
            content = (
                f"项目档案延迟测试片段 {query_index:02d}-{candidate_index:02d}。"
                "本片段包含工程资料中的日期、单位、版本、阶段、会议决定、验收结论、"
                "供货信息和其他可检索字段，用于模拟 Final Chunk 的中文文本长度。"
                "该文本仅用于本地 CrossEncoder 推理耗时测量，不写入 PostgreSQL 或 Chroma。"
                * 3
            )
            question_pairs.append((query, content))
        pairs.append(question_pairs)
    return pairs


def _process_memory() -> tuple[int, int]:
    """返回当前进程 RSS 与系统记录的峰值工作集（字节）。"""
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        process = kernel32.GetCurrentProcess()
        ok = get_process_memory_info(
            process, ctypes.byref(counters), ctypes.sizeof(counters)
        )
        if ok:
            return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)

    return 0, 0


def _system_memory() -> dict[str, int | None]:
    """读取本机总内存和可用内存，失败时返回空值。"""
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {
                "total_bytes": int(status.ullTotalPhys),
                "available_bytes": int(status.ullAvailPhys),
            }
    return {"total_bytes": None, "available_bytes": None}


def _load_model(model_path: Path) -> tuple[CrossEncoder, float, int, int]:
    """从已下载的本地目录加载 CPU CrossEncoder。"""
    if not model_path.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{model_path}")
    started = time.perf_counter()
    model = CrossEncoder(
        str(model_path),
        device="cpu",
        local_files_only=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    rss, peak_rss = _process_memory()
    return model, elapsed_ms, rss, peak_rss


def _predict(
    model: CrossEncoder,
    pairs: list[Pair],
    variant: ProbeVariant,
) -> Any:
    """按指定变体执行一次批量预测。"""
    return model.predict(
        pairs,
        batch_size=variant.batch_size,
        show_progress_bar=False,
        **build_prediction_kwargs(variant.max_length),
    )


def _measure_variant(
    model: CrossEncoder,
    pairs_by_query: list[list[Pair]],
    variant: ProbeVariant,
    *,
    warmup_runs: int,
) -> dict[str, Any]:
    """对固定 12 道问题记录单次批量推理延迟。"""
    for _ in range(warmup_runs):
        _predict(model, pairs_by_query[0], variant)

    latencies_ms: list[float] = []
    peak_rss = _process_memory()[1]
    for pairs in pairs_by_query:
        started = time.perf_counter()
        scores = _predict(model, pairs, variant)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if len(scores) != len(pairs):
            raise RuntimeError("Reranker 返回分数数量与候选数量不一致。")
        if not all(math.isfinite(float(score)) for score in scores):
            raise RuntimeError("Reranker 返回了非有限分数。")
        latencies_ms.append(elapsed_ms)
        peak_rss = max(peak_rss, _process_memory()[1])

    return {
        "variant": variant.name,
        "max_length": variant.max_length,
        "batch_size": variant.batch_size,
        "candidate_count": len(pairs_by_query[0]),
        "query_count": len(pairs_by_query),
        "p50_ms": round(percentile(latencies_ms, 0.50), 3),
        "p95_ms": round(percentile(latencies_ms, 0.95), 3),
        "max_ms": round(max(latencies_ms), 3),
        "mean_ms": round(sum(latencies_ms) / len(latencies_ms), 3),
        "peak_rss_bytes": peak_rss,
        "samples_ms": [round(value, 3) for value in latencies_ms],
    }


def _parse_model_spec(value: str) -> tuple[str, Path]:
    """解析 NAME=PATH 参数，避免在输出中暴露本地路径。"""
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("模型参数必须使用 NAME=PATH 格式。")
    return name, Path(path)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="本地模型，允许重复传入多个；输出只保留 NAME。",
    )
    parser.add_argument(
        "--variant",
        action="append",
        choices=tuple(VARIANTS),
        help="要运行的变体；默认运行全部变体。",
    )
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path, help="可选的 JSON 报告路径。")
    return parser


def main(argv: list[str] | None = None) -> int:
    """运行 D2 探针并输出脱敏 JSON 结果。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.warmup < 0:
        parser.error("--warmup 不能为负数。")

    model_specs = [_parse_model_spec(value) for value in args.model]
    variants = [VARIANTS[name] for name in (args.variant or list(VARIANTS))]
    pairs_by_query = build_fixed_pairs()
    memory = _system_memory()
    report: dict[str, Any] = {
        "environment": {
            "os": platform.platform(),
            "cpu_logical_count": os.cpu_count(),
            "torch_version": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "memory": memory,
        },
        "models": [],
    }

    for model_name, model_path in model_specs:
        model, load_ms, rss_after_load, peak_rss = _load_model(model_path)
        try:
            variant_results = [
                _measure_variant(
                    model,
                    pairs_by_query,
                    variant,
                    warmup_runs=args.warmup,
                )
                for variant in variants
            ]
            report["models"].append(
                {
                    "model": model_name,
                    "load_ms": round(load_ms, 3),
                    "rss_after_load_bytes": rss_after_load,
                    "peak_rss_bytes": max(
                        [peak_rss]
                        + [int(result["peak_rss_bytes"]) for result in variant_results]
                    ),
                    "variants": variant_results,
                }
            )
        finally:
            del model
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
