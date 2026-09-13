"""验证制度 Agent Golden Dataset 的六类场景和评测路径约束。"""

import importlib
import json
from pathlib import Path


DATASET_PATH = (
    Path(__file__).parents[3]
    / "evals"
    / "policy_agent"
    / "datasets"
    / "policy-agent-golden.json"
)


def _load_dataset() -> dict:
    """读取脱敏的制度 Agent 固定集。"""
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_dataset_covers_six_policy_agent_scenarios() -> None:
    """固定集必须覆盖六类制度 Agent 行为。"""
    dataset = _load_dataset()
    entries = dataset["entries"]
    assert {entry["category"] for entry in entries} == {
        "single_evidence",
        "multi_evidence_exception",
        "no_evidence",
        "clarification",
        "non_policy",
        "scope_injection",
    }
    assert len(entries) == 6
    assert sum(entry["difficulty"] == "routine" for entry in entries) <= 3
    assert any(entry["difficulty"] == "challenging" for entry in entries)


def test_every_entry_declares_policy_retrieval_input_and_evaluator() -> None:
    """每条样本都必须声明唯一制度检索输入和可导入评审器。"""
    dataset = _load_dataset()
    assert dataset["runnable"] == "evals/policy_agent/runnable.py:PolicyAgentRunnable"
    assert dataset["evaluators"] == [
        "evals/policy_agent/evaluators.py:policy_agent_contract"
    ]
    for entry in dataset["entries"]:
        assert [item["name"] for item in entry["eval_input"]] == [
            "policy_retrieval_result"
        ]
        assert entry["evaluators"][0] == "..."
        if entry["category"] in {"single_evidence", "multi_evidence_exception"}:
            assert (
                "evals/policy_agent/evaluators.py:policy_evidence_faithfulness"
                in entry["evaluators"]
            )
        else:
            assert (
                "evals/policy_agent/evaluators.py:policy_clarification_refusal_routing_safety"
                in entry["evaluators"]
            )


def test_dataset_uses_only_fixed_deidentified_references() -> None:
    """样本中的文档和 Chunk 引用必须来自固定虚构集合。"""
    dataset = _load_dataset()
    allowed_documents = {
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    }
    allowed_chunks = {
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    }
    for entry in dataset["entries"]:
        for chunk in entry["eval_input"][0]["value"]:
            assert chunk.get("document_id") in allowed_documents
            assert chunk.get("chunk_id") in allowed_chunks


def test_dataset_contains_no_retired_assets_or_runtime_names() -> None:
    """固定集不能混入旧工具、旧路径或历史基础设施名称。"""
    text = DATASET_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "leave_",
        "leave service",
        "milvus",
        "sqlite",
        "pixie_qa/",
        "app.services.",
    )
    assert not [term for term in forbidden if term in text]


def test_dataset_declared_paths_are_importable() -> None:
    """固定集声明的 Runnable 和评审器路径必须可导入。"""
    dataset = _load_dataset()
    paths = [dataset["runnable"], *dataset["evaluators"]]
    for entry in dataset["entries"]:
        paths.extend(path for path in entry["evaluators"] if path != "...")
    for path in set(paths):
        module_name, attribute_name = path.split(":", 1)
        module = importlib.import_module(module_name.replace("/", ".").removesuffix(".py"))
        assert hasattr(module, attribute_name)
