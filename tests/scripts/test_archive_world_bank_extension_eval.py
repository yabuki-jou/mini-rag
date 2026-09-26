"""覆盖 World Bank 扩展来源评测候选准备器的隔离行为。"""

from __future__ import annotations

import json
from dataclasses import replace
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Mapping
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.archive_retrieval import ArchiveRetrievalResponse
from scripts import archive_world_bank_extension_eval as extension


def test_project_root_is_added_to_sys_path_for_direct_script_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_directory = str(extension.PROJECT_ROOT / "scripts")
    monkeypatch.setattr(sys, "path", [scripts_directory])

    extension._ensure_project_root_on_sys_path()

    assert sys.path[0] == str(extension.PROJECT_ROOT)
    assert sys.path.count(str(extension.PROJECT_ROOT)) == 1


def test_run_paths_are_unique_and_inside_git_ignored_pytest_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    first = extension.build_run_paths("1" * 16)
    second = extension.build_run_paths("2" * 16)

    assert first.root != second.root
    assert first.root == tmp_path / "tests" / "pytest_docs" / "public_projects" / "world_bank_belarus_m6" / "acceptance" / ("1" * 16)
    assert first.runtime_root == tmp_path / "tests" / "pytest_docs" / "acceptance-runs" / ("1" * 16)
    assert first.file_storage == first.runtime_root / "files"
    assert first.checkpoint == first.runtime_root / "checkpoints.sqlite"
    assert first.root == first.result_dir
    assert first.log_file == Path(tempfile.gettempdir()) / f"mini-rag-wb-eval-{('1' * 16)}" / "logs" / "app.log"
    assert first.root not in first.log_file.parents


def test_target_names_follow_existing_retrieval_runner_contract_and_env_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)
    paths = extension.build_run_paths("a" * 16)

    targets = extension.build_targets("a" * 16, paths, chroma_tenant="tenant-from-settings")

    assert targets.schema == "fr042_wb_" + "a" * 16
    assert targets.chroma_database == "fr042_wb_db_" + "a" * 16
    assert targets.chroma_collection == "fr042_wb_collection_" + "a" * 16
    assert targets.chroma_tenant == "tenant-from-settings"


def test_isolated_runtime_rebinds_persistence_acceptance_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """验证隔离配置会重绑已预导入的持久化验收模块。

    Args:
        monkeypatch: 用于替换隔离依赖并在测试结束后恢复原状态的 pytest 工具。
        tmp_path: pytest 提供的临时路径，用作隔离运行路径。
    """
    import app.core.config as config
    import app.db as db
    import sqlalchemy

    class OldEngine:
        def dispose(self) -> None:
            """提供可替换的旧 Engine 清理接口，避免测试触及真实连接。"""
            return None

    class SettingsStub(SimpleNamespace):
        def model_copy(self, *, update: Mapping[str, Any]) -> SimpleNamespace:
            """复制设置替身并应用隔离运行所需的字段更新。

            Args:
                update: 需要覆盖或补入设置副本的字段映射。
            """
            values = vars(self).copy()
            values.update(update)
            return SimpleNamespace(**values)

    old_settings = SettingsStub(
        database_url="postgresql+psycopg://unused:unused@localhost/unused"
    )
    old_engine = OldEngine()
    new_engine = object()
    persistence_module = ModuleType("scripts.archive_lushan_persistence_acceptance")
    persistence_module.settings = old_settings
    persistence_module.engine = old_engine
    app_main = ModuleType("app.main")
    app_instance = object()
    app_main.app = app_instance

    monkeypatch.setattr(db, "engine", old_engine)
    monkeypatch.setattr(db, "database_url", old_settings.database_url)
    monkeypatch.setattr(config, "settings", old_settings)
    monkeypatch.setitem(
        sys.modules, "scripts.archive_lushan_persistence_acceptance", persistence_module
    )
    monkeypatch.setitem(sys.modules, "app.main", app_main)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *_args, **_kwargs: new_engine)

    paths = extension.RunPaths(
        run_id="a" * 16,
        root=tmp_path,
        runtime_root=tmp_path / "runtime",
        result_dir=tmp_path,
        candidate_file=tmp_path / "candidates.json",
        summary_file=tmp_path / "summary.json",
        file_storage=tmp_path / "files",
        checkpoint=tmp_path / "checkpoints.sqlite",
        log_root=tmp_path / "logs",
        log_file=tmp_path / "logs" / "app.log",
    )
    targets = extension.Targets(
        schema="fr042_wb_" + "a" * 16,
        chroma_tenant="tenant",
        chroma_database="database",
        chroma_collection="collection",
        paths=paths,
    )

    for name, module in tuple(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "settings"):
            monkeypatch.setattr(module, "settings", module.settings)
    for module in (
        persistence_module,
        sys.modules.get("scripts.archive_lushan_persistence_acceptance"),
        sys.modules.get("scripts.archive_world_bank_retrieval_acceptance"),
    ):
        if module is not None:
            for attribute in ("settings", "engine"):
                if hasattr(module, attribute):
                    monkeypatch.setattr(module, attribute, getattr(module, attribute))

    app = extension._configure_isolated_runtime(old_settings, targets)

    assert app is app_instance
    assert persistence_module.settings is not old_settings
    assert persistence_module.settings is config.settings
    assert persistence_module.engine is db.engine
    assert persistence_module.engine is new_engine


def test_preflight_rejects_any_preexisting_local_run_target(tmp_path: Path) -> None:
    paths = extension.RunPaths(
        run_id="b" * 16,
        root=tmp_path / "result",
        runtime_root=tmp_path / "runtime",
        result_dir=tmp_path / "result",
        candidate_file=tmp_path / "result" / "candidates.json",
        summary_file=tmp_path / "result" / "summary.json",
        file_storage=tmp_path / "runtime" / "files",
        checkpoint=tmp_path / "runtime" / "checkpoints.sqlite",
        log_root=tmp_path / "log",
        log_file=tmp_path / "log" / "logs" / "app.log",
    )
    paths.log_root.mkdir()

    with pytest.raises(extension.PreparationError) as raised:
        extension.validate_paths_absent(paths)

    assert raised.value.safe_code == "TARGET_EXISTS"


def test_rejects_output_path_when_gitignore_does_not_confirm_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: False)

    with pytest.raises(extension.PreparationError) as raised:
        extension.build_run_paths("3" * 16)

    assert raised.value.safe_code == "OUTPUT_PATH_NOT_IGNORED"


def test_candidate_capture_mapping_uses_slash_keys_and_keeps_only_actual_top8() -> None:
    capture = extension.CandidateCaptureMap()
    payload = [{"filename": "a.pdf", "rank": rank} for rank in range(1, 11)]

    capture["WB-REV-LOAN-01/original"] = payload

    assert list(capture) == ["WB-REV-LOAN-01/original"]
    assert [item["rank"] for item in capture["WB-REV-LOAN-01/original"]] == list(range(1, 9))
    assert all("supports_answer" not in item for item in capture["WB-REV-LOAN-01/original"])


def test_build_pixie_dataset_skips_incomplete_or_unsupported_positive_groups() -> None:
    capture = {
        "LUSHAN-02/original": [
            {"filename": "2022_restructuring_paper.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 8, "location_end": 8, "excerpt": "December 31, 2023"}
        ],
        "WB-REV-LOAN-01/original": [
            {"filename": "snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 7, "location_end": 7, "excerpt": "US$250 million"}
        ],
    }

    dataset, failures = extension.build_pixie_dataset(
        capture, supported_groups={"LUSHAN-02/original": True}
    )

    assert dataset["entries"] == []
    assert {item["case_id"] for item in failures} == {
        "LUSHAN-02", "LUSHAN-03", "WB-REV-LOAN-01", "WB-CONTEXT-NONLOAN-01",
        "WB-ENTITY-NEG-01", "WB-UNSUPPORTED-DRAW-01",
    }
    assert all(set(item) == {"case_id", "safe_error_code"} for item in failures)


def test_build_pixie_dataset_uses_evaluator_contract_and_real_candidate_excerpts() -> None:
    capture = {
        "LUSHAN-02/original": [
            {"filename": "2022_restructuring_paper.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 8, "location_end": 8, "excerpt": "December 31, 2023"}
        ],
        "LUSHAN-03/original": [
            {"filename": "2024_completion_report_review.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 20, "location_end": 20, "excerpt": "Outcome Satisfactory"}
        ],
        "WB-REV-LOAN-01/original": [
            {"filename": "belarus-snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 7, "location_end": 7, "excerpt": "US$250 million"}
        ],
        "WB-REV-LOAN-01/supplementary": [
            {"filename": "belarus-snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 16, "location_end": 16, "excerpt": "IBRD Loan 84590"}
        ],
        "WB-CONTEXT-NONLOAN-01/original": [
            {"filename": "belarus-snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 16, "location_end": 16, "excerpt": "1,700 trucks daily"}
        ],
        "WB-ENTITY-NEG-01/original": [
            {"filename": "belarus-snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 16, "location_end": 16, "excerpt": "IFC advisory work"}
        ],
        "WB-UNSUPPORTED-DRAW-01/original": [
            {"filename": "belarus-snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 16, "location_end": 16, "excerpt": "Disbursed 0 as of 2015-03-05"}
        ],
    }

    supported = {
        "LUSHAN-02/original": True,
        "LUSHAN-03/original": True,
        "WB-REV-LOAN-01/original": True,
        "WB-REV-LOAN-01/supplementary": True,
        "WB-CONTEXT-NONLOAN-01/original": True,
        "WB-ENTITY-NEG-01/original": False,
        "WB-UNSUPPORTED-DRAW-01/original": False,
    }
    capture = {
        key: [
            {
                **items[0],
                "chunk_id": f"test-chunk-{group_index}-{rank}",
                "document_id": str(UUID("00000000-0000-4000-8000-000000000001")),
                "location_type": "PDF_PAGE",
                "score": 1.0 / rank,
                "reranker_score": 1.0 / (rank + 1),
            }
            for rank in range(1, 9)
        ]
        for group_index, (key, items) in enumerate(capture.items(), start=1)
    }
    dataset, failures = extension.build_pixie_dataset(capture, supported_groups=supported)

    assert failures == []
    assert dataset["runnable"] == "evals/archive/world_bank_runnable.py:WorldBankQuestionRunnable"
    assert len(dataset["entries"]) == 21
    assert {entry["eval_metadata"]["case_id"] for entry in dataset["entries"]} == set(
        extension.REQUIRED_CASE_IDS
    )
    for entry in dataset["entries"]:
        assert entry["input_data"] == {"question": entry["input_data"]["question"]}
        retrieval = [item for item in entry["eval_input"] if item["name"] == "archive_question_retrieval"]
        assert len(retrieval) == 1
        payload = retrieval[0]["value"]
        try:
            response = ArchiveRetrievalResponse.model_validate(payload)
        except ValidationError as exc:
            error_paths = [
                {
                    "path": ".".join(str(part) for part in error["loc"]),
                    "type": error["type"],
                }
                for error in exc.errors(include_input=False)
            ]
            pytest.fail(f"检索注入负载不符合响应契约：{error_paths}")
        assert response.returned_count == len(response.items)
        assert response.requested_top_k == extension.EXPECTED_CANDIDATE_COUNT
        assert len(response.items) == extension.EXPECTED_CANDIDATE_COUNT
        assert set(response.items[0].model_dump()) == {
            "chunk_id",
            "document_id",
            "filename",
            "location_type",
            "location_start",
            "location_end",
            "excerpt",
            "score",
            "reranker_score",
        }
        serialized = json.dumps(entry, ensure_ascii=False)
        assert "excerpt" in serialized
        assert "archive_question_response" not in serialized
    loan = next(entry for entry in dataset["entries"] if entry["eval_metadata"]["case_id"] == "WB-REV-LOAN-01")
    assert loan["expectation"]["extension_sources"]
    assert loan["expectation"]["extension_sources"] == [{"filename": "belarus-snapshot.pdf", "page": 16}]
    assert loan["expectation"]["answer_fragments"] == []
    assert loan["expectation"]["answer_alternatives"] == [
        ["US$250 million", "250 million US Dollars", "250百万美元", "2.5亿美元"]
    ]
    assert loan["eval_metadata"]["expected_answer_status"] == "ANSWERED"
    assert loan["eval_metadata"]["expected_answer_fragments"] == []
    lushan = next(entry for entry in dataset["entries"] if entry["eval_metadata"]["case_id"] == "LUSHAN-02")
    assert lushan["expectation"]["frozen_target"] == {
        "filename": "2022_restructuring_paper.pdf", "page": 8
    }
    groups = [
        (entry["eval_metadata"]["case_id"], entry["eval_metadata"]["ranking_method"])
        for entry in dataset["entries"]
    ]
    assert groups == [
        (case_id, method)
        for sample in range(1, 4)
        for case_id, method in extension.CONDITION_GROUPS
    ]
    for case_id, method in extension.CONDITION_GROUPS:
        selected = [entry for entry in dataset["entries"]
                    if entry["eval_metadata"]["case_id"] == case_id
                    and entry["eval_metadata"]["ranking_method"] == method]
        assert [entry["eval_metadata"]["sample"] for entry in selected] == [1, 2, 3]
    assert len(dataset["entries"]) == 21


def test_supported_groups_come_from_top8_acceptance_diagnostics_only() -> None:
    summary = {"cases": [{
        "case_id": "WB-REV-LOAN-01",
        "rankings": {
            "original": [{"source_page": 7, "supports_answer": True}],
            "supplementary": [{"source_page": 16, "supports_answer": True}],
        },
    }]}

    supported = extension.supported_groups_from_summary(summary)

    assert supported == {
        "WB-REV-LOAN-01/original": False,
        "WB-REV-LOAN-01/supplementary": True,
    }


class FakeResources:
    """记录资源协作器的调用顺序，不访问真实持久层。"""

    def __init__(self, *, fail_at: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.artifacts: list[Mapping[str, Any]] = []
        self.cleanup_result = True

    def call(self, name: str, result: object = None) -> object:
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError("sensitive failure text")
        return result


def _complete_candidate_capture(
    capture: extension.CandidateCaptureMap,
) -> None:
    """为隔离运行夹具补齐与真实响应 DTO 一致的候选字段。"""
    document_id = str(UUID("00000000-0000-4000-8000-000000000001"))
    for group_key, candidates in list(capture.items()):
        capture[group_key] = [
            {
                **candidate,
                "chunk_id": f"test-{group_key}-{rank}",
                "document_id": document_id,
                "location_type": "PDF_PAGE",
                "score": 1.0 / (rank + 1),
                "reranker_score": 1.0 / (rank + 2),
            }
            for rank, candidate in enumerate(candidates)
        ]


def _fake_retrieval(resources: FakeResources, capture: extension.CandidateCaptureMap) -> Mapping[str, Any]:
    resources.call("run_retrieval")
    cases = []
    for case_id, method in extension.CONDITION_GROUPS:
        capture[f"{case_id}/{method}"] = [
            {"filename": "evidence.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 16 if case_id == "WB-REV-LOAN-01" else 1,
             "location_end": 16 if case_id == "WB-REV-LOAN-01" else 1,
             "excerpt": f"candidate {rank}"}
            for rank in range(8)
        ]
        cases.append({
            "case_id": case_id,
            "query": "private-query-sentinel",
            "excerpt": "private-excerpt-sentinel",
            "rankings": {method: [
                {"source_page": 16 if case_id == "WB-REV-LOAN-01" else 1,
                 "supports_answer": case_id not in {"WB-ENTITY-NEG-01", "WB-UNSUPPORTED-DRAW-01"},
                 "exact": False, "public": True}
                for _ in range(8)
            ]},
            "candidate_union_count": 30,
            "candidate_intersection_count": 5,
            "target_page_ranks": {method: {"16": 7}},
            "latency_ms": {"production_service": {"elapsed": 1.25}},
        })
    cases.append({
        "case_id": "WB-UNSUPPORTED-DRAW-GATED-01",
        "rankings": {"original": []},
    })
    _complete_candidate_capture(capture)
    return {
        "evaluation": "retrieval-only",
        "model_calls": 0,
        "query": "private-top-level-query-sentinel",
        "cases": cases,
    }


def _dependencies(resources: FakeResources) -> extension.PreparationDependencies:
    return extension.PreparationDependencies(
        load_settings=lambda: resources.call("load_settings", SimpleNamespace(chroma_tenant="tenant-from-settings")),
        validate_configuration=lambda settings: resources.call("validate_configuration", True),
        verify_sources=lambda: resources.call("verify_sources", {"verified": True}),
        inspect_targets=lambda targets: resources.call("inspect_targets", {"clear": True}),
        create_schema=lambda target: resources.call("create_schema"),
        verify_search_path=lambda target: resources.call("verify_search_path", True),
        create_chroma_database=lambda target: resources.call("create_chroma_database"),
        create_run_directory=lambda path: resources.call("create_run_directory"),
        configure_isolated_runtime=lambda settings, targets: resources.call("configure_runtime"),
        start_api=lambda settings: resources.call("start_api", SimpleNamespace(port=8765)),
        wait_api_ready=lambda api: resources.call("wait_api_ready", True),
        run_retrieval=lambda base_url, capture: _fake_retrieval(resources, capture),
        stop_api=lambda api: resources.call("stop_api"),
        dispose_runtime=lambda: resources.call("dispose_runtime"),
        shutdown_logging=lambda: resources.call("shutdown_logging"),
        cleanup_retrieval=lambda: resources.call("cleanup_retrieval"),
        drop_schema=lambda target: resources.call("drop_schema"),
        delete_chroma_database=lambda target: resources.call("delete_chroma_database"),
        delete_run_directory=lambda path: resources.call("delete_run_directory"),
        delete_log_directory=lambda path: resources.call("delete_log_directory"),
        verify_cleanup=lambda targets: resources.call("verify_cleanup", resources.cleanup_result),
        write_artifacts=lambda targets, artifacts: (
            resources.artifacts.append(artifacts), resources.call("write_artifacts")
        ),
        emit=lambda status: resources.call("emit"),
    )


def test_chroma_cleanup_is_idempotent_when_database_is_already_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chromadb
    from chromadb.errors import NotFoundError

    class AdminClient:
        def get_database(self, *, name: str, tenant: str) -> None:
            raise NotFoundError("database not found")

    def unexpected_runtime_client(**_kwargs: object) -> None:
        pytest.fail("缺失的隔离 Database 不应再创建运行客户端。")

    monkeypatch.setattr(extension, "_admin_client", lambda _settings: AdminClient())
    monkeypatch.setattr(chromadb, "HttpClient", unexpected_runtime_client)

    extension._delete_chroma_database(SimpleNamespace(
        chroma_tenant="isolated-tenant",
        chroma_database="isolated-database",
        chroma_collection="isolated-collection",
    ))


def test_coordinator_preflights_before_writes_then_cleans_every_owned_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    result = extension.prepare_extension_eval(deps, run_id="4" * 16)

    assert result["status"] == "completed"
    assert result["generated_entry_count"] == 21
    assert resources.calls.count("run_retrieval") == 1
    assert resources.calls.index("inspect_targets") < resources.calls.index("create_schema")
    assert resources.calls.index("verify_sources") < resources.calls.index("create_schema")
    assert resources.calls[-10:-1] == [
        "stop_api", "shutdown_logging", "cleanup_retrieval", "dispose_runtime", "drop_schema",
        "delete_run_directory", "delete_log_directory", "verify_cleanup", "write_artifacts",
    ]
    assert "delete_chroma_database" not in resources.calls
    assert resources.calls.index("cleanup_retrieval") < resources.calls.index("dispose_runtime")
    assert resources.calls.index("dispose_runtime") < resources.calls.index("drop_schema")
    assert resources.calls[-1] == "emit"
    entries = resources.artifacts[0]["dataset"]["entries"]
    assert len(entries) == 21
    assert [(entry["eval_metadata"]["case_id"], entry["eval_metadata"]["ranking_method"])
            for entry in entries] == [pair for _ in range(3) for pair in extension.CONDITION_GROUPS]
    for case_id, method in extension.CONDITION_GROUPS:
        group = [entry for entry in entries if entry["eval_metadata"]["case_id"] == case_id
                 and entry["eval_metadata"]["ranking_method"] == method]
        assert [entry["eval_metadata"]["sample"] for entry in group] == [1, 2, 3]
    assert all("supports_answer" not in json.dumps(entry) for entry in entries)
    diagnostics = resources.artifacts[0]["summary"]["retrieval_diagnostics"]
    assert diagnostics["cases"][0]["candidate_union_count"] == 30
    assert diagnostics["cases"][0]["target_page_ranks"] == {"original": {"16": 7}}
    assert any(
        case["case_id"] == "WB-UNSUPPORTED-DRAW-GATED-01"
        for case in diagnostics["cases"]
    )
    assert "excerpt" not in json.dumps(diagnostics)
    assert "private-query-sentinel" not in json.dumps(diagnostics)
    assert "private-top-level-query-sentinel" not in json.dumps(diagnostics)


def test_false_cleanup_verification_fails_without_claiming_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    resources.cleanup_result = False
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="6" * 16)

    assert raised.value.safe_code == "CLEANUP_FAILED"
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is False
    assert resources.calls.count("run_retrieval") == 1


def test_api_stop_failure_preserves_persistent_and_runtime_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources(fail_at="stop_api")
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="7" * 16)

    assert raised.value.safe_code == "CLEANUP_FAILED"
    assert "drop_schema" not in resources.calls
    assert "delete_chroma_database" not in resources.calls
    assert "delete_run_directory" not in resources.calls
    assert "delete_log_directory" not in resources.calls
    assert "dispose_runtime" not in resources.calls
    assert "shutdown_logging" not in resources.calls
    assert resources.calls.count("verify_cleanup") == 1
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is False
    assert resources.artifacts[0]["summary"]["failure_stage"] == "cleanup.stop_api"


def test_failed_api_stop_does_not_dispose_runtime_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources(fail_at="stop_api")
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError):
        extension.prepare_extension_eval(deps, run_id="8" * 16)

    assert "dispose_runtime" not in resources.calls


def test_existing_target_is_not_cleaned_or_claimed_as_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    deps = _dependencies(resources)

    def reject_existing_target(targets: extension.Targets) -> None:
        resources.calls.append("inspect_targets")
        raise extension.PreparationError("TARGET_EXISTS")

    deps = replace(deps, inspect_targets=reject_existing_target)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="9" * 16)

    assert raised.value.safe_code == "TARGET_EXISTS"
    assert not {
        "create_schema", "create_chroma_database", "run_retrieval", "verify_cleanup",
        "drop_schema", "delete_chroma_database",
    }.intersection(resources.calls)
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is False


def test_skips_unsupported_positive_group_and_prepares_remaining_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    deps = _dependencies(resources)
    original_run = deps.run_retrieval

    def retrieval_with_background_only_loan(base_url: str, capture: extension.CandidateCaptureMap):
        summary = original_run(base_url, capture)
        capture["WB-REV-LOAN-01/original"] = [
            {"filename": "belarus-snapshot.pdf", "location_type": "PDF_PAGE_RANGE",
             "location_start": 7, "location_end": 7, "excerpt": "project background"}
            for _ in range(8)
        ]
        for case in summary["cases"]:
            if case["case_id"] == "WB-REV-LOAN-01":
                case["rankings"]["original"] = [
                    {"source_page": 7, "supports_answer": True} for _ in range(8)
                ]
        _complete_candidate_capture(capture)
        return summary

    deps = replace(deps, run_retrieval=retrieval_with_background_only_loan)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    result = extension.prepare_extension_eval(deps, run_id="a" * 16)

    assert result["status"] == "partial"
    assert result["generated_entry_count"] == 18
    assert result["failure_count"] == 1
    assert result["skipped_group_count"] == 1
    summary = resources.artifacts[0]["summary"]
    assert summary["failure_count"] == summary["skipped_group_count"] == 1
    assert "excerpt" not in json.dumps(summary, ensure_ascii=False)


def test_zero_runnable_entries_fails_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    deps = _dependencies(resources)
    original_run = deps.run_retrieval

    def retrieval_without_eligible_groups(base_url: str, capture: extension.CandidateCaptureMap):
        summary = original_run(base_url, capture)
        negative_cases = {"WB-ENTITY-NEG-01", "WB-UNSUPPORTED-DRAW-01"}
        for case in summary["cases"]:
            for rows in case["rankings"].values():
                for row in rows:
                    row["supports_answer"] = case["case_id"] in negative_cases
        return summary

    deps = replace(deps, run_retrieval=retrieval_without_eligible_groups)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="b" * 16)

    assert raised.value.safe_code == "DATASET_INCOMPLETE"
    assert resources.artifacts[0]["summary"]["generated_entry_count"] == 0
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is True
    assert resources.artifacts[0]["summary"]["failure_stage"] == "prepare.build_pixie_dataset"


@pytest.mark.parametrize(
    ("creation_step", "owned_cleanup", "prior_cleanup"),
    [
        ("create_schema", "drop_schema", ()),
        ("create_chroma_database", "delete_chroma_database", ("drop_schema",)),
        ("create_run_directory", "delete_run_directory", ("drop_schema", "delete_chroma_database")),
    ],
)
def test_failed_resource_creation_does_not_claim_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    creation_step: str,
    owned_cleanup: str,
    prior_cleanup: tuple[str, ...],
) -> None:
    resources = FakeResources(fail_at=creation_step)
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="c" * 16)

    assert raised.value.safe_code == "PREPARATION_FAILED"
    assert owned_cleanup not in resources.calls
    assert all(action in resources.calls for action in prior_cleanup)
    assert "verify_cleanup" in resources.calls
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is resources.cleanup_result


def test_retrieval_cleanup_failure_still_disposes_and_preserves_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources(fail_at="cleanup_retrieval")
    resources.cleanup_result = False
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="d" * 16)

    assert raised.value.safe_code == "CLEANUP_FAILED"
    assert resources.calls.index("cleanup_retrieval") < resources.calls.index("dispose_runtime")
    assert resources.calls.index("dispose_runtime") < resources.calls.index("drop_schema")
    assert "delete_chroma_database" in resources.calls
    assert "delete_run_directory" not in resources.calls
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is False
    assert resources.artifacts[0]["summary"]["failure_stage"] == "cleanup.cleanup_retrieval"


def test_summary_preserves_primary_and_cleanup_failure_diagnostics_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    deps = _dependencies(resources)
    residuals = {
        "postgres_zero": True,
        "chroma_zero": False,
        "files_zero": True,
        "checkpoint_zero": True,
    }

    def fail_retrieval(_base_url: str, _capture: extension.CandidateCaptureMap) -> Mapping[str, Any]:
        error = extension.PreparationError("RETRIEVAL_FAILED")
        error.preflight_error_code = "SQLALCHEMY_QUERY"
        error.residual_probe_error_code = "SQLALCHEMY_CONNECTION"
        raise error

    def fail_residual_verification() -> None:
        error = extension.PreparationError("CLEANUP_FAILED")
        error.zero_residuals = residuals
        error.residual_probe_error_code = "FILESYSTEM"
        raise error

    deps = replace(
        deps,
        run_retrieval=fail_retrieval,
        cleanup_retrieval=fail_residual_verification,
        verify_cleanup=lambda _targets: False,
    )
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="f" * 16)

    summary = resources.artifacts[0]["summary"]
    assert raised.value.safe_code == "CLEANUP_FAILED"
    assert summary["error_code"] == "CLEANUP_FAILED"
    assert summary["failure_stage"] == "cleanup.cleanup_retrieval"
    assert summary["primary_error_code"] == "RETRIEVAL_FAILED"
    assert summary["primary_failure_stage"] == "prepare.run_retrieval"
    assert summary["cleanup_error_code"] == "CLEANUP_FAILED"
    assert summary["cleanup_failure_stage"] == "cleanup.cleanup_retrieval"
    assert summary["zero_residuals"] == residuals
    assert summary["preflight_error_code"] == "SQLALCHEMY_QUERY"
    assert summary["residual_probe_error_code"] == "FILESYSTEM"
    assert "sensitive failure text" not in json.dumps(summary)
    assert "sensitive failure text" not in str(raised.value)


def test_nested_retrieval_diagnostics_reach_outer_summary_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import archive_world_bank_retrieval_acceptance as retrieval

    resources = FakeResources()
    deps = replace(_dependencies(resources), run_retrieval=extension._run_retrieval)
    residuals = {
        "postgres_zero": False,
        "chroma_zero": True,
        "files_zero": True,
        "checkpoint_zero": True,
    }

    def fail_retrieval(**_kwargs: object) -> None:
        error = retrieval.AcceptanceError("sensitive nested exception text")
        error.retrieval_stage = "prepare.document_seeding"
        error.retrieval_operation_code = "SEED_CONFIRM"
        error.retrieval_http_status = 500
        error.cleanup_failed_steps = ("ZERO_RESIDUAL_VERIFY", "untrusted-resource")
        error.residual_probe_stage = "residuals.postgres_count"
        error.zero_residuals = residuals
        error.preflight_error_code = "UNKNOWN"
        error.residual_probe_error_code = "SQLALCHEMY_DRIVER"
        raise error

    monkeypatch.setattr(retrieval, "run_world_bank_retrieval_acceptance", fail_retrieval)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda _path: True)

    with pytest.raises(extension.PreparationError):
        extension.prepare_extension_eval(deps, run_id="1" * 16)

    summary = resources.artifacts[0]["summary"]
    assert summary["retrieval_stage"] == "prepare.document_seeding"
    assert summary["retrieval_operation_code"] == "SEED_CONFIRM"
    assert summary["retrieval_http_status"] == 500
    assert summary["cleanup_failed_steps"] == ["ZERO_RESIDUAL_VERIFY"]
    assert summary["residual_probe_stage"] == "residuals.postgres_count"
    assert summary["zero_residuals"] == residuals
    assert summary["preflight_error_code"] == "UNKNOWN"
    assert summary["residual_probe_error_code"] == "SQLALCHEMY_DRIVER"
    assert "untrusted-resource" not in json.dumps(summary)
    assert "sensitive nested exception text" not in json.dumps(summary)


def test_runtime_dispose_failure_prevents_schema_drop_and_success_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources(fail_at="dispose_runtime")
    resources.cleanup_result = False
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="e" * 16)

    assert raised.value.safe_code == "CLEANUP_FAILED"
    assert "drop_schema" not in resources.calls
    assert resources.artifacts[0]["summary"]["cleanup_verified"] is False


@pytest.mark.parametrize("failure", [
    "validate_configuration", "verify_sources", "inspect_targets",
    "verify_search_path", "run_retrieval",
])
def test_failure_paths_do_not_leak_exception_text_and_attempt_owned_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    resources = FakeResources(fail_at=failure)
    deps = _dependencies(resources)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(deps, run_id="5" * 16)

    assert "sensitive failure text" not in str(raised.value)
    summary = resources.artifacts[0]["summary"]
    if failure == "inspect_targets":
        assert summary["failure_stage"] == "prepare.inspect_targets"
        assert "sensitive failure text" not in json.dumps(summary)
    if failure == "verify_search_path":
        assert resources.calls.index("create_schema") < resources.calls.index("verify_search_path")
        assert "drop_schema" in resources.calls
        assert resources.calls.index("verify_search_path") < resources.calls.index("drop_schema")
        assert "start_api" not in resources.calls
        assert "run_retrieval" not in resources.calls
        assert "cleanup_retrieval" not in resources.calls
        assert "create_chroma_database" not in resources.calls
        return
    if failure == "run_retrieval":
        assert "stop_api" in resources.calls
        assert "cleanup_retrieval" in resources.calls
        assert "drop_schema" in resources.calls
        assert "delete_chroma_database" not in resources.calls
        assert "delete_run_directory" in resources.calls


def test_safe_summary_omits_unallowlisted_failure_stage() -> None:
    summary = extension._safe_summary(
        {"status": "failed", "failure_stage": "secret-setting-value"},
        error_code="PREPARATION_FAILED",
        retrieval_operation_code="secret-setting-value",
        retrieval_http_status=True,
    )

    assert "failure_stage" not in summary
    assert "retrieval_operation_code" not in summary
    assert "retrieval_http_status" not in summary


def test_retrieval_diagnostic_failure_reports_the_exact_preparation_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = FakeResources()
    original_sanitizer = extension._safe_retrieval_diagnostics

    def fail_sanitizer(value: Any) -> dict[str, Any] | None:
        if isinstance(value, Mapping):
            raise TypeError("sensitive sanitizer failure")
        return original_sanitizer(value)

    monkeypatch.setattr(extension, "_safe_retrieval_diagnostics", fail_sanitizer)
    monkeypatch.setattr(extension, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(extension, "is_git_ignored", lambda _path: True)

    with pytest.raises(extension.PreparationError) as raised:
        extension.prepare_extension_eval(_dependencies(resources), run_id="2" * 16)

    summary = resources.artifacts[0]["summary"]
    assert raised.value.safe_code == "PREPARATION_FAILED"
    assert summary["failure_stage"] == "prepare.sanitize_retrieval_diagnostics"
    assert summary["diagnostic_exception_type"] == "TypeError"
    assert "sensitive sanitizer failure" not in json.dumps(summary)


def test_sanitizer_error_line_is_taken_only_from_the_sanitizer_frame() -> None:
    class BrokenSummary(dict[str, Any]):
        def get(self, key: str, default: Any = None) -> Any:
            if key == "cases":
                raise AttributeError("sensitive mapping failure")
            return super().get(key, default)

    with pytest.raises(AttributeError) as raised:
        extension._safe_retrieval_diagnostics(BrokenSummary())

    line = extension._safe_diagnostic_error_line(raised.value)
    assert line is not None and line > 0
    assert "sensitive mapping failure" not in str(line)


def test_safe_retrieval_diagnostics_preserves_graph_probe_after_rank_summaries() -> None:
    summary = {
        "evaluation": "retrieval-only",
        "cases": [{
            "case_id": "LUSHAN-01",
            "ranking_summary": {"rankings": {
                "original": {"retrieved_relevant_context_ranks": [2]},
            }},
        }],
        "fr042_graph_probe": {
            "external_model_calls": 0,
            "target_evidence_ranks": [1],
            "target_in_top8": True,
            "answer_quality_evaluated": False,
        },
    }

    diagnostics = extension._safe_retrieval_diagnostics(summary)

    assert diagnostics is not None
    assert diagnostics["cases"][0]["ranking_summary"]["rankings"]["original"][
        "retrieved_relevant_context_ranks"
    ] == [2]
    assert diagnostics["fr042_graph_probe"]["target_evidence_ranks"] == [1]


@pytest.mark.parametrize(
    "stage",
    [
        "prepare.empty_target.current_schema",
        "prepare.empty_target.namespace_contract",
        "prepare.empty_target.embedding_mode",
        "prepare.empty_target.chroma_collections",
        "prepare.empty_target.postgres_count",
        "prepare.empty_target.vector_count",
        "prepare.empty_target.file_count",
        "prepare.empty_target.checkpoint_count",
    ],
)
def test_safe_summary_preserves_allowlisted_empty_target_substage(stage: str) -> None:
    summary = extension._safe_summary(
        {"status": "failed"},
        error_code="PREPARATION_FAILED",
        retrieval_stage=stage,
    )

    assert summary["retrieval_stage"] == stage


def test_emit_forwards_only_allowlisted_failure_diagnostics(capsys: pytest.CaptureFixture[str]) -> None:
    extension._emit({
        "status": "failed",
        "error_code": "CLEANUP_FAILED",
        "failure_stage": "cleanup.cleanup_retrieval",
        "primary_error_code": "RETRIEVAL_FAILED",
        "primary_failure_stage": "prepare.run_retrieval",
        "cleanup_error_code": "CLEANUP_FAILED",
        "cleanup_failure_stage": "cleanup.cleanup_retrieval",
        "retrieval_stage": "prepare.candidate_query_ranking",
        "retrieval_operation_code": "SEED_CONFIRM",
        "retrieval_http_status": 500,
        "diagnostic_exception_type": "TypeError",
        "cleanup_failed_steps": ["ZERO_RESIDUAL_VERIFY", "untrusted-step"],
        "residual_probe_stage": "residuals.chroma_match",
        "preflight_error_code": "SQLALCHEMY_QUERY",
        "residual_probe_error_code": "FILESYSTEM",
        "zero_residuals": {
            "postgres_zero": True,
            "chroma_zero": False,
            "files_zero": True,
            "checkpoint_zero": True,
        },
        "unexpected_diagnostic": "sensitive exception contents",
    })

    emitted = json.loads(capsys.readouterr().out)

    assert emitted["error_code"] == "CLEANUP_FAILED"
    assert emitted["failure_stage"] == "cleanup.cleanup_retrieval"
    assert emitted["primary_error_code"] == "RETRIEVAL_FAILED"
    assert emitted["primary_failure_stage"] == "prepare.run_retrieval"
    assert emitted["cleanup_error_code"] == "CLEANUP_FAILED"
    assert emitted["cleanup_failure_stage"] == "cleanup.cleanup_retrieval"
    assert emitted["retrieval_stage"] == "prepare.candidate_query_ranking"
    assert emitted["retrieval_operation_code"] == "SEED_CONFIRM"
    assert emitted["retrieval_http_status"] == 500
    assert emitted["diagnostic_exception_type"] == "TypeError"
    assert emitted["cleanup_failed_steps"] == ["ZERO_RESIDUAL_VERIFY"]
    assert emitted["residual_probe_stage"] == "residuals.chroma_match"
    assert emitted["preflight_error_code"] == "SQLALCHEMY_QUERY"
    assert emitted["residual_probe_error_code"] == "FILESYSTEM"
    assert emitted["zero_residuals"] == {
        "postgres_zero": True,
        "chroma_zero": False,
        "files_zero": True,
        "checkpoint_zero": True,
    }
    assert "unexpected_diagnostic" not in emitted
    assert "sensitive exception contents" not in json.dumps(emitted)

    extension._emit({
        "status": "failed",
        "retrieval_stage": "untrusted-stage",
        "cleanup_failed_steps": ["credential=secret"],
        "residual_probe_stage": "untrusted-probe",
        "preflight_error_code": "SQLAlchemyOperationalError host=db.internal",
        "residual_probe_error_code": "secret-message",
        "zero_residuals": {
            "postgres_zero": True,
            "chroma_zero": 0,
            "files_zero": True,
            "checkpoint_zero": True,
            "resource_name": "private",
        },
    })

    rejected = json.loads(capsys.readouterr().out)
    assert "retrieval_stage" not in rejected
    assert "cleanup_failed_steps" not in rejected
    assert "residual_probe_stage" not in rejected
    assert "preflight_error_code" not in rejected
    assert "residual_probe_error_code" not in rejected
    assert "zero_residuals" not in rejected
    assert "secret" not in json.dumps(rejected)


def test_temporary_cleanup_preserves_result_directory(tmp_path: Path) -> None:
    paths = extension.RunPaths(
        run_id="c" * 16,
        root=tmp_path / "acceptance" / ("c" * 16),
        runtime_root=tmp_path / "acceptance-runs" / ("c" * 16),
        result_dir=tmp_path / "acceptance" / ("c" * 16),
        candidate_file=tmp_path / "acceptance" / ("c" * 16) / "candidates.json",
        summary_file=tmp_path / "acceptance" / ("c" * 16) / "summary.json",
        file_storage=tmp_path / "acceptance-runs" / ("c" * 16) / "files",
        checkpoint=tmp_path / "acceptance-runs" / ("c" * 16) / "checkpoints.sqlite",
        log_root=tmp_path / "os-temp" / ("c" * 16),
        log_file=tmp_path / "os-temp" / ("c" * 16) / "logs" / "app.log",
    )
    paths.runtime_root.mkdir(parents=True)
    paths.file_storage.mkdir()
    paths.result_dir.mkdir(parents=True)
    paths.candidate_file.write_text("{}", encoding="utf-8")

    extension._delete_run_directory(paths)

    assert not paths.runtime_root.exists()
    assert paths.candidate_file.is_file()


def test_retrieval_cleanup_checks_all_four_zero_residual_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import archive_world_bank_retrieval_acceptance as retrieval

    monkeypatch.setattr(retrieval, "_verify_zero_residuals", lambda: {
        "postgres_zero": True, "chroma_zero": True,
        "files_zero": True, "checkpoint_zero": False,
    })

    with pytest.raises(extension.PreparationError) as raised:
        extension._cleanup_retrieval()

    assert raised.value.safe_code == "CLEANUP_FAILED"
