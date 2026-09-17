"""验证智慧档案评测资产已迁入 evals.archive。"""

import importlib
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_archive_eval_modules_are_importable() -> None:
    """Runnable 和评审器应从新的评测业务域导入。"""
    assert importlib.import_module("evals.archive.runnable")
    assert importlib.import_module("evals.archive.evaluators")


def test_sources_do_not_reference_retired_archive_eval_package() -> None:
    """运行时代码、测试和脚本不能继续引用旧 Pixie 评测包。"""
    offenders = []
    for source_root in ("app", "tests", "scripts"):
        for path in (ROOT / source_root).rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "pixie_qa.archive_v1_p14" in text or "pixie_qa/archive_v1_p14" in text:
                offenders.append(str(path))
    assert offenders == []


def test_policy_eval_package_is_retired() -> None:
    """制度 Agent 评测包必须随运行能力一起下线。"""
    package = ROOT / "evals" / "policy_agent"
    assert not any(
        path.is_file()
        and "__pycache__" not in path.parts
        and (
            path.suffix in {".py", ".md"}
            or path.name == "policy-agent-golden.json"
            or path.name.startswith("input-")
        )
        for path in package.rglob("*")
    )
