"""验证芦山公开项目隔离持久层验收运行器的确定性契约。"""

import json
from statistics import median
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.models import EvidenceLocationType
from app.schemas.archive_retrieval import ArchiveRetrievalItemRead
from scripts import archive_lushan_persistence_acceptance as acceptance


ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "pixie_qa" / "datasets" / "lushan-p153548-smoke.json"
CORPUS_ROOT = (
    ROOT
    / "tests"
    / "pytest_docs"
    / "public_projects"
    / "lushan_earthquake_p153548"
)


def _dataset() -> dict[str, object]:
    """读取已提交的公开资料评测集。"""
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _manifest() -> dict[str, object]:
    """读取本地公开 PDF 来源清单。"""
    return json.loads(
        (CORPUS_ROOT / "metadata" / "manifest.json").read_text(encoding="utf-8")
    )


def _comparison_candidate(chunk_id: str = "private-chunk-id") -> ArchiveRetrievalItemRead:
    """构造含生产字段的请求内候选，不代表真实持久层记录。"""
    return ArchiveRetrievalItemRead(
        chunk_id=chunk_id,
        document_id=uuid4(),
        filename="2016_project_appraisal_document.pdf",
        location_type=EvidenceLocationType.PDF_PAGE,
        location_start=16,
        location_end=16,
        excerpt="IBRD Investment Project Financing US$300 million private excerpt",
        score=0.9,
    )


def test_build_document_label_uses_four_manifest_backed_original_pdfs() -> None:
    """持久层验收只导入问题实际依赖的四份原始 PDF。"""
    label = acceptance.build_document_label(_dataset(), _manifest())

    assert label["dataset_id"] == "lushan-p153548-persistence-v1"
    assert label["projects"] == [{"id": "lushan", "name": "芦山地震恢复重建项目"}]
    documents = label["normal_documents"]
    assert {Path(item["relative_path"]).name for item in documents} == {
        "2016_project_appraisal_document.pdf",
        "2022_restructuring_paper.pdf",
        "2024_completion_report.pdf",
        "2024_completion_report_review.pdf",
    }
    assert all(item["project_id"] == "lushan" for item in documents)
    assert all(item["expected_fields"]["TITLE"]["evidence"] for item in documents)
    assert all(item["expected_fields"]["DOCUMENT_TYPE"]["value"] == "OTHER" for item in documents)


def test_build_document_label_rejects_dataset_file_missing_from_manifest() -> None:
    """数据集引用未知文件时必须在任何外部写入前停止。"""
    dataset = _dataset()
    dataset["entries"][0]["eval_input"][1]["value"][0]["filename"] = "missing.pdf"

    with pytest.raises(acceptance.AcceptanceError, match="来源清单"):
        acceptance.build_document_label(dataset, _manifest())


def test_validate_retrieval_cases_counts_grounded_coverage_and_no_evidence() -> None:
    """真实检索聚合必须区分三条有据覆盖与一条无据探针。"""
    dataset = _dataset()
    responses = {
        "LUSHAN-01": {
            "items": [dataset["entries"][0]["eval_input"][1]["value"][0]],
            "requested_top_k": 8,
            "returned_count": 1,
        },
        "LUSHAN-02": {
            "items": [dataset["entries"][1]["eval_input"][1]["value"][0]],
            "requested_top_k": 8,
            "returned_count": 1,
        },
        "LUSHAN-03": {
            "items": [dataset["entries"][2]["eval_input"][1]["value"][0]],
            "requested_top_k": 8,
            "returned_count": 1,
        },
        "LUSHAN-04": {
            "items": [],
            "requested_top_k": 8,
            "returned_count": 0,
        },
    }

    result = acceptance.validate_retrieval_cases(dataset, responses)

    assert result == {
        "question_count": 4,
        "grounded_question_count": 3,
        "grounded_covered_count": 3,
        "no_evidence_question_count": 1,
    }


def test_validate_retrieval_cases_rejects_missing_grounded_evidence() -> None:
    """Top-8 未覆盖标准证据必须作为真实检索失败暴露。"""
    dataset = _dataset()
    responses = {
        case_id: {"items": [], "requested_top_k": 8, "returned_count": 0}
        for case_id in ("LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04")
    }

    with pytest.raises(acceptance.AcceptanceError, match="Top-8"):
        acceptance.validate_retrieval_cases(dataset, responses)


def test_summarize_retrieval_cases_keeps_safe_per_case_failures() -> None:
    """真实质量未通过时仍应保留案例编号级结果，供后续问答继续运行。"""
    dataset = _dataset()
    responses = {
        "LUSHAN-01": {
            "items": [dataset["entries"][0]["eval_input"][1]["value"][0]],
            "requested_top_k": 8,
            "returned_count": 1,
        },
        "LUSHAN-02": {"items": [], "requested_top_k": 8, "returned_count": 0},
        "LUSHAN-03": {"items": [], "requested_top_k": 8, "returned_count": 0},
        "LUSHAN-04": {"items": [], "requested_top_k": 8, "returned_count": 0},
    }

    result = acceptance.summarize_retrieval_cases(dataset, responses)

    assert result["grounded_covered_count"] == 1
    assert result["grounded_coverage_by_case"] == {
        "LUSHAN-01": True,
        "LUSHAN-02": False,
        "LUSHAN-03": False,
    }
    assert result["quality_passed"] is False


def test_summarize_retrieval_diagnostic_locates_expected_evidence_safely() -> None:
    """双排序诊断只保留标准证据排名和目标文档统计，不泄露候选内容。"""
    payload = {
        "chroma_candidate_count": 3,
        "candidate_count": 3,
        "reranker_query_mode": "c4_b",
        "candidates": [
            {
                "candidate_key": "a" * 64,
                "dense_rank": 1,
                "dense_distance": 0.1,
                "reranker_rank": 2,
                "reranker_score": 0.8,
                "matches_expected_evidence": False,
                "public_coverage_match": False,
                "candidate_kind": "SAME_DOCUMENT",
                "isolation_violation": False,
            },
            {
                "candidate_key": "b" * 64,
                "dense_rank": 2,
                "dense_distance": 0.2,
                "reranker_rank": 9,
                "reranker_score": 0.3,
                "matches_expected_evidence": True,
                "public_coverage_match": True,
                "candidate_kind": "SAME_DOCUMENT",
                "isolation_violation": False,
            },
            {
                "candidate_key": "c" * 64,
                "dense_rank": 3,
                "dense_distance": 0.3,
                "reranker_rank": 1,
                "reranker_score": 0.9,
                "matches_expected_evidence": False,
                "public_coverage_match": False,
                "candidate_kind": "OTHER_DOCUMENT",
                "isolation_violation": False,
            },
        ],
    }

    result = acceptance.summarize_retrieval_diagnostic("LUSHAN-01", payload)

    assert result == {
        "case_id": "LUSHAN-01",
        "chroma_candidate_count": 3,
        "candidate_count": 3,
        "reranker_query_mode": "c4_b",
        "expected_evidence_in_top30": True,
        "expected_dense_rank": 2,
        "expected_dense_distance": 0.2,
        "expected_reranker_rank": 9,
        "expected_reranker_score": 0.3,
        "expected_evidence_in_top8": False,
        "same_document_candidate_count": 2,
        "same_document_best_dense_rank": 1,
        "same_document_best_reranker_rank": 2,
        "isolation_violation": False,
    }
    assert "candidate_key" not in json.dumps(result)


def test_validate_agent_turn_requires_status_fragment_and_expected_citation() -> None:
    """FR-042 有据轮次必须同时满足状态、答案事实和来源引用。"""
    payload = {
        "answer_status": "ANSWERED",
        "answer": "贷款金额为 US$300 million。",
        "citations": [
            {
                "filename": "2016_project_appraisal_document.pdf",
                "location_type": "PDF_PAGE",
                "location_start": 16,
                "location_end": 16,
                "excerpt": "IBRD Investment Project Financing in the amount of US$300 million.",
            }
        ],
    }

    acceptance.validate_agent_turn(
        payload,
        expected_status="ANSWERED",
        expected_fragments=("US$300 million",),
        expected_filenames=("2016_project_appraisal_document.pdf",),
    )

    with pytest.raises(acceptance.AcceptanceError, match="引用"):
        acceptance.validate_agent_turn(
            {**payload, "citations": []},
            expected_status="ANSWERED",
            expected_fragments=("US$300 million",),
            expected_filenames=("2016_project_appraisal_document.pdf",),
        )


def test_validate_agent_turn_accepts_fixed_no_evidence_refusal() -> None:
    """无据轮次必须拒答且不返回引用。"""
    acceptance.validate_agent_turn(
        {
            "answer_status": "REFUSED_NO_EVIDENCE",
            "answer": "当前正式档案中没有足够依据回答这个问题。",
            "citations": [],
        },
        expected_status="REFUSED_NO_EVIDENCE",
        expected_fragments=(),
        expected_filenames=(),
    )


def test_assess_agent_turn_returns_only_safe_quality_dimensions() -> None:
    """回答诊断只保留状态与匹配布尔值，不回写正文或引用摘录。"""
    result = acceptance.assess_agent_turn(
        {
            "answer_status": "ANSWERED",
            "answer": "贷款金额为 US$300 million。",
            "citations": [{"filename": "source.pdf", "excerpt": "private text"}],
        },
        expected_status="ANSWERED",
        expected_fragments=("US$300 million",),
        expected_filenames=("source.pdf",),
    )

    assert result == {
        "observed_status": "ANSWERED",
        "status_matched": True,
        "answer_fragments_matched": True,
        "citations_matched": True,
        "refusal_citations_empty": True,
        "passed": True,
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "private text" not in serialized
    assert "US$300 million" not in serialized


def test_wrap_stage_error_preserves_only_safe_diagnostic_fields() -> None:
    """失败诊断应保留阶段和稳定错误码，但不得拼接请求路径。"""
    source = acceptance.AcceptanceError(
        "POST /projects/private-id failed",
        safe_code="SEED_PROJECT_CREATE",
        http_status=422,
        api_code="VALIDATION_ERROR",
    )

    wrapped = acceptance.wrap_stage_error("seed_confirmation", source)

    assert str(wrapped) == "P153548 验收阶段失败：seed_confirmation。"
    assert wrapped.safe_code == "SEED_PROJECT_CREATE"
    assert wrapped.http_status == 422
    assert wrapped.api_code == "VALIDATION_ERROR"
    assert "private-id" not in str(wrapped)


def test_p14_api_extracts_nested_application_error_code() -> None:
    """验收客户端应读取当前统一错误包装中的稳定业务码。"""
    api = acceptance.P14Api("http://fixture")
    api._client.close()
    api._client = httpx.Client(
        base_url="http://fixture",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                500,
                json={"error": {"code": "FILE_SAVE_FAILED", "message": "不可见细节"}},
            )
        ),
    )

    with pytest.raises(acceptance.AcceptanceError) as error:
        api.request("POST", "/upload", expected_statuses=(201,), payload={})

    api.close()
    assert error.value.api_code == "FILE_SAVE_FAILED"


def test_merge_candidate_pools_deduplicates_by_chunk_id_and_keeps_each_dense_rank() -> None:
    """双路召回合并按 Chunk 去重，但不能丢失各自的 dense 名次。"""
    original = [(0.1, "same"), (0.2, "original-only")]
    supplementary = [(0.05, "same"), (0.3, "supplementary-only")]

    merged, original_ranks, supplementary_ranks = acceptance._merge_candidate_pools(
        original, supplementary
    )

    assert [item[1] for item in merged] == ["same", "original-only", "supplementary-only"]
    assert original_ranks == {"same": 1, "original-only": 2}
    assert supplementary_ranks == {"same": 1, "supplementary-only": 2}


def test_rank_fusion_helpers_use_rrf_k10_and_equal_weight_minmax() -> None:
    """离线融合应使用固定 RRF k=10 和两路等权 min-max 分数。"""
    first = {"a": 0.9, "b": 0.2, "c": 0.1}
    second = {"a": 0.1, "b": 0.8, "c": 0.2}

    rrf = acceptance._reciprocal_rank_fusion(first, second, k=10)
    minmax = acceptance._minmax_fusion(first, second)

    assert sorted(rrf, key=rrf.get, reverse=True) == ["b", "a", "c"]
    assert sorted(minmax, key=minmax.get, reverse=True) == ["b", "a", "c"]
    assert minmax["a"] == pytest.approx(0.5)
    assert minmax["b"] == pytest.approx(0.5625)


def test_summarize_dual_rankings_reports_exact_and_public_top8_without_raw_ids() -> None:
    """双路摘要按去标识键报告各目标证据覆盖，不保存 Chunk ID 或正文。"""
    rankings = {
        "original": [
            {"chunk_id": "private-a", "coverage": {"LUSHAN-01": {"exact": False, "public": False}}},
            {"chunk_id": "private-b", "coverage": {"LUSHAN-01": {"exact": True, "public": True}}},
        ],
        "rrf_k10": [
            {"chunk_id": "private-b", "coverage": {"LUSHAN-01": {"exact": True, "public": True}}},
            {"chunk_id": "private-a", "coverage": {"LUSHAN-01": {"exact": False, "public": False}}},
        ],
    }

    result = acceptance._summarize_dual_rankings(rankings)

    assert result["rankings"]["original"]["top8_candidate_keys"][1] == acceptance._safe_candidate_key("private-b")
    assert result["rankings"]["original"]["target_evidence"]["LUSHAN-01"] == {
        "exact_rank": 2,
        "exact_in_top8": True,
        "public_rank": 2,
        "public_in_top8": True,
    }
    serialized = json.dumps(result)
    assert "private-a" not in serialized
    assert "private-b" not in serialized


def test_postgres_row_count_counts_every_table_in_current_schema_except_alembic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清理核验应覆盖当前隔离 Schema 全部业务表而非易漏的静态白名单。"""
    executed: list[str] = []

    class _Preparer:
        def quote(self, identifier: str) -> str:
            return f'"{identifier}"'

    class _Connection:
        dialect = type("_Dialect", (), {"identifier_preparer": _Preparer()})()

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: object) -> object:
            sql = str(statement)
            executed.append(sql)
            if "current_schema" in sql:
                return type("_Result", (), {"scalar_one": lambda _self: "isolated"})()
            table = sql.rsplit('"', 2)[-2]
            value = {"projects": 2, "future_business_table": 3}[table]
            return type("_Result", (), {"scalar_one": lambda _self: value})()

    class _Engine:
        def connect(self) -> _Connection:
            return _Connection()

    class _Inspector:
        def get_table_names(self, *, schema: str) -> list[str]:
            assert schema == "isolated"
            return ["alembic_version", "projects", "future_business_table"]

    monkeypatch.setattr(acceptance, "engine", _Engine())
    monkeypatch.setattr(acceptance, "inspect", lambda _engine: _Inspector())

    assert acceptance._postgres_row_count() == 5
    assert len(executed) == 3
    assert all("alembic_version" not in statement for statement in executed)


def test_isolation_configuration_rejects_defaults_and_requires_evidence_values(
    tmp_path: Path,
) -> None:
    """允许复用 tenant，但要求专用 Chroma Database/Collection 和隔离路径。"""
    isolated_config = {
        "embedding_context_mode": "evidence_values",
        "chroma_tenant": "mini_rag_tenant",
        "chroma_database": "lushan_test_database",
        "chroma_collection": "lushan_test_collection",
        "file_storage_path": tmp_path / "files",
        "checkpoint_path": tmp_path / "checkpoints.sqlite",
    }
    acceptance._validate_isolation_configuration(**isolated_config)

    with pytest.raises(acceptance.AcceptanceError, match="Chroma"):
        acceptance._validate_isolation_configuration(
            **{**isolated_config, "chroma_database": "mini_rag_chroma"}
        )
    with pytest.raises(acceptance.AcceptanceError, match="Chroma"):
        acceptance._validate_isolation_configuration(
            **{**isolated_config, "chroma_collection": "archive_final_chunks"}
        )

    with pytest.raises(acceptance.AcceptanceError, match="evidence_values"):
        acceptance._validate_isolation_configuration(
            **{**isolated_config, "embedding_context_mode": "values"}
        )


def test_supplementary_query_changes_only_institution_reference() -> None:
    """补充召回只替换机构指称，原始问题仍可独立用于质量问答。"""
    original = "世界银行贷款金额是多少？请保留原文货币格式。"

    supplementary = acceptance._supplementary_query(original)

    assert supplementary == "IBRD IDA贷款金额是多少？请保留原文货币格式。"
    assert original == "世界银行贷款金额是多少？请保留原文货币格式。"
    with pytest.raises(acceptance.AcceptanceError, match="不含预期世界银行指称"):
        acceptance._supplementary_query("项目金额是多少？")


def test_fusion_answer_groups_require_formal_baseline_order_and_build_five_top8s() -> None:
    """五组候选按冻结顺序构造，A 组必须等于正式单路排序。"""
    candidates = {key: SimpleNamespace(chunk_id=key) for key in "abcdefghij"}
    first_scores = {key: float(10 - index) for index, key in enumerate("abcdefghij")}
    second_scores = {key: float(index) for index, key in enumerate("abcdefghij")}

    groups = acceptance._build_fusion_answer_groups(
        original_candidates=[(float(index), candidates[key]) for index, key in enumerate("abcdefgh")],
        union_candidates=[(float(index), candidates[key]) for index, key in enumerate("abcdefghij")],
        original_scores=first_scores,
        supplementary_scores=second_scores,
        baseline_chunk_ids=list("abcdefgh"),
    )

    assert list(groups) == ["A", "B", "C", "D", "E"]
    assert all(len(items) == 8 for items in groups.values())
    assert [item.chunk_id for item in groups["A"]] == list("abcdefgh")
    assert [item.chunk_id for item in groups["C"]] == list("jihgfedc")
    with pytest.raises(acceptance.AcceptanceError, match="正式单路基线"):
        acceptance._build_fusion_answer_groups(
            original_candidates=[(float(index), candidates[key]) for index, key in enumerate("abcdefgh")],
            union_candidates=[(float(index), candidates[key]) for index, key in enumerate("abcdefghij")],
            original_scores=first_scores,
            supplementary_scores=second_scores,
            baseline_chunk_ids=list("bcdefgha"),
        )


def test_dual_retrieval_diagnostic_reports_current_chain_stage_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 Chroma 诊断应分别计入两次 dense、两次重排与融合后处理耗时。"""
    user_id = uuid4()
    project_id = uuid4()
    kb_id = uuid4()
    first = _comparison_candidate("first")
    second = _comparison_candidate("second")
    pools = iter(([(0.1, first)], [(0.2, second)]))
    monkeypatch.setattr(acceptance, "_formal_document_ids", lambda **_kwargs: [first.document_id, second.document_id])
    monkeypatch.setattr(acceptance, "_query_validated_candidates", lambda **_kwargs: (1, next(pools)))
    monkeypatch.setattr(
        acceptance,
        "score_archive_candidates",
        lambda *, query, contents: [0.2, 0.8] if "IBRD IDA" in query else [0.9, 0.1],
    )
    session = SimpleNamespace(
        get=lambda _model, _id: SimpleNamespace(owner_id=user_id, kb_id=kb_id)
    )

    result = acceptance._dual_retrieval_diagnostic(
        session=session,
        user_id=user_id,
        project_id=project_id,
        query="世界银行贷款金额是多少？",
        expected_evidence={"relative_path": first.filename, "items": []},
    )

    assert set(result["latency_ms"]) == {
        "original_dense", "supplementary_dense", "original_rerank",
        "supplementary_rerank", "fusion_postprocess",
    }
    assert all(value >= 0 for value in result["latency_ms"].values())


def test_fusion_answer_trials_use_original_query_fixed_rotation_and_no_retries() -> None:
    """回答对照固定执行 15 次原问题判定，结果只留脱敏指标。"""
    candidate = _comparison_candidate()
    groups = {letter: [_comparison_candidate(letter)] for letter in "ABCDE"}
    calls: list[tuple[str, tuple[str, ...]]] = []
    factory_calls: list[dict[str, int]] = []

    class _Model:
        def invoke(self, _prompt: str) -> object:
            return SimpleNamespace(
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            )

    def model_factory(**kwargs: int) -> _Model:
        factory_calls.append(kwargs)
        return _Model()

    def judge(*, question: str, candidates: list[object], model: object) -> object:
        calls.append((question, tuple(item.chunk_id for item in candidates)))
        model.invoke("private prompt")
        return SimpleNamespace(answer_status="ANSWERED", answer="US$300 million", citation_numbers=(1,))

    result = acceptance._run_fusion_answer_trials(
        query="原始世界银行问题",
        groups=groups,
        expected_evidence={"relative_path": candidate.filename, "items": [{
            "location_type": "PDF_PAGE", "location_start": 16, "location_end": 16,
            "excerpt": candidate.excerpt,
        }]},
        model_factory=model_factory,
        judge=judge,
        confirm_preflight=lambda value: value["A"]["scheduled_calls"] == 3,
    )

    assert factory_calls == [{"max_retries": 0}] * 15
    assert len(calls) == 15
    assert all(query == "原始世界银行问题" for query, _ in calls)
    assert [ids[0] for _, ids in calls] == list("ABCDE") + list("CDEAB") + list("EABCD")
    assert result["attempted_calls"] == 15
    assert result["round_order"] == [list("ABCDE"), list("CDEAB"), list("EABCD")]
    assert result["groups"]["A"]["frozen_annotation_pass_count"] == 3
    assert result["groups"]["A"]["source_support_pass_count"] == 3
    assert result["groups"]["A"]["trials"][0]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }
    assert result["groups"]["A"]["latency_median_ms"] == median(
        trial["latency_ms"] for trial in result["groups"]["A"]["trials"]
    )
    serialized = json.dumps(result, ensure_ascii=False)
    for forbidden in ("private-chunk-id", "private excerpt", "US$300 million", "原始世界银行问题"):
        assert forbidden not in serialized


def test_fusion_answer_trials_count_model_failures_without_retry_or_answer_persistence() -> None:
    """模型异常按原轮次记失败，不补跑且不保存异常详情。"""
    groups = {letter: [_comparison_candidate(letter)] for letter in "ABCDE"}
    attempts = 0

    class _Model:
        def invoke(self, _prompt: str) -> object:
            return SimpleNamespace()

    def judge(*, question: str, candidates: list[object], model: object) -> object:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("prompt and secret-bearing provider body")

    result = acceptance._run_fusion_answer_trials(
        query="original query",
        groups=groups,
        expected_evidence={},
        model_factory=lambda **_kwargs: _Model(),
        judge=judge,
        confirm_preflight=lambda _value: True,
    )

    assert attempts == result["attempted_calls"] == 15
    assert all(
        trial["status"] == "ERROR"
        for group in result["groups"].values()
        for trial in group["trials"]
    )
    assert "provider body" not in json.dumps(result)


def test_fusion_source_support_is_separate_from_frozen_annotation() -> None:
    """未命中冻结摘录但引用含金额时，保留来源支持的人工复核状态。"""
    candidate = _comparison_candidate("candidate")
    groups = {letter: [candidate] for letter in "ABCDE"}

    def judge(*, question: str, candidates: list[object], model: object) -> object:
        return SimpleNamespace(
            answer_status="ANSWERED", answer="USD 300 million", citation_numbers=(1,)
        )

    result = acceptance._run_fusion_answer_trials(
        query="question",
        groups=groups,
        expected_evidence={
            "relative_path": candidate.filename,
            "items": [{
                "location_type": "PDF_PAGE",
                "location_start": 1,
                "location_end": 1,
                "excerpt": "different frozen passage",
            }],
        },
        model_factory=lambda **_kwargs: SimpleNamespace(),
        judge=judge,
        confirm_preflight=lambda _value: True,
    )

    trial = result["groups"]["A"]["trials"][0]
    assert trial["fixed_amount_matched"] is False
    assert trial["frozen_annotation_pass"] is False
    assert trial["citation_amount_visible"] is True
    assert trial["source_support_status"] == "needs_manual_review"


def test_fusion_frozen_citation_uses_public_evidence_coverage_not_exact_chunk_text() -> None:
    """页级 Chunk 包含冻结摘录时，应按正式范围覆盖口径认可引用。"""
    candidate = _comparison_candidate()
    expected_evidence = {
        "relative_path": candidate.filename,
        "items": [{
            "location_type": "PDF_PAGE",
            "location_start": 16,
            "location_end": 16,
            "excerpt": "IBRD Investment Project Financing US$300 million",
        }],
    }
    assert not acceptance._candidate_matches_expected_evidence(
        item=candidate, expected_evidence=expected_evidence
    )
    assert acceptance._candidate_publicly_covers_expected_evidence(
        item=candidate, expected_evidence=expected_evidence
    )

    result = acceptance._run_fusion_answer_trials(
        query="芦山项目的世界银行贷款金额是多少？",
        groups={letter: [candidate] for letter in "ABCDE"},
        expected_evidence=expected_evidence,
        model_factory=lambda **_kwargs: SimpleNamespace(),
        judge=lambda **_kwargs: SimpleNamespace(
            answer_status="ANSWERED",
            answer="US$300 million",
            citation_numbers=(1,),
        ),
        confirm_preflight=lambda _value: True,
    )

    assert result["groups"]["A"]["exact_evidence_in_top8"] is False
    assert result["groups"]["A"]["public_evidence_in_top8"] is True
    assert result["groups"]["A"]["frozen_annotation_pass_count"] == 3
    assert result["groups"]["A"]["source_support_pass_count"] == 3


def test_fusion_answer_trials_require_preflight_confirmation_before_model_calls() -> None:
    """未确认公开证据外发时，预检结束后不得创建模型或调用判定层。"""
    groups = {letter: [_comparison_candidate(letter)] for letter in "ABCDE"}
    calls = 0

    def forbidden_factory(**_kwargs: int) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("模型工厂不得触发")

    with pytest.raises(acceptance.AcceptanceError, match="未获模型调用确认"):
        acceptance._run_fusion_answer_trials(
            query="original query",
            groups=groups,
            expected_evidence={},
            model_factory=forbidden_factory,
            confirm_preflight=lambda preflight: all(
                item["prompt_character_count"] > 0 for item in preflight.values()
            ) and False,
        )

    assert calls == 0


def test_fusion_pre_cleanup_requires_checkpoint_to_remain_empty() -> None:
    """融合模式跳过 FR-042 时，Checkpoint 零行是正确的验收证据。"""
    acceptance._validate_pre_cleanup_evidence(
        context_counts=[1, 2, 3, 4],
        chroma_count=4,
        file_count=4,
        checkpoint_count=0,
        fusion_answer_comparison=True,
    )

    with pytest.raises(acceptance.AcceptanceError, match="Checkpoint"):
        acceptance._validate_pre_cleanup_evidence(
            context_counts=[1, 2, 3, 4],
            chroma_count=4,
            file_count=4,
            checkpoint_count=1,
            fusion_answer_comparison=True,
        )
    with pytest.raises(acceptance.AcceptanceError):
        acceptance._validate_pre_cleanup_evidence(
            context_counts=[1, 2, 3, 4],
            chroma_count=4,
            file_count=4,
            checkpoint_count=0,
            fusion_answer_comparison=False,
        )


def test_fusion_runner_skips_legacy_model_routes_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """融合模式不请求 FR-039/FR-042，并在结束时走既有清理分支。"""
    user_id = uuid4()
    project_id = str(uuid4())
    candidate = _comparison_candidate("private-chunk")
    request_paths: list[str] = []
    cleanup_calls: list[str] = []
    resource_counts = {
        "chroma": iter((12, 0)),
        "files": iter((4, 0)),
        "checkpoint": iter((0, 0)),
    }

    class _Api:
        def __init__(self, _base_url: str) -> None:
            self.closed = False

        def request(self, _method: str, path: str, **_kwargs: object) -> dict[str, object]:
            request_paths.append(path)
            return {}

        def close(self) -> None:
            self.closed = True

    class _Session:
        def __enter__(self) -> "_Session":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _model: object, _identifier: object) -> object:
            return SimpleNamespace(owner_id=user_id, kb_id=uuid4())

    monkeypatch.setattr(acceptance, "_assert_empty_isolated_targets", lambda: None)
    monkeypatch.setattr(acceptance, "_read_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(acceptance, "build_document_label", lambda *_args: {})
    monkeypatch.setattr(
        acceptance,
        "_entry_map",
        lambda _dataset: {
            case_id: {"eval_metadata": {"category": "NO_EVIDENCE" if case_id.endswith("04") else "GROUNDED"}}
            for case_id in ("LUSHAN-01", "LUSHAN-02", "LUSHAN-03", "LUSHAN-04")
        },
    )
    monkeypatch.setattr(acceptance, "_question", lambda *_args, **_kwargs: "original query")
    monkeypatch.setattr(
        acceptance,
        "_diagnostic_expected_evidence",
        lambda _entry: {"relative_path": "source.pdf", "items": []},
    )
    monkeypatch.setattr(acceptance, "P14Api", _Api)
    monkeypatch.setattr(acceptance, "_register_and_login", lambda *_args, **_kwargs: (user_id, "unused"))

    def seed(_api: object, *, project_ids: dict[str, str], seeded: list[tuple[str, str]], **_kwargs: object) -> tuple[None, list[int]]:
        project_ids["lushan"] = project_id
        seeded.append(("project", "document"))
        return None, [1, 1, 1, 1]

    monkeypatch.setattr(acceptance, "_seed_confirmed_documents", seed)
    monkeypatch.setattr(acceptance, "_require_object", lambda value, **_kwargs: value)
    monkeypatch.setattr(acceptance, "summarize_retrieval_cases", lambda *_args: {"quality_passed": False})
    monkeypatch.setattr(acceptance, "summarize_retrieval_diagnostic", lambda *_args: {})
    monkeypatch.setattr(
        acceptance,
        "_dual_retrieval_diagnostic",
        lambda *, capture_internal, **_kwargs: capture_internal.update(
            {
                "original_candidates": [candidate],
                "union_candidates": [candidate],
                "union_distances": {candidate.chunk_id: 0.1},
                "original_scores": {candidate.chunk_id: 0.9},
                "supplementary_scores": {candidate.chunk_id: 0.8},
            }
        ) or {"safe": True},
    )
    monkeypatch.setattr(acceptance, "Session", lambda _engine: _Session())
    monkeypatch.setattr(acceptance, "retrieve_archive_answer_candidates", lambda **_kwargs: SimpleNamespace(items=[candidate]))

    def groups(**kwargs: object) -> dict[str, list[object]]:
        assert kwargs["baseline_chunk_ids"] == [candidate.chunk_id]
        return {name: [candidate] for name in "ABCDE"}

    monkeypatch.setattr(acceptance, "_build_fusion_answer_groups", groups)
    monkeypatch.setattr(
        acceptance,
        "_run_fusion_answer_trials",
        lambda **_kwargs: {"attempted_calls": 15},
    )
    monkeypatch.setattr(acceptance, "_existing_final_collection_count", lambda: next(resource_counts["chroma"]))
    monkeypatch.setattr(acceptance, "_stored_file_count", lambda _root: next(resource_counts["files"]))
    monkeypatch.setattr(acceptance, "_checkpoint_row_count", lambda _path: next(resource_counts["checkpoint"]))
    monkeypatch.setattr(acceptance, "_postgres_row_count", lambda: 0)
    monkeypatch.setattr(acceptance, "_cleanup_seeded_scope", lambda *_args, **_kwargs: cleanup_calls.append("cleaned"))

    result = acceptance.run_lushan_persistence_acceptance(
        base_url="http://127.0.0.1:8010",
        fusion_answer_comparison=True,
        confirm_fusion_preflight=lambda _value: True,
    )

    assert result["fusion_answer_comparison"] == {"attempted_calls": 15}
    assert result["quality_passed"] is None
    assert cleanup_calls == ["cleaned"]
    assert all("archive-questions" not in path and "agent-sessions" not in path for path in request_paths)
