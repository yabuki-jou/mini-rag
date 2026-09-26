"""固定 World Bank 双项目检索验收脚本的隔离与脱敏约束。"""

from __future__ import annotations

import subprocess
import sqlite3
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from scripts import archive_world_bank_retrieval_acceptance as acceptance
from app.models import EvidenceLocationType
from app.schemas.archive_retrieval import ArchiveRetrievalItemRead, ArchiveRetrievalResponse
from app.services.archive import catalog as catalog_service
from app.services.archive import retrieval as retrieval_service


def test_cli_help_runs_from_repository_root_without_import_error() -> None:
    script = acceptance.PROJECT_ROOT / "scripts" / "archive_world_bank_retrieval_acceptance.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=acceptance.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "--base-url" in result.stdout


def _isolated_targets(tmp_path: Path) -> dict[str, object]:
    run_root = tmp_path / "tests" / "pytest_docs" / "acceptance-runs" / "91af2c00"
    return {
        "postgres_schema": "fr042_wb_91af2c00",
        "chroma_tenant": "mini_rag_tenant",
        "chroma_database": "fr042_wb_db_91af2c00",
        "chroma_collection": "fr042_wb_collection_91af2c00",
        "file_storage_path": run_root / "files",
        "checkpoint_path": run_root / "checkpoints.sqlite",
    }


def test_rejects_default_public_and_non_dedicated_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acceptance, "PROJECT_ROOT", tmp_path)
    targets = _isolated_targets(tmp_path)

    acceptance.validate_isolated_targets(**targets)

    for changes in (
        {"postgres_schema": "public"},
        {"postgres_schema": "mini_rag"},
        {"chroma_database": "mini_rag_chroma"},
        {"chroma_collection": "archive_final_chunks"},
        {"chroma_collection": "shared_custom_collection"},
    ):
        with pytest.raises(acceptance.AcceptanceError):
            acceptance.validate_isolated_targets(**{**targets, **changes})


def test_nonempty_chroma_database_is_rejected() -> None:
    acceptance.require_empty_chroma_database([])

    with pytest.raises(acceptance.AcceptanceError, match="无任何 Collection"):
        acceptance.require_empty_chroma_database([SimpleNamespace(name="unrelated")])


@pytest.mark.parametrize(
    ("failed_check", "expected_stage"),
    [
        ("current_schema", "prepare.empty_target.current_schema"),
        ("namespace_contract", "prepare.empty_target.namespace_contract"),
        ("embedding_mode", "prepare.empty_target.embedding_mode"),
        ("chroma_collections", "prepare.empty_target.chroma_collections"),
        ("postgres_count", "prepare.empty_target.postgres_count"),
        ("vector_count", "prepare.empty_target.vector_count"),
        ("file_count", "prepare.empty_target.file_count"),
        ("checkpoint_count", "prepare.empty_target.checkpoint_count"),
    ],
)
def test_empty_target_subcheck_failure_reports_safe_stage_without_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_check: str,
    expected_stage: str,
) -> None:
    def fail_with_private_text(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private database and resource details")

    monkeypatch.setattr(
        acceptance,
        "settings",
        SimpleNamespace(
            chroma_tenant="tenant-fixture",
            chroma_database="database-fixture",
            chroma_final_collection="collection-fixture",
            archive_embedding_context_mode=(
                "invalid" if failed_check == "embedding_mode" else "evidence_values"
            ),
            file_storage_path=tmp_path / "files",
            agent_checkpoint_path=tmp_path / "checkpoints.sqlite",
        ),
    )
    monkeypatch.setattr(acceptance, "_read_current_schema", lambda: "schema-fixture")
    monkeypatch.setattr(acceptance, "validate_isolated_targets", lambda **_kwargs: None)
    monkeypatch.setattr(acceptance, "get_chroma_client", lambda: SimpleNamespace(
        list_collections=lambda: [object()] if failed_check == "chroma_collections" else []
    ))
    monkeypatch.setattr(acceptance, "_postgres_row_count", lambda: 0)
    monkeypatch.setattr(acceptance, "_existing_final_collection_count", lambda: 0)
    monkeypatch.setattr(acceptance, "_stored_file_count", lambda _path: 0)
    monkeypatch.setattr(acceptance, "_checkpoint_row_count", lambda _path: 0)

    if failed_check == "current_schema":
        monkeypatch.setattr(acceptance, "_read_current_schema", fail_with_private_text)
    elif failed_check == "namespace_contract":
        monkeypatch.setattr(acceptance, "validate_isolated_targets", fail_with_private_text)
    elif failed_check == "postgres_count":
        monkeypatch.setattr(acceptance, "_postgres_row_count", fail_with_private_text)
    elif failed_check == "vector_count":
        monkeypatch.setattr(acceptance, "_existing_final_collection_count", fail_with_private_text)
    elif failed_check == "file_count":
        monkeypatch.setattr(acceptance, "_stored_file_count", fail_with_private_text)
    elif failed_check == "checkpoint_count":
        monkeypatch.setattr(acceptance, "_checkpoint_row_count", fail_with_private_text)

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance._assert_empty_targets()

    assert raised.value.retrieval_stage == expected_stage
    assert "private database and resource details" not in str(raised.value)


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("expected_rejection", "EXPECTED_REJECTION"),
        ("sqlalchemy_connection", "SQLALCHEMY_CONNECTION"),
        ("sqlalchemy_driver", "SQLALCHEMY_DRIVER"),
        ("sqlalchemy_query", "SQLALCHEMY_QUERY"),
        ("filesystem", "FILESYSTEM"),
        ("unknown", "UNKNOWN"),
    ],
)
def test_preflight_exception_category_uses_fixed_safe_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_code: str,
) -> None:
    from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

    class PrivateFailureClassNameError(Exception):
        pass

    def raise_private_exception() -> None:
        if failure_kind == "expected_rejection":
            raise acceptance.AcceptanceError("secret=account SQL SELECT private host=db.local")
        if failure_kind == "sqlalchemy_connection":
            raise OperationalError(
                "SELECT private FROM records host=db.local",
                {"account": "private-user"},
                RuntimeError("password=private"),
            )
        if failure_kind == "sqlalchemy_driver":
            raise DBAPIError(
                "SELECT private FROM records host=db.local",
                {"account": "private-user"},
                RuntimeError("password=private"),
            )
        if failure_kind == "sqlalchemy_query":
            raise SQLAlchemyError("SELECT private FROM records host=db.local")
        if failure_kind == "filesystem":
            raise PermissionError("C:\\private\\customer.db account=private-user")
        raise PrivateFailureClassNameError("private exception class/message")

    monkeypatch.setattr(
        acceptance,
        "settings",
        SimpleNamespace(
            chroma_tenant="tenant-fixture",
            chroma_database="database-fixture",
            chroma_final_collection="collection-fixture",
            archive_embedding_context_mode="evidence_values",
            file_storage_path=tmp_path / "files",
            agent_checkpoint_path=tmp_path / "checkpoints.sqlite",
        ),
    )
    monkeypatch.setattr(acceptance, "_read_current_schema", lambda: "schema-fixture")
    monkeypatch.setattr(acceptance, "validate_isolated_targets", lambda **_kwargs: None)
    monkeypatch.setattr(
        acceptance,
        "get_chroma_client",
        lambda: SimpleNamespace(list_collections=lambda: []),
    )
    monkeypatch.setattr(acceptance, "_postgres_row_count", raise_private_exception)
    monkeypatch.setattr(acceptance, "_existing_final_collection_count", lambda: 0)
    monkeypatch.setattr(acceptance, "_stored_file_count", lambda _path: 0)
    monkeypatch.setattr(acceptance, "_checkpoint_row_count", lambda _path: 0)

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance._assert_empty_targets()

    assert raised.value.preflight_error_code == expected_code
    assert raised.value.retrieval_stage == "prepare.empty_target.postgres_count"
    public_error = str(raised.value)
    assert all(secret not in public_error for secret in (
        "private", "SELECT", "host=", "account=", "password=",
        "PrivateFailureClassNameError", "customer.db",
    ))


def test_diagnostic_expected_evidence_helper_is_imported_and_callable() -> None:
    entry = {
        "eval_metadata": {"expected_document_filenames": ["loan.pdf"]},
        "eval_input": [
            {
                "name": "archive_agent_evidence_retrieval",
                "value": [
                    {
                        "filename": "loan.pdf",
                        "location_type": "PDF_PAGE",
                        "location_start": 16,
                        "location_end": 16,
                        "excerpt": "贷款批准金额为 250 million US dollars。",
                    },
                    {"filename": "other.pdf", "excerpt": "不应进入目标证据。"},
                ],
            }
        ],
    }

    expected = acceptance._diagnostic_expected_evidence(entry)

    assert expected == {
        "relative_path": "loan.pdf",
        "items": [
            {
                "location_type": "PDF_PAGE",
                "location_start": 16,
                "location_end": 16,
                "excerpt": "贷款批准金额为 250 million US dollars。",
            }
        ],
    }


def test_graph_probe_runs_compiled_graph_and_passes_current_question_to_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """脚本化工具调用应经过真实 Graph State 序列化并到达检索服务。"""
    user_id = uuid4()
    project_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    question = "世界银行贷款批准金额是多少？"
    tool_query = "项目融资金额"
    service_calls: list[dict[str, object]] = []
    candidate = ArchiveRetrievalItemRead(
        chunk_id="a" * 64,
        document_id=document_id,
        filename="appraisal.pdf",
        location_type=EvidenceLocationType.PDF_PAGE,
        location_start=16,
        location_end=16,
        excerpt="IBRD Loan Amount: 250 million US dollars.",
        score=0.9,
    )

    def retrieve(**kwargs: object) -> ArchiveRetrievalResponse:
        service_calls.append(kwargs)
        return ArchiveRetrievalResponse(
            items=[candidate], requested_top_k=8, returned_count=1
        )

    monkeypatch.setattr(retrieval_service, "retrieve_archive_answer_candidates", retrieve)
    monkeypatch.setattr(
        catalog_service,
        "list_agent_confirmed_document_titles",
        lambda *, document_ids, **_kwargs: {
            document_id: "Lushan appraisal" for document_id in document_ids
        },
    )

    checkpoint_path = tmp_path / "graph-probe.sqlite"
    result = acceptance._run_graph_evidence_probe(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        question=question,
        tool_query=tool_query,
        session_factory=lambda: nullcontext(object()),
        checkpoint_path=checkpoint_path,
        thread_id="fr042-graph-probe",
    )

    assert len(service_calls) == 1
    assert service_calls[0]["query"] == tool_query
    assert service_calls[0]["gate_question"] == question
    assert result["tool_payload"]["found"] is True
    assert result["tool_payload"]["results"][0]["excerpt"] == candidate.excerpt
    assert result["turn"].tool_call_count == 1
    assert result["turn"].tool_events[0].status == "COMPLETED"
    assert result["agent_model_invocations"] == 2
    assert result["judge_model_invocations"] == 1
    with sqlite3.connect(checkpoint_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        remaining_checkpoint_rows = sum(
            int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in ("checkpoints", "writes", "blobs")
            if table in tables
        )
    assert remaining_checkpoint_rows == 0


def test_local_target_cleanup_removes_empty_wal_checkpoint_and_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents.checkpoint import open_checkpoint_store

    run_root = tmp_path / "acceptance-run"
    file_root = run_root / "files"
    file_root.mkdir(parents=True)
    checkpoint_path = run_root / "checkpoints.sqlite"
    with open_checkpoint_store(checkpoint_path) as store:
        journal_mode = str(
            store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()

    assert journal_mode == "wal"
    monkeypatch.setattr(
        acceptance,
        "settings",
        SimpleNamespace(
            file_storage_path=file_root,
            agent_checkpoint_path=checkpoint_path,
        ),
    )

    acceptance._remove_local_run_targets()

    assert not run_root.exists()


def test_zero_residual_verification_uses_admin_client_and_checks_four_layers_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    class AdminClient:
        def list_databases(self, *, tenant: str) -> list[dict[str, str]]:
            calls.append(("list_databases", tenant))
            return [{"name": "unrelated_database"}]

    class RuntimeClient:
        def list_databases(self, *, tenant: str) -> list[dict[str, str]]:
            raise AssertionError("归零核验不得通过普通 Chroma 客户端列出数据库。")

    monkeypatch.setattr(acceptance, "get_chroma_admin_client", lambda: AdminClient(), raising=False)
    monkeypatch.setattr(acceptance, "get_chroma_client", lambda: RuntimeClient())
    monkeypatch.setattr(acceptance, "_postgres_row_count", lambda: 0)
    monkeypatch.setattr(acceptance.settings, "chroma_tenant", "isolated-tenant")
    monkeypatch.setattr(acceptance.settings, "chroma_database", "fr042_wb_db_91af2c00")
    monkeypatch.setattr(acceptance.settings, "file_storage_dir", tmp_path / "missing-files")
    monkeypatch.setattr(
        acceptance.settings,
        "agent_checkpoint_file",
        tmp_path / "missing-checkpoint.sqlite",
    )

    result = acceptance._verify_zero_residuals()

    assert result == {
        "postgres_zero": True,
        "chroma_zero": True,
        "files_zero": True,
        "checkpoint_zero": True,
    }
    assert calls == [("list_databases", "isolated-tenant")]


def test_zero_residual_failure_preserves_only_safe_boolean_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AdminClient:
        def list_databases(self, *, tenant: str) -> list[dict[str, str]]:
            return [{"name": "fr042_wb_db_91af2c00"}]

    monkeypatch.setattr(acceptance, "get_chroma_admin_client", lambda: AdminClient(), raising=False)
    monkeypatch.setattr(acceptance, "_postgres_row_count", lambda: 0)
    monkeypatch.setattr(acceptance.settings, "chroma_tenant", "isolated-tenant")
    monkeypatch.setattr(acceptance.settings, "chroma_database", "fr042_wb_db_91af2c00")
    monkeypatch.setattr(acceptance.settings, "file_storage_dir", tmp_path / "missing-files")
    monkeypatch.setattr(
        acceptance.settings,
        "agent_checkpoint_file",
        tmp_path / "existing-checkpoint.sqlite",
    )
    (tmp_path / "existing-checkpoint.sqlite").touch()

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance._verify_zero_residuals()

    assert str(raised.value) == "检索-only 验收清理后仍有持久化残留。"
    assert raised.value.zero_residuals == {
        "postgres_zero": True,
        "chroma_zero": False,
        "files_zero": True,
        "checkpoint_zero": False,
    }
    assert raised.value.residual_probe_stage == "residuals.validate"
    assert raised.value.residual_probe_error_code == "EXPECTED_REJECTION"


@pytest.mark.parametrize(
    ("failed_probe", "expected_stage", "expected_error_code"),
    [
        ("chroma", "residuals.chroma_list", "UNKNOWN"),
        ("postgres", "residuals.postgres_count", "SQLALCHEMY_CONNECTION"),
        ("files", "residuals.files_check", "FILESYSTEM"),
        ("checkpoint", "residuals.checkpoint_check", "FILESYSTEM"),
    ],
)
def test_zero_residual_probe_failure_has_safe_stage_without_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_probe: str,
    expected_stage: str,
    expected_error_code: str,
) -> None:
    from sqlalchemy.exc import OperationalError

    class AdminClient:
        def list_databases(self, *, tenant: str) -> list[dict[str, str]]:
            if failed_probe == "chroma":
                raise RuntimeError("exception-class=PrivateAdminFailure host=chroma.internal")
            return []

    def postgres_row_count() -> int:
        if failed_probe == "postgres":
            raise OperationalError(
                "SELECT private FROM records host=postgres.internal",
                {"account": "private-user"},
                RuntimeError("password=private"),
            )
        return 0

    monkeypatch.setattr(acceptance, "get_chroma_admin_client", lambda: AdminClient(), raising=False)
    monkeypatch.setattr(
        acceptance,
        "_postgres_row_count",
        postgres_row_count,
    )
    monkeypatch.setattr(acceptance.settings, "chroma_tenant", "isolated-tenant")
    monkeypatch.setattr(acceptance.settings, "chroma_database", "isolated-database")
    monkeypatch.setattr(
        acceptance.settings,
        "file_storage_dir",
        tmp_path / "missing-files",
    )
    monkeypatch.setattr(
        acceptance.settings,
        "agent_checkpoint_file",
        tmp_path / "missing-checkpoint.sqlite",
    )
    original_exists = Path.exists

    def exists(path: Path) -> bool:
        if failed_probe == "files" and path == tmp_path / "missing-files":
            raise PermissionError("C:\\private\\uploads host=files.internal")
        if failed_probe == "checkpoint" and path == tmp_path / "missing-checkpoint.sqlite":
            raise PermissionError("C:\\private\\checkpoint.sqlite host=files.internal")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", exists)

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance._verify_zero_residuals()

    assert raised.value.residual_probe_stage == expected_stage
    assert raised.value.residual_probe_error_code == expected_error_code
    public_error = str(raised.value)
    assert all(secret not in public_error for secret in (
        "private", "SELECT", "host=", "account=", "password=",
        "PrivateAdminFailure", "customer.db",
    ))


def test_chroma_database_comparison_failure_has_its_own_safe_probe_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AdminClient:
        def list_databases(self, *, tenant: str) -> list[dict[str, str]]:
            return []

    class InvalidDatabaseName:
        def __hash__(self) -> int:
            raise RuntimeError("sensitive database comparison exception")

    monkeypatch.setattr(acceptance, "get_chroma_admin_client", lambda: AdminClient(), raising=False)
    monkeypatch.setattr(
        acceptance,
        "settings",
        SimpleNamespace(
            chroma_tenant="tenant-placeholder",
            chroma_database=InvalidDatabaseName(),
            file_storage_path=tmp_path / "files",
            agent_checkpoint_path=tmp_path / "checkpoint.sqlite",
        ),
    )

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance._verify_zero_residuals()

    assert raised.value.residual_probe_stage == "residuals.chroma_match"
    assert raised.value.residual_probe_error_code == "UNKNOWN"
    assert "sensitive database comparison exception" not in str(raised.value)


def test_source_manifest_rejects_hash_mismatch(tmp_path: Path) -> None:
    import hashlib
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"fixture-pdf-bytes")
    with pytest.raises(acceptance.AcceptanceError, match="哈希不匹配"):
        acceptance._require_file_hash(
            pdf,
            hashlib.sha256(b"wrong-bytes").hexdigest(),
            label="Belarus Snapshot",
        )


def test_query_plan_uses_bare_expression_for_gate_positive_loan_queries() -> None:
    plan = acceptance.build_query_plan(
        {
            "LUSHAN-01": "世界银行贷款批准金额是多少？",
            "LUSHAN-02": "项目延期到哪一天？",
            "LUSHAN-03": "独立复核评级是什么？",
            "LUSHAN-04": "项目负责人联系电话是多少？",
        },
        {
            "WB-REV-LOAN-01": "世界银行贷款金额是多少？",
            "WB-CONTEXT-NONLOAN-01": "世界银行项目的口岸能力是多少？",
            "WB-ENTITY-NEG-01": "IFC 为项目提供多少贷款？",
            "WB-UNSUPPORTED-DRAW-01": "项目当前实际提款金额是多少？",
            "WB-UNSUPPORTED-DRAW-GATED-01": (
                "白俄罗斯 M6 交通走廊改善项目的世界银行贷款在项目完工时实际提款金额是多少？"
            ),
        },
    )

    assert len(plan) == 9
    assert [item["query_kind"] for item in plan if item["supplementary_query"]] == [
        "LUSHAN-01",
        "WB-REV-LOAN-01",
        "WB-UNSUPPORTED-DRAW-GATED-01",
    ]
    assert {
        item["query_kind"]: item["supplementary_query"]
        for item in plan
        if item["supplementary_query"]
    } == {
        "LUSHAN-01": "IBRD IDA",
        "WB-REV-LOAN-01": "IBRD IDA",
        "WB-UNSUPPORTED-DRAW-GATED-01": "IBRD IDA",
    }
    assert next(item for item in plan if item["query_kind"] == "LUSHAN-04")["supplementary_query"] is None
    assert next(item for item in plan if item["query_kind"] == "WB-UNSUPPORTED-DRAW-01")["supplementary_query"] is None
    for item in plan:
        if item["supplementary_query"]:
            assert item["supplementary_query"] == "IBRD IDA"


def test_retrieval_only_lushan_query_set_includes_fixed_no_trigger_case() -> None:
    queries = {
        "LUSHAN-01": "世界银行贷款批准金额是多少？",
        "LUSHAN-02": "项目延期到哪一天？",
        "LUSHAN-03": "独立复核评级是什么？",
        "LUSHAN-04": "芦山项目现场总负责人的手机号是多少？",
    }
    belarus = dict(acceptance._BELARUS_QUERIES)

    plan = acceptance.build_query_plan(queries, belarus)

    assert [item["query_kind"] for item in plan[:4]] == [
        "LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04"
    ]
    assert next(item for item in plan if item["query_kind"] == "LUSHAN-04")["supplementary_query"] is None


def test_production_reranks_merged_pool_once_with_bare_query_and_reports_belarus_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(owner_id="owner", kb_id="kb")

    class SessionStub:
        def get(self, *_args: object) -> object:
            return project

    session = SessionStub()

    def candidate(chunk_id: str, page: int) -> SimpleNamespace:
        return SimpleNamespace(
            chunk_id=chunk_id,
            excerpt=f"公开页 {page}",
            filename="belarus-snapshot.pdf",
            location_type=SimpleNamespace(value="PDF_PAGE"),
            location_start=page,
        )

    page7 = candidate("page-7", 7)
    page16 = candidate("page-16", 16)
    dense_calls: list[str] = []
    reranker_calls: list[str] = []
    production_calls: list[dict[str, object]] = []

    def query_candidates(**kwargs: object) -> tuple[list[object], list[tuple[float, object]]]:
        dense_calls.append(str(kwargs["query"]))
        if kwargs["query"] == "original question":
            return [], [(0.1, page7)]
        return [], [(0.2, page16)]

    def rerank(*, query: str, contents: list[str]) -> list[float]:
        reranker_calls.append(query)
        return [0.9, 0.1] if query == "original question" else [0.1, 0.9]

    def retrieve(**kwargs: object) -> SimpleNamespace:
        production_calls.append(kwargs)
        return SimpleNamespace(items=[page16, page7])

    monkeypatch.setattr(acceptance, "_formal_document_ids", lambda **_kwargs: {"doc"})
    monkeypatch.setattr(acceptance, "_query_validated_candidates", query_candidates)
    monkeypatch.setattr(acceptance, "_build_archive_reranker_query_expression", lambda query: query)
    monkeypatch.setattr(acceptance, "score_archive_candidates", rerank)
    monkeypatch.setattr(
        acceptance,
        "retrieve_archive_answer_candidates",
        retrieve,
        raising=False,
    )

    result = acceptance._rank_query(
        session=session,
        user_id="owner",
        project_id="project",
        query="original question",
        case_id="WB-REV-LOAN-01",
        expected_lushan=None,
        supplementary_query="IBRD IDA",
    )

    assert dense_calls == ["original question", "IBRD IDA"]
    assert reranker_calls == ["original question", "IBRD IDA"]
    assert result["production_rerank"] == {
        "query_expression": "IBRD IDA",
        "candidate_count": 2,
        "reranker_calls": 1,
        "ranking": "supplementary",
    }
    assert result["target_page_ranks"] == {
        "original": {"7": 1, "16": 2},
        "supplementary": {"7": 2, "16": 1},
    }
    assert result["production_top8"]["matches_reconstructed"] is True
    assert len(production_calls) == 1
    assert {key: value for key, value in production_calls[0].items() if key != "session"} == {
        "user_id": "owner",
        "project_id": "project",
        "kb_id": "kb",
        "query": "original question",
        "observe_result": False,
        "gate_question": "original question",
    }
    assert production_calls[0]["session"] is session


def test_lushan_bare_rerank_reports_each_verified_amount_page_and_top8_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(owner_id="owner", kb_id="kb")

    class SessionStub:
        def get(self, *_args: object) -> object:
            return project

    candidates = [
        SimpleNamespace(
            chunk_id=f"amount-{index}",
            excerpt=f"金额证据页 {index}",
            filename=filename,
            location_type=SimpleNamespace(value="PDF_PAGE"),
            location_start=page,
        )
        for index, (filename, page) in enumerate(acceptance._LUSHAN_AMOUNT_SUPPORT_PAGES)
    ]
    routes = iter([candidates, []])

    monkeypatch.setattr(acceptance, "_formal_document_ids", lambda **_kwargs: {"doc"})
    monkeypatch.setattr(
        acceptance,
        "_query_validated_candidates",
        lambda **_kwargs: ([], [(float(index), item) for index, item in enumerate(next(routes))]),
    )
    monkeypatch.setattr(acceptance, "_build_archive_reranker_query_expression", lambda query: query)
    monkeypatch.setattr(acceptance, "score_archive_candidates", lambda **_kwargs: [1, 0, 0, 0, 0])
    monkeypatch.setattr(
        acceptance,
        "retrieve_archive_answer_candidates",
        lambda **_kwargs: SimpleNamespace(items=candidates),
        raising=False,
    )
    monkeypatch.setattr(acceptance, "_candidate_matches_expected_evidence", lambda **_kwargs: False)
    monkeypatch.setattr(acceptance, "_candidate_publicly_covers_expected_evidence", lambda **_kwargs: False)

    result = acceptance._rank_query(
        session=SessionStub(),
        user_id="owner",
        project_id="project",
        query="贷款批准金额是多少？",
        case_id="LUSHAN-01",
        expected_lushan={"relative_path": "fixed-target.pdf"},
        supplementary_query="IBRD IDA",
    )

    coverage = result["verified_amount_page_coverage"]["supplementary"]
    assert [(row["file"], row["page"], row["rank"], row["in_top8"]) for row in coverage["pages"]] == [
        ("2016_project_appraisal_document.pdf", 1, 1, True),
        ("2016_project_appraisal_document.pdf", 16, 2, True),
        ("2022_restructuring_paper.pdf", 4, 3, True),
        ("2024_completion_report.pdf", 1, 4, True),
        ("2024_completion_report_review.pdf", 1, 5, True),
    ]
    assert coverage["top8_count"] == 5
    assert result["production_rerank"]["reranker_calls"] == 1
    assert result["production_top8"]["matches_reconstructed"] is True


def test_actual_production_retrieval_runs_once_with_original_gate_question_without_supplement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(owner_id="owner", kb_id="kb")
    item = SimpleNamespace(
        chunk_id="actual-production-chunk",
        excerpt="时间口径证据",
        filename="belarus-snapshot.pdf",
        location_type=SimpleNamespace(value="PDF_PAGE"),
        location_start=16,
    )
    calls: list[dict[str, object]] = []

    class SessionStub:
        def get(self, *_args: object) -> object:
            return project

    session = SessionStub()

    def retrieve(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(items=[item])

    monkeypatch.setattr(acceptance, "_formal_document_ids", lambda **_kwargs: {"doc"})
    monkeypatch.setattr(acceptance, "_query_validated_candidates", lambda **_kwargs: ([], [(0.1, item)]))
    monkeypatch.setattr(acceptance, "_build_archive_reranker_query_expression", lambda query: query)
    monkeypatch.setattr(acceptance, "score_archive_candidates", lambda **_kwargs: [0.9])
    monkeypatch.setattr(acceptance, "retrieve_archive_answer_candidates", retrieve, raising=False)

    result = acceptance._rank_query(
        session=session,
        user_id="owner",
        project_id="project",
        query="白俄罗斯项目截至何时提款？",
        case_id="WB-ENTITY-NEG-01",
        expected_lushan=None,
        supplementary_query=None,
    )

    assert len(calls) == 1
    assert {key: value for key, value in calls[0].items() if key != "session"} == {
        "user_id": "owner",
        "project_id": "project",
        "kb_id": "kb",
        "query": "白俄罗斯项目截至何时提款？",
        "observe_result": False,
        "gate_question": "白俄罗斯项目截至何时提款？",
    }
    assert calls[0]["session"] is session
    assert result["production_top8"]["candidate_keys"] == [
        acceptance._safe_candidate_key("actual-production-chunk")
    ]
    assert result["production_top8"]["matches_reconstructed"] is True
    assert "production_service" in result["latency_ms"]


class _SerializableCandidate:
    """提供检索脚本所需字段并记录 JSON 序列化模式的合成候选。"""

    def __init__(self, chunk_id: str, page: int) -> None:
        self.chunk_id = chunk_id
        self.excerpt = f"合成摘录 {chunk_id}"
        self.filename = "synthetic.pdf"
        self.location_type = SimpleNamespace(value="PDF_PAGE")
        self.location_start = page
        self.location_end = page

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "chunk_id": self.chunk_id,
            "excerpt": self.excerpt,
            "filename": self.filename,
            "location_type": self.location_type.value,
            "location_start": self.location_start,
            "location_end": self.location_end,
        }


def _patch_capture_ranking(
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[_SerializableCandidate],
    production_items: list[_SerializableCandidate],
) -> object:
    project = SimpleNamespace(owner_id="owner", kb_id="kb")

    class SessionStub:
        def get(self, *_args: object) -> object:
            return project

    monkeypatch.setattr(acceptance, "_formal_document_ids", lambda **_kwargs: {"doc"})
    monkeypatch.setattr(
        acceptance,
        "_query_validated_candidates",
        lambda **_kwargs: ([], [(float(index), item) for index, item in enumerate(candidates)]),
    )
    monkeypatch.setattr(acceptance, "_build_archive_reranker_query_expression", lambda query: query)
    monkeypatch.setattr(
        acceptance,
        "score_archive_candidates",
        lambda *, query, contents: (
            list(range(len(contents))) if query == "IBRD IDA" else list(range(len(contents), 0, -1))
        ),
    )
    monkeypatch.setattr(acceptance, "_candidate_matches_expected_evidence", lambda **_kwargs: False)
    monkeypatch.setattr(acceptance, "_candidate_publicly_covers_expected_evidence", lambda **_kwargs: False)
    monkeypatch.setattr(
        acceptance,
        "retrieve_archive_answer_candidates",
        lambda **_kwargs: SimpleNamespace(items=production_items),
        raising=False,
    )
    return SessionStub()


def test_rank_query_captures_both_rankings_from_the_same_world_bank_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_SerializableCandidate(f"chunk-{index}", index + 1) for index in range(8)]
    # “original” 降序为 chunk-0..7，补充排序逆序；生产接口采用补充排序。
    supplementary_order = list(reversed(candidates))
    session = _patch_capture_ranking(monkeypatch, candidates, supplementary_order)
    captured: dict[str, list[dict[str, object]]] = {}

    acceptance._rank_query(
        session=session,
        user_id="owner",
        project_id="project",
        query="世界银行贷款金额是多少？",
        case_id="WB-REV-LOAN-01",
        expected_lushan=None,
        supplementary_query="IBRD IDA",
        candidate_capture=captured,
    )

    assert captured == {
        "WB-REV-LOAN-01/original": [item.model_dump(mode="json") for item in candidates],
        "WB-REV-LOAN-01/supplementary": [
            item.model_dump(mode="json") for item in supplementary_order
        ],
    }


@pytest.mark.parametrize(
    ("case_id", "project_id", "supplementary_query"),
    [
        ("LUSHAN-02", "lushan", None),
        ("LUSHAN-03", "lushan", None),
        ("WB-CONTEXT-NONLOAN-01", "belarus", None),
        ("WB-ENTITY-NEG-01", "belarus", None),
        ("WB-UNSUPPORTED-DRAW-01", "belarus", None),
        ("WB-UNSUPPORTED-DRAW-GATED-01", "belarus", "IBRD IDA"),
    ],
)
def test_rank_query_captures_original_top8_for_pixie_expansion_cases(
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    project_id: str,
    supplementary_query: str | None,
) -> None:
    candidates = [_SerializableCandidate(f"{case_id}-{index}", index + 1) for index in range(10)]
    production_items = list(reversed(candidates[2:])) if supplementary_query else candidates[:8]
    session = _patch_capture_ranking(monkeypatch, candidates, production_items)
    captured: dict[str, list[dict[str, object]]] = {}

    acceptance._rank_query(
        session=session,
        user_id="owner",
        project_id=project_id,
        query="合成问题",
        case_id=case_id,
        expected_lushan={"items": []} if case_id.startswith("LUSHAN-") else None,
        supplementary_query=supplementary_query,
        candidate_capture=captured,
    )

    assert captured == {
        f"{case_id}/original": [item.model_dump(mode="json") for item in candidates[:8]]
    }


def test_run_acceptance_passes_capture_mapping_without_putting_it_in_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[dict[str, object]]] = {
        "WB-REV-LOAN-01/original": [
            {"chunk_id": "synthetic-private", "excerpt": "合成私有摘录"}
        ]
    }
    result_file = tmp_path / "retrieval-summary.json"
    plan = [
        {
            "query_kind": "LUSHAN-01",
            "project_key": "lushan",
            "original_query": "世界银行贷款批准金额是多少？",
            "supplementary_query": "IBRD IDA",
        },
        {
            "query_kind": "WB-REV-LOAN-01",
            "project_key": "belarus",
            "original_query": "合成问题",
            "supplementary_query": None,
        }
    ]

    class SessionStub:
        def __enter__(self) -> "SessionStub":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _model: object, _project_id: object) -> SimpleNamespace:
            return SimpleNamespace(owner_id="owner", kb_id="isolated-kb")

    monkeypatch.setattr(acceptance, "_validate_result_file", lambda _path: None)
    monkeypatch.setattr(acceptance, "_assert_empty_targets", lambda: None)
    monkeypatch.setattr(
        acceptance,
        "verify_source_manifests",
        lambda: {
            "dataset": object(),
            "lushan_label": {"normal_documents": []},
        },
    )
    monkeypatch.setattr(acceptance, "_lushan_queries", lambda _dataset: {})
    monkeypatch.setattr(acceptance, "build_query_plan", lambda *_args: plan)
    monkeypatch.setattr(acceptance, "_make_combined_label", lambda _sources: "synthetic")
    monkeypatch.setattr(acceptance, "P14Api", lambda _url: object())
    monkeypatch.setattr(acceptance, "_register_tracked", lambda *_args, **kwargs: ("owner", "user"))
    monkeypatch.setattr(
        acceptance,
        "_seed_confirmed_documents",
        lambda _api, **kwargs: (
            kwargs["project_ids"].update(
                {
                    "lushan": "00000000-0000-0000-0000-000000000001",
                    "belarus": "00000000-0000-0000-0000-000000000002",
                }
            )
            or {"lushan": ["lushan.pdf"], "belarus": ["synthetic.pdf"]},
            None,
        ),
    )
    monkeypatch.setattr(
        acceptance,
        "_entry_map",
        lambda _dataset: {"LUSHAN-01": {"case_id": "LUSHAN-01"}},
    )
    expected = {
        "relative_path": "appraisal.pdf",
        "items": [
            {
                "location_type": "PDF_PAGE",
                "location_start": 16,
                "location_end": 16,
                "excerpt": "目标摘录",
            }
        ],
    }
    monkeypatch.setattr(acceptance, "_diagnostic_expected_evidence", lambda _entry: expected)
    monkeypatch.setattr(acceptance, "Session", lambda _engine: SessionStub())
    passed_mappings: list[object] = []

    def rank_query(**kwargs: object) -> dict[str, object]:
        passed_mappings.append(kwargs.get("candidate_capture"))
        return {"case_id": kwargs["case_id"]}

    monkeypatch.setattr(acceptance, "_rank_query", rank_query)
    monkeypatch.setattr(
        acceptance,
        "_run_graph_evidence_probe",
        lambda **_kwargs: {
            "tool_payload": {
                "found": True,
                "results": [
                    {
                        "filename": "appraisal.pdf",
                        "location_type": "PDF_PAGE",
                        "location_start": 16,
                        "location_end": 16,
                        "excerpt": "目标摘录覆盖内容",
                    }
                ],
            },
            "turn": SimpleNamespace(
                tool_call_count=1,
                tool_events=[SimpleNamespace(status="COMPLETED")],
            ),
            "agent_model_invocations": 2,
            "judge_model_invocations": 1,
        },
    )
    monkeypatch.setattr(acceptance, "_cleanup_and_verify", lambda **_kwargs: {"verified": True})

    summary = acceptance.run_world_bank_retrieval_acceptance(
        base_url="http://127.0.0.1:8000",
        result_file=result_file,
        candidate_capture=captured,
    )

    assert passed_mappings == [captured, captured]
    assert "candidate_capture" not in summary
    assert summary["fr042_graph_probe"]["target_in_top8"] is True
    result_text = result_file.read_text(encoding="utf-8")
    assert "synthetic-private" not in result_text
    assert "合成私有摘录" not in result_text


def test_lushan04_is_no_answer_support_retrieval_case() -> None:
    item = SimpleNamespace(
        filename="unrelated.pdf",
        location_type=SimpleNamespace(value="PDF_PAGE"),
        location_start=1,
        location_end=1,
    )

    flags = acceptance.candidate_evidence_flags(
        case_id="LUSHAN-04", item=item, expected_lushan=None
    )

    assert flags == {
        "exact": False,
        "public": False,
        "retrieved_relevant_context": False,
        "supports_answer": False,
    }


def test_merge_and_evidence_mapping_are_deterministic_and_scoped() -> None:
    first = [
        (0.2, SimpleNamespace(chunk_id="chunk-a")),
        (0.3, SimpleNamespace(chunk_id="chunk-b")),
    ]
    second = [
        (0.1, SimpleNamespace(chunk_id="chunk-b")),
        (0.4, SimpleNamespace(chunk_id="chunk-c")),
    ]

    merged, first_ranks, second_ranks = acceptance.merge_candidate_pools(first, second)
    coverage = acceptance.score_evidence_coverage(
        [item for _, item in merged],
        {"chunk-a": True, "chunk-c": True},
    )

    assert [item.chunk_id for _, item in merged] == ["chunk-b", "chunk-a", "chunk-c"]
    assert first_ranks == {"chunk-a": 1, "chunk-b": 2}
    assert second_ranks == {"chunk-b": 1, "chunk-c": 2}
    assert coverage == {"candidate_count": 3, "target_count": 2, "target_ranks": [2, 3]}


def test_exact_and_public_coverage_are_independent_and_negative_cases_do_not_support_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = SimpleNamespace(
        filename="belarus-snapshot.pdf",
        location_type=SimpleNamespace(value="PDF_PAGE"),
        location_start=16,
        location_end=16,
    )
    monkeypatch.setattr(acceptance, "_candidate_matches_expected_evidence", lambda **_kw: True)
    monkeypatch.setattr(acceptance, "_candidate_publicly_covers_expected_evidence", lambda **_kw: False)

    frozen = acceptance.candidate_evidence_flags(
        case_id="LUSHAN-01", item=item, expected_lushan={"relative_path": "target.pdf"}
    )
    ifc_negative = acceptance.candidate_evidence_flags(
        case_id="WB-ENTITY-NEG-01", item=item, expected_lushan=None
    )
    draw_negative = acceptance.candidate_evidence_flags(
        case_id="WB-UNSUPPORTED-DRAW-01", item=item, expected_lushan=None
    )

    assert frozen["exact"] is True
    assert frozen["public"] is False
    assert ifc_negative["retrieved_relevant_context"] is True
    assert ifc_negative["supports_answer"] is False
    assert draw_negative["retrieved_relevant_context"] is True
    assert draw_negative["supports_answer"] is False


def test_sanitized_result_omits_chunk_document_excerpt_and_resource_ids() -> None:
    result = acceptance.sanitize_retrieval_result(
        {
            "project_id": "project-private",
            "user_id": "user-private",
            "candidates": [
                {
                    "chunk_id": "chunk-private",
                    "document_id": "document-private",
                    "excerpt": "私有证据摘录",
                    "dense_rank": 1,
                    "dense_distance": 0.12,
                    "reranker_rank": 1,
                    "reranker_score": 0.91,
                    "target_evidence": True,
                    "source_page": 16,
                }
            ],
        }
    )
    serialized = str(result)

    for secret in (
        "project-private",
        "user-private",
        "chunk-private",
        "document-private",
        "私有证据摘录",
    ):
        assert secret not in serialized
    assert result["candidates"][0]["dense_rank"] == 1
    assert result["candidates"][0]["target_evidence"] is True


def test_cleanup_runs_and_verifies_all_four_storage_layers_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    api = object()

    monkeypatch.setattr(acceptance, "_cleanup_seeded_scope", lambda *_a, **_k: events.append("cleanup"))
    monkeypatch.setattr(acceptance, "_verify_zero_residuals", lambda: events.append("verify"))
    monkeypatch.setattr(acceptance, "_close_api", lambda _api: events.append("close"))
    monkeypatch.setattr(acceptance, "_delete_isolated_chroma_database", lambda: events.append("chroma-delete"))
    monkeypatch.setattr(acceptance, "_remove_local_run_targets", lambda: events.append("local-delete"))

    with pytest.raises(RuntimeError, match="seed failed"):
        acceptance.run_with_cleanup(
            api=api,
            user_id="00000000-0000-0000-0000-000000000001",
            seeded=[("project-a", "document-a")],
            project_ids={"lushan": "project-a"},
            operation=lambda: (_ for _ in ()).throw(RuntimeError("seed failed")),
        )

    assert events == ["cleanup", "close", "chroma-delete", "local-delete", "verify"]


def test_cleanup_records_every_failed_step_and_continues_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    api = object()

    def run_step(name: str, *, fail: bool = False) -> None:
        events.append(name)
        if fail:
            raise RuntimeError(f"sensitive diagnostic from {name}")

    monkeypatch.setattr(
        acceptance,
        "_cleanup_seeded_scope",
        lambda *_a, **_k: run_step("business", fail=True),
    )
    monkeypatch.setattr(acceptance, "_close_api", lambda _api: run_step("api"))
    monkeypatch.setattr(
        acceptance,
        "_delete_isolated_chroma_database",
        lambda: run_step("chroma", fail=True),
    )
    monkeypatch.setattr(
        acceptance,
        "_remove_local_run_targets",
        lambda: run_step("local", fail=True),
    )
    monkeypatch.setattr(
        acceptance,
        "_verify_zero_residuals",
        lambda: (run_step("verify"), {})[1],
    )

    with pytest.raises(acceptance.AcceptanceError) as error:
        acceptance._cleanup_and_verify(
            api=api,
            user_id=None,
            seeded=[],
            project_ids={},
        )

    assert events == ["api", "chroma", "local", "verify"]
    assert type(error.value) is acceptance.AcceptanceError
    assert error.value.cleanup_failed_steps == (
        "CHROMA_DATABASE_DELETE",
        "LOCAL_TARGET_DELETE",
    )


def test_cleanup_uses_static_codes_for_all_failure_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    api = object()

    def fail(name: str) -> None:
        events.append(name)
        raise RuntimeError(f"sensitive diagnostic from {name}")

    monkeypatch.setattr(
        acceptance,
        "_cleanup_seeded_scope",
        lambda *_a, **_k: fail("business"),
    )
    monkeypatch.setattr(acceptance, "_close_api", lambda _api: fail("api"))
    monkeypatch.setattr(
        acceptance, "_delete_isolated_chroma_database", lambda: fail("chroma")
    )
    monkeypatch.setattr(acceptance, "_remove_local_run_targets", lambda: fail("local"))
    monkeypatch.setattr(acceptance, "_verify_zero_residuals", lambda: fail("verify"))

    with pytest.raises(acceptance.AcceptanceError) as error:
        acceptance._cleanup_and_verify(
            api=api,
            user_id="00000000-0000-0000-0000-000000000001",
            seeded=[],
            project_ids={},
        )

    assert events == ["business", "api", "chroma", "local", "verify"]
    assert type(error.value) is acceptance.AcceptanceError
    assert error.value.cleanup_failed_steps == (
        "BUSINESS_SCOPE_CLEANUP",
        "API_CLOSE",
        "CHROMA_DATABASE_DELETE",
        "LOCAL_TARGET_DELETE",
        "ZERO_RESIDUAL_VERIFY",
    )
    assert "sensitive diagnostic" not in str(error.value)


def test_cleanup_wrapper_preserves_zero_residual_probe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = object()
    residuals = {
        "postgres_zero": True,
        "chroma_zero": False,
        "files_zero": True,
        "checkpoint_zero": True,
    }

    def fail_probe() -> None:
        error = acceptance.AcceptanceError("sensitive probe details")
        error.residual_probe_stage = "residuals.chroma_list"
        error.residual_probe_error_code = "SQLALCHEMY_DRIVER"
        error.zero_residuals = residuals
        raise error

    monkeypatch.setattr(acceptance, "_close_api", lambda _api: None)
    monkeypatch.setattr(acceptance, "_delete_isolated_chroma_database", lambda: None)
    monkeypatch.setattr(acceptance, "_remove_local_run_targets", lambda: None)
    monkeypatch.setattr(acceptance, "_verify_zero_residuals", fail_probe)

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance._cleanup_and_verify(
            api=api,
            user_id=None,
            seeded=[],
            project_ids={},
        )

    assert raised.value.cleanup_failed_steps == ("ZERO_RESIDUAL_VERIFY",)
    assert raised.value.residual_probe_stage == "residuals.chroma_list"
    assert raised.value.residual_probe_error_code == "SQLALCHEMY_DRIVER"
    assert raised.value.zero_residuals == residuals
    assert "sensitive probe details" not in str(raised.value)


@pytest.mark.parametrize(
    ("failed_stage", "expected_stage"),
    [
        ("empty", "prepare.empty_target.postgres_count"),
        ("sources", "prepare.source_validation"),
        ("identity", "prepare.identity_registration"),
        ("seed", "prepare.document_seeding"),
        ("rank", "prepare.candidate_query_ranking"),
    ],
)
def test_retrieval_preparation_failure_reports_only_safe_stage(
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: str,
    expected_stage: str,
) -> None:
    from uuid import UUID

    class FakeSession:
        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fail_if(stage: str) -> None:
        if failed_stage == stage:
            error = RuntimeError("sensitive preparation failure")
            if stage == "empty":
                error.retrieval_stage = "prepare.empty_target.postgres_count"
                error.preflight_error_code = "UNKNOWN"
            raise error

    monkeypatch.setattr(acceptance, "_validate_result_file", lambda *_args: None)
    monkeypatch.setattr(acceptance, "_assert_empty_targets", lambda: fail_if("empty"))
    monkeypatch.setattr(
        acceptance,
        "verify_source_manifests",
        lambda: (fail_if("sources"), {
            "dataset": {}, "lushan_label": {"normal_documents": []},
        })[1],
    )
    monkeypatch.setattr(acceptance, "_lushan_queries", lambda _dataset: [])
    monkeypatch.setattr(
        acceptance,
        "build_query_plan",
        lambda *_args: [{
            "query_kind": "WB-CONTEXT-NONLOAN-01",
            "project_key": "lushan",
            "original_query": "safe test question",
            "supplementary_query": None,
        }],
    )
    monkeypatch.setattr(acceptance, "_make_combined_label", lambda _sources: "fixture")
    monkeypatch.setattr(acceptance, "P14Api", lambda _url: object())

    def register(_api: object, *, on_registered, **_kwargs: object):
        fail_if("identity")
        identity = UUID("00000000-0000-0000-0000-000000000001")
        on_registered(identity)
        return identity, "fixture-user"

    monkeypatch.setattr(acceptance, "_register_tracked", register)

    def seed(_api: object, *, project_ids: dict[str, str], seeded: list, **_kwargs: object):
        fail_if("seed")
        project_id = "00000000-0000-0000-0000-000000000002"
        project_ids["lushan"] = project_id
        seeded.append((project_id, "fixture-document"))
        return {"lushan": []}, None

    monkeypatch.setattr(acceptance, "_seed_confirmed_documents", seed)
    monkeypatch.setattr(acceptance, "_entry_map", lambda _dataset: {})
    monkeypatch.setattr(acceptance, "Session", lambda _engine: FakeSession())
    monkeypatch.setattr(
        acceptance,
        "_rank_query",
        lambda **_kwargs: fail_if("rank"),
    )
    monkeypatch.setattr(
        acceptance,
        "_cleanup_and_verify",
        lambda **_kwargs: {
            "postgres_zero": True, "chroma_zero": True,
            "files_zero": True, "checkpoint_zero": True,
        },
    )

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance.run_world_bank_retrieval_acceptance(base_url="http://127.0.0.1:8000")

    assert raised.value.retrieval_stage == expected_stage
    if failed_stage == "empty":
        assert raised.value.preflight_error_code == "UNKNOWN"
    assert "sensitive preparation failure" not in str(raised.value)


def test_runner_preserves_primary_substage_and_terminal_cleanup_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    class FakeSession:
        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    primary_residuals = {
        "postgres_zero": True, "chroma_zero": False,
        "files_zero": True, "checkpoint_zero": True,
    }
    cleanup_residuals = {
        "postgres_zero": False, "chroma_zero": True,
        "files_zero": True, "checkpoint_zero": True,
    }
    primary_steps = ("BUSINESS_SCOPE_CLEANUP",)
    cleanup_steps = ("API_CLOSE", "ZERO_RESIDUAL_VERIFY")

    monkeypatch.setattr(acceptance, "_validate_result_file", lambda *_args: None)
    monkeypatch.setattr(acceptance, "_assert_empty_targets", lambda: None)
    monkeypatch.setattr(
        acceptance,
        "verify_source_manifests",
        lambda: {"dataset": {}, "lushan_label": {"normal_documents": []}},
    )
    monkeypatch.setattr(acceptance, "_lushan_queries", lambda _dataset: [])
    monkeypatch.setattr(acceptance, "build_query_plan", lambda *_args: [{
        "query_kind": "WB-CONTEXT-NONLOAN-01",
        "project_key": "lushan",
        "original_query": "safe test question",
        "supplementary_query": None,
    }])
    monkeypatch.setattr(acceptance, "_make_combined_label", lambda _sources: "fixture")
    monkeypatch.setattr(acceptance, "P14Api", lambda _url: object())

    def register(_api: object, *, on_registered, **_kwargs: object):
        user_id = UUID("00000000-0000-0000-0000-000000000001")
        on_registered(user_id)
        return user_id, "fixture-user"

    monkeypatch.setattr(acceptance, "_register_tracked", register)

    def seed(_api: object, *, project_ids: dict[str, str], seeded: list, **_kwargs: object):
        project_id = "00000000-0000-0000-0000-000000000002"
        project_ids["lushan"] = project_id
        seeded.append((project_id, "fixture-document"))
        return {"lushan": []}, None

    monkeypatch.setattr(acceptance, "_seed_confirmed_documents", seed)
    monkeypatch.setattr(acceptance, "_entry_map", lambda _dataset: {})
    monkeypatch.setattr(acceptance, "Session", lambda _engine: FakeSession())

    def fail_ranking(**_kwargs: object) -> None:
        error = acceptance.AcceptanceError("sensitive primary failure")
        error.retrieval_stage = "prepare.empty_target.postgres_count"
        error.preflight_error_code = "SQLALCHEMY_QUERY"
        error.residual_probe_error_code = "SQLALCHEMY_CONNECTION"
        error.cleanup_failed_steps = primary_steps
        error.residual_probe_stage = "residuals.chroma_list"
        error.zero_residuals = primary_residuals
        raise error

    def fail_cleanup(**_kwargs: object) -> None:
        error = acceptance.AcceptanceError("sensitive cleanup failure")
        error.retrieval_stage = "cleanup"
        error.preflight_error_code = "FILESYSTEM"
        error.residual_probe_error_code = "FILESYSTEM"
        error.cleanup_failed_steps = cleanup_steps
        error.residual_probe_stage = "residuals.files_check"
        error.zero_residuals = cleanup_residuals
        raise error

    monkeypatch.setattr(acceptance, "_rank_query", fail_ranking)
    monkeypatch.setattr(acceptance, "_cleanup_and_verify", fail_cleanup)

    with pytest.raises(acceptance.AcceptanceError) as raised:
        acceptance.run_world_bank_retrieval_acceptance(base_url="http://127.0.0.1:8000")

    assert raised.value.retrieval_stage == "prepare.empty_target.postgres_count"
    assert raised.value.preflight_error_code == "SQLALCHEMY_QUERY"
    assert raised.value.residual_probe_error_code == "FILESYSTEM"
    assert raised.value.cleanup_failed_steps == cleanup_steps
    assert raised.value.residual_probe_stage == "residuals.files_check"
    assert raised.value.zero_residuals == cleanup_residuals
    assert "sensitive" not in str(raised.value)


def test_safe_failure_copy_preserves_only_seed_operation_and_http_status() -> None:
    source = acceptance.AcceptanceError(
        "sensitive", safe_code="SEED_CONFIRM", http_status=503, api_code="UNSAFE_VALUE"
    )
    destination = acceptance.AcceptanceError("wrapped")

    acceptance._copy_safe_failure_diagnostics(
        destination, source, retrieval_stage="prepare.document_seeding"
    )

    assert destination.retrieval_operation_code == "SEED_CONFIRM"
    assert destination.retrieval_http_status == 503
    assert not hasattr(destination, "retrieval_api_code")
    assert "sensitive" not in str(destination)


def test_safe_failure_copy_drops_unknown_operation_and_invalid_http_status() -> None:
    source = acceptance.AcceptanceError(
        "sensitive", safe_code="secret", http_status=True
    )
    destination = acceptance.AcceptanceError("wrapped")

    acceptance._copy_safe_failure_diagnostics(destination, source)

    assert not hasattr(destination, "retrieval_operation_code")
    assert not hasattr(destination, "retrieval_http_status")


def test_chroma_cleanup_deletes_only_dedicated_collection_then_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chromadb

    events: list[tuple[str, object]] = []

    class CollectionClient:
        def delete_collection(self, *, name: str) -> None:
            events.append(("collection", name))

    class AdminClient:
        def delete_database(self, *, name: str, tenant: str) -> None:
            events.append(("database", (name, tenant)))

    monkeypatch.setattr(acceptance, "get_chroma_client", CollectionClient)
    admin_settings: list[object] = []

    def build_admin_client(*, settings: object) -> AdminClient:
        admin_settings.append(settings)
        return AdminClient()

    monkeypatch.setattr(chromadb, "AdminClient", build_admin_client)
    monkeypatch.setattr(
        acceptance.settings,
        "chroma_database",
        "fr042_wb_db_91af2c00",
    )
    monkeypatch.setattr(
        acceptance.settings,
        "chroma_final_collection",
        "fr042_wb_collection_91af2c00",
    )
    monkeypatch.setattr(acceptance.settings, "chroma_tenant", "isolated-tenant")

    acceptance._delete_isolated_chroma_database()

    assert events == [
        ("collection", "fr042_wb_collection_91af2c00"),
        ("database", ("fr042_wb_db_91af2c00", "isolated-tenant")),
    ]
    assert len(admin_settings) == 1
    assert admin_settings[0].chroma_server_host == acceptance.settings.chroma_host
    assert admin_settings[0].chroma_server_http_port == acceptance.settings.chroma_port
