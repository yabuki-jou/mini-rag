"""验证 FR-042 Archive Agent 评测入口、数据集和评测器契约。"""

import asyncio
import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import ArchiveDocumentType, ProjectStage


def test_archive_agent_args_accept_one_or_two_real_user_messages() -> None:
    """单轮和两轮会话应共用真实消息列表输入，拒绝空白或第三轮。"""
    from evals.archive.runnable import ArchiveAgentArgs

    assert ArchiveAgentArgs(messages=["列出正式档案"]).messages == ["列出正式档案"]
    assert ArchiveAgentArgs(messages=["合同金额是多少？", "签订日期呢？"]).messages == [
        "合同金额是多少？",
        "签订日期呢？",
    ]
    for invalid in ([], ["   "], ["一", "二", "三"]):
        with pytest.raises(ValidationError):
            ArchiveAgentArgs(messages=invalid)


def test_archive_agent_runnable_is_serial_and_never_replaces_model() -> None:
    """Runnable 应串行使用共享临时存储，并让生产 Runtime 自行取得真实模型。"""
    from evals.archive.runnable import ArchiveAgentRunnable

    runnable = ArchiveAgentRunnable.create()
    source = inspect.getsource(ArchiveAgentRunnable)

    assert runnable._semaphore._value == 1
    assert "ASGITransport" in source
    assert "model=" not in source
    assert "build_archive_runtime" not in source


def test_pixie_run_app_reexports_archive_agent_runnable() -> None:
    """Pixie 标准入口和仓库冻结路径必须指向同一个 Runnable。"""
    from evals.archive.runnable import ArchiveAgentArgs, ArchiveAgentRunnable
    from pixie_qa.run_app import AppArgs, AppRunnable

    assert AppArgs is ArchiveAgentArgs
    assert AppRunnable is ArchiveAgentRunnable


def test_reference_trace_inputs_cover_catalog_and_two_turn_follow_up() -> None:
    """参考 Trace 输入应覆盖目录筛选与省略主语的两轮事实追问。"""
    project_root = Path(__file__).resolve().parents[3]
    catalog = json.loads(
        (project_root / "pixie_qa" / "sample-input-catalog.json").read_text(
            encoding="utf-8"
        )
    )
    follow_up = json.loads(
        (project_root / "pixie_qa" / "sample-input-follow-up.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(catalog["messages"]) == 1
    assert "第1页" in catalog["messages"][0]
    assert "施工阶段" in catalog["messages"][0]
    assert len(follow_up["messages"]) == 2
    assert "责任单位" in follow_up["messages"][0]
    assert "该单位" in follow_up["messages"][1]
    serialized = json.dumps([catalog, follow_up], ensure_ascii=False)
    for forbidden in ("user_id", "project_id", "kb_id", "session_id"):
        assert forbidden not in serialized


def test_reference_world_reuses_safe_fictional_catalog_and_evidence() -> None:
    """参考 Trace 世界数据应可筛选且不含持久化标识或检索分数。"""
    from evals.archive.reference_world import (
        reference_catalog_result,
        reference_evidence_result,
    )

    page = reference_catalog_result(
        page=1,
        page_size=10,
        document_type=ArchiveDocumentType.CONTRACT,
        project_stage=ProjectStage.CONSTRUCTION,
    )
    evidence = reference_evidence_result()

    assert page.total == 1
    assert page.items[0]["filename"] == "PX-BETA-cover.txt"
    assert len(evidence) == 2
    serialized = json.dumps(
        {"catalog": page.as_dict(), "evidence": evidence},
        ensure_ascii=False,
    )
    assert "North Star Build Lab" in serialized
    assert "cycle 3" in serialized
    for forbidden in ("document_id", "chunk_id", "score", "user_id", "kb_id"):
        assert forbidden not in serialized


def test_archive_agent_runnable_scopes_reference_world_to_lifecycle() -> None:
    """Runnable 只应在自身生命周期内替换两个外部世界数据入口。"""
    from app.agents.tools import archive_tools
    from app.services.archive import catalog
    from evals.archive.reference_world import (
        reference_catalog_result,
        reference_evidence_result,
    )
    from evals.archive.runnable import ArchiveAgentRunnable

    original_catalog = catalog.list_agent_formal_archives
    original_evidence = archive_tools._retrieve_archive_agent_evidence

    async def exercise() -> None:
        runnable = ArchiveAgentRunnable.create()
        await runnable.setup()
        try:
            assert catalog.list_agent_formal_archives is reference_catalog_result
            assert archive_tools._retrieve_archive_agent_evidence is reference_evidence_result
        finally:
            await runnable.teardown()

    asyncio.run(exercise())

    assert catalog.list_agent_formal_archives is original_catalog
    assert archive_tools._retrieve_archive_agent_evidence is original_evidence


def test_archive_agent_dataset_passes_realism_and_coverage_gate() -> None:
    """最终集应覆盖六类用例、保持难度分布并完整注入两个外部输入。"""
    project_root = Path(__file__).resolve().parents[3]
    dataset = json.loads(
        (
            project_root
            / "pixie_qa"
            / "datasets"
            / "archive-agent-mvp.json"
        ).read_text(encoding="utf-8")
    )
    entries = dataset["entries"]
    categories = [entry["eval_metadata"]["category"] for entry in entries]
    difficulties = [entry["eval_metadata"]["difficulty"] for entry in entries]
    capabilities = {
        capability
        for entry in entries
        for capability in entry["eval_metadata"]["capabilities"]
    }

    assert dataset["runnable"] == "pixie_qa/run_app.py:AppRunnable"
    assert dataset["evaluators"] == [
        "pixie_qa/evaluators.py:archive_agent_trace_privacy"
    ]
    assert len(entries) == 17
    assert categories.count("GROUNDED") == 8
    assert categories.count("NO_EVIDENCE") == 2
    assert categories.count("ISOLATION") == 2
    assert categories.count("CATALOG") == 2
    assert categories.count("MULTI_TURN") == 2
    assert categories.count("CONTROLLED_FAILURE") == 1
    assert difficulties.count("routine") / len(entries) <= 0.6
    assert "challenging" in difficulties
    assert len(capabilities) >= 3

    distinct_worlds: set[str] = set()
    for entry in entries:
        assert 1 <= len(entry["input_data"]["messages"]) <= 2
        inputs = {item["name"]: item["value"] for item in entry["eval_input"]}
        assert "archive_agent_catalog_result" in inputs
        assert "archive_agent_evidence_retrieval" in inputs
        assert inputs["archive_agent_catalog_result"]
        assert inputs["archive_agent_evidence_retrieval"] is not None
        assert entry["eval_metadata"]["source_provenance"] in {
            "AV1_P02_CAPTURED_20260908",
            "FR042_REFERENCE_TRACE",
            "FR042_CONTROLLED_FAILURE",
        }
        distinct_worlds.add(
            json.dumps(entry["eval_input"], ensure_ascii=False, sort_keys=True)
        )
    assert len(distinct_worlds) > len(entries) / 2

    serialized = json.dumps(dataset, ensure_ascii=False)
    for forbidden in (
        "document_id",
        "chunk_id",
        "reranker_score",
        '"score"',
        "access_token",
        "refresh_token",
    ):
        assert forbidden not in serialized


def test_answered_archive_agent_worlds_bind_facts_to_expected_documents() -> None:
    """应回答事实必须与预期文件共享请求内文档引用，禁止跨文档坏金标。"""
    project_root = Path(__file__).resolve().parents[3]
    dataset = json.loads(
        (
            project_root
            / "pixie_qa"
            / "datasets"
            / "archive-agent-mvp.json"
        ).read_text(encoding="utf-8")
    )

    for entry in dataset["entries"]:
        metadata = entry["eval_metadata"]
        if not metadata.get("expected_answer_kinds") or any(
            kind != "ANSWERED" for kind in metadata["expected_answer_kinds"]
        ):
            continue
        expected_fragments = metadata["expected_answer_fragments"]
        expected_filenames = metadata.get("expected_document_filenames")
        expected_titles = metadata.get("expected_document_titles")
        assert isinstance(expected_filenames, list), metadata["case_id"]
        assert isinstance(expected_titles, list), metadata["case_id"]
        assert len(expected_filenames) == len(expected_fragments), metadata["case_id"]
        assert len(expected_titles) == len(expected_fragments), metadata["case_id"]
        evidence_worlds = [
            item["value"]
            for item in entry["eval_input"]
            if item["name"].startswith("archive_agent_evidence_retrieval")
        ]

        assert evidence_worlds, metadata["case_id"]
        for world in evidence_worlds:
            for turn_fragments, filename, title in zip(
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
                assert document_refs, (metadata["case_id"], filename, title)
                bound_items = [
                    item for item in world if item["document_ref"] in document_refs
                ]
                serialized_document = json.dumps(bound_items, ensure_ascii=False)
                for fragment in turn_fragments:
                    assert fragment in serialized_document, (
                        metadata["case_id"],
                        filename,
                        fragment,
                    )


def test_failure_observation_keeps_only_stable_status_and_audit_shape() -> None:
    """失败 Trace 不得保留请求、会话、Tool Call 标识或参数值。"""
    from evals.archive.runnable import _build_failure_observation

    observation = _build_failure_observation(
        status_code=503,
        error_payload={
            "error": {
                "code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
                "request_id": "should-not-leak",
            }
        },
        tool_calls=[
            {
                "id": "tool-log-id",
                "tool_call_id": "call-secret",
                "tool_name": "list_formal_archives",
                "status": "COMPLETED",
                "error_code": None,
                "arguments_summary": {"page": 1},
            }
        ],
        history=[
            {"role": "USER", "content": "should-not-leak"},
        ],
    )

    assert observation == {
        "http_status": 503,
        "error_code": "ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE",
        "tool_audit": [
            {
                "tool_name": "list_formal_archives",
                "status": "COMPLETED",
                "error_code": None,
            }
        ],
        "history_message_count": 1,
        "history_roles": ["USER"],
    }
