"""验证 AV1-P02 正式 12 题问答门槛评测器。"""

import asyncio
from collections.abc import Mapping

from pixie import Evaluable


def _citation(*, excerpt: str = "北辰设计院", filename: str = "A-02-design.txt") -> dict[str, object]:
    """构造固定集评测使用的脱敏引用。"""
    return {
        "chunk_id": "a" * 64,
        "document_id": "11111111-1111-4111-8111-111111111111",
        "filename": filename,
        "location_type": "TEXT_LINE_RANGE",
        "location_start": 2,
        "location_end": 3,
        "excerpt": excerpt,
        "score": 0.91,
        "reranker_score": 0.88,
    }


def _evaluable(
    *,
    category: str,
    answer_status: str,
    answer: str,
    citations: list[Mapping[str, object]],
    candidate_pool_complete: bool = True,
    expected_answer: str | None = "北辰设计院",
    expected_answer_fragments: list[str] | None = None,
    expected_evidence: dict[str, object] | None = None,
    retrieval_latency_ms: float = 120.0,
    question_end_to_end_latency_ms: float | None = None,
    case_id: str = "GROUNDED-01",
) -> Evaluable:
    """构造一个不含运行时 Ground Truth 的离线评测载荷。"""
    output = [
        {
            "name": "archive_question_response",
            "value": {
                "answer_status": answer_status,
                "answer": answer,
                "citations": citations,
            },
        }
    ]
    if question_end_to_end_latency_ms is not None:
        output.append(
            {
                "name": "archive_question_end_to_end_latency_ms",
                "value": question_end_to_end_latency_ms,
            }
        )
    return Evaluable(
        eval_input=[{"name": "question", "value": "编制单位是什么？"}],
        eval_output=output,
        eval_metadata={
            "case_id": case_id,
            "category": category,
            "expected_answer": expected_answer,
            "expected_answer_fragments": expected_answer_fragments,
            "expected_evidence": expected_evidence,
            "candidate_pool_complete": candidate_pool_complete,
            "retrieval_latency_ms": retrieval_latency_ms,
        },
    )


def test_grounded_entry_requires_fact_and_matching_citation() -> None:
    """GROUNDED 必须回答、包含标注事实并引用匹配证据。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    result = archive_v1_p02_quality_gate(
        _evaluable(
            category="GROUNDED",
            answer_status="ANSWERED",
            answer="编制单位为北辰设计院。",
            citations=[_citation()],
            expected_evidence={
                "relative_path": "documents/A-02-design.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 2,
                        "location_end": 2,
                        "excerpt": "北辰设计院",
                    }
                ],
            },
        )
    )

    assert result.score == 1.0


def test_no_evidence_and_isolation_require_refusal_without_citations() -> None:
    """NO_EVIDENCE/ISOLATION 只能返回拒答和空引用。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    for category in ("NO_EVIDENCE", "ISOLATION"):
        result = archive_v1_p02_quality_gate(
            _evaluable(
                category=category,
                answer_status="REFUSED_NO_EVIDENCE",
                answer="正式档案中没有足够依据。",
                citations=[],
                expected_answer=None,
                expected_evidence=None,
            )
        )
        assert result.score == 1.0


def test_enterprise_grounded_answer_fragments_accept_actual_paraphrases() -> None:
    """企业四道已知表达应按全部必要事实片段通过，而不是要求整句连续相同。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    cases = (
        (
            "GROUNDED-04",
            "档案问答保留 Top-8 条候选证据。",
            "档案问答内部保留 Top-8 候选。",
            ["Top-8", "候选"],
        ),
        (
            "GROUNDED-06",
            "Chroma 检索的范围条件是 user_id 和 kb_id。",
            "Chroma 检索必须包含服务端确定的 user_id 和 kb_id。",
            ["Chroma", "user_id", "kb_id"],
        ),
        (
            "GROUNDED-11",
            "公开接口最多返回 10条候选。",
            "公开检索接口最多返回 10 条候选。",
            ["10条"],
        ),
        (
            "GROUNDED-12",
            "删除 Chroma 时必须包含 document_id。",
            "删除文档时 Chroma 条件必须额外包含 document_id。",
            ["document_id"],
        ),
    )
    for case_id, answer, expected_answer, fragments in cases:
        result = archive_v1_p02_quality_gate(
            _evaluable(
                category="GROUNDED",
                answer_status="ANSWERED",
                answer=answer,
                citations=[_citation()],
                expected_answer=expected_answer,
                expected_answer_fragments=fragments,
                case_id=case_id,
                expected_evidence={
                    "relative_path": "documents/A-02-design.txt",
                    "items": [
                        {
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 2,
                            "location_end": 2,
                            "excerpt": "北辰设计院",
                        }
                    ],
                },
            )
        )
        assert result.score == 1.0, result.reasoning


def test_grounded_answer_fragments_require_every_declared_fact() -> None:
    """片段标注不能只命中一个事实就放行。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    result = archive_v1_p02_quality_gate(
        _evaluable(
            category="GROUNDED",
            answer_status="ANSWERED",
            answer="档案问答保留 Top-8。",
            citations=[_citation()],
            expected_answer="档案问答内部保留 Top-8 候选。",
            expected_answer_fragments=["Top-8", "候选"],
            expected_evidence={
                "relative_path": "documents/A-02-design.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 2,
                        "location_end": 2,
                        "excerpt": "北辰设计院",
                    }
                ],
            },
        )
    )

    assert result.score < 0.5


def test_grounded_10_fragments_accept_all_three_observed_answers() -> None:
    """GROUNDED-10 的三种真实表达都必须保留五个必要事实片段。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    expected_answer = "原文件、PostgreSQL 文档和 Chroma Chunk 使用同一个 document_id 关联。"
    fragments = ["原文件", "PostgreSQL", "Chroma Chunk", "document_id", "关联"]
    answers = (
        "根据证据，原文件、PostgreSQL 文档和 Chroma Chunk 使用同一个 document_id 关联（S4），因此云港项目通过 document_id 将原文件、业务文档（PostgreSQL 文档）和向量片段（Chroma Chunk）关联起来。",
        "根据证据，原文件、PostgreSQL 文档和 Chroma Chunk（即业务文档和向量片段）使用同一个 document_id 关联（S4）。",
        "根据证据，原文件、PostgreSQL 文档和 Chroma Chunk 使用同一个 document_id 关联（S4）。",
    )
    for answer in answers:
        result = archive_v1_p02_quality_gate(
            _evaluable(
                category="GROUNDED",
                answer_status="ANSWERED",
                answer=answer,
                citations=[_citation()],
                expected_answer=expected_answer,
                expected_answer_fragments=fragments,
                case_id="GROUNDED-10",
                expected_evidence={
                    "relative_path": "documents/A-02-design.txt",
                    "items": [
                        {
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 2,
                            "location_end": 2,
                            "excerpt": "北辰设计院",
                        }
                    ],
                },
            )
        )
        assert result.score == 1.0, result.reasoning


def test_all_categories_fail_when_candidate_pool_is_incomplete() -> None:
    """候选池不完整时，无论单题回答如何都不能通过。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    result = archive_v1_p02_quality_gate(
        _evaluable(
            category="GROUNDED",
            answer_status="ANSWERED",
            answer="编制单位为北辰设计院。",
            citations=[_citation()],
            candidate_pool_complete=False,
            expected_evidence={
                "relative_path": "documents/A-02-design.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 2,
                        "location_end": 2,
                        "excerpt": "北辰设计院",
                    }
                ],
            },
        )
    )

    assert result.score < 0.5


def test_aggregate_helper_computes_the_twelve_question_gate() -> None:
    """聚合辅助应分别统计 8/2/2 类别并计算正式门槛。"""
    from evals.archive.evaluators import aggregate_archive_v1_p02_results

    entries: list[Evaluable] = []
    for index in range(8):
        entries.append(
            _evaluable(
                category="GROUNDED",
                answer_status="ANSWERED",
                answer="编制单位为北辰设计院。",
                citations=[_citation()],
                retrieval_latency_ms=100.0 + index,
                question_end_to_end_latency_ms=200.0 + index,
                case_id=f"GROUNDED-{index + 1:02d}",
                expected_evidence={
                    "relative_path": "documents/A-02-design.txt",
                    "items": [
                        {
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 2,
                            "location_end": 2,
                            "excerpt": "北辰设计院",
                        }
                    ],
                },
            )
        )
    for index, category in enumerate(
        ("NO_EVIDENCE", "NO_EVIDENCE", "ISOLATION", "ISOLATION"), start=1
    ):
        entries.append(
            _evaluable(
                category=category,
                answer_status="REFUSED_NO_EVIDENCE",
                answer="正式档案中没有足够依据。",
                citations=[],
                expected_answer=None,
                expected_evidence=None,
                case_id=f"{category}-{index:02d}",
                question_end_to_end_latency_ms=999.0,
            )
        )

    aggregate = aggregate_archive_v1_p02_results(entries)

    assert aggregate["question_count"] == 12
    assert aggregate["candidate_pool_complete_question_count"] == 12
    assert aggregate["grounded_answer_and_citation_count"] == 8
    assert aggregate["no_evidence_refusal_count"] == 2
    assert aggregate["isolation_refusal_count"] == 2
    assert aggregate["quality_gate_passed"] is True


def test_grounded_evaluator_preserves_case_id_and_rejects_missing_fact() -> None:
    """单题详情应保留脱敏 case_id，答案缺少标注事实时失败。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    result = archive_v1_p02_quality_gate(
        _evaluable(
            category="GROUNDED",
            answer_status="ANSWERED",
            answer="资料已完成归档。",
            citations=[_citation()],
            case_id="GROUNDED-08",
            expected_evidence={
                "relative_path": "documents/A-02-design.txt",
                "items": [
                    {
                        "location_type": "TEXT_LINE_RANGE",
                        "location_start": 2,
                        "location_end": 2,
                        "excerpt": "北辰设计院",
                    }
                ],
            },
        )
    )

    assert result.score < 0.5
    assert result.details["case_id"] == "GROUNDED-08"


def test_no_evidence_requires_the_fixed_refusal_text() -> None:
    """无据题即使状态和引用正确，也不能使用任意回答文案。"""
    from evals.archive.evaluators import archive_v1_p02_quality_gate

    result = archive_v1_p02_quality_gate(
        _evaluable(
            category="NO_EVIDENCE",
            answer_status="REFUSED_NO_EVIDENCE",
            answer="没有找到相关内容。",
            citations=[],
            expected_answer=None,
            expected_evidence=None,
        )
    )

    assert result.score < 0.5


def test_aggregate_rejects_duplicate_case_ids() -> None:
    """正式 12 题聚合必须要求 case_id 唯一且完整。"""
    from evals.archive.evaluators import aggregate_archive_v1_p02_results

    entries = [
        _evaluable(
            category="NO_EVIDENCE",
            answer_status="REFUSED_NO_EVIDENCE",
            answer="正式档案中没有足够依据。",
            citations=[],
            expected_answer=None,
            expected_evidence=None,
            case_id="NO_EVIDENCE-01",
        ),
        _evaluable(
            category="NO_EVIDENCE",
            answer_status="REFUSED_NO_EVIDENCE",
            answer="正式档案中没有足够依据。",
            citations=[],
            expected_answer=None,
            expected_evidence=None,
            case_id="NO_EVIDENCE-01",
        ),
    ]

    aggregate = aggregate_archive_v1_p02_results(entries)

    assert aggregate["unique_case_id_count"] == 1
    assert aggregate["quality_gate_passed"] is False


def test_aggregate_uses_question_end_to_end_latency_p95() -> None:
    """问答端到端 P95 应来自 Runnable 的 latency wrap，而非检索延迟。"""
    from evals.archive.evaluators import aggregate_archive_v1_p02_results

    entries: list[Evaluable] = []
    for index in range(8):
        entries.append(
            _evaluable(
                category="GROUNDED",
                answer_status="ANSWERED",
                answer="编制单位为北辰设计院。",
                citations=[_citation()],
                case_id=f"GROUNDED-{index + 1:02d}",
                retrieval_latency_ms=1.0,
                question_end_to_end_latency_ms=200.0 + index,
                expected_evidence={
                    "relative_path": "documents/A-02-design.txt",
                    "items": [
                        {
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 2,
                            "location_end": 2,
                            "excerpt": "北辰设计院",
                        }
                    ],
                },
            )
        )
    for index, category in enumerate(
        ("NO_EVIDENCE", "NO_EVIDENCE", "ISOLATION", "ISOLATION"), start=1
    ):
        entries.append(
            _evaluable(
                category=category,
                answer_status="REFUSED_NO_EVIDENCE",
                answer="正式档案中没有足够依据。",
                citations=[],
                expected_answer=None,
                expected_evidence=None,
                case_id=f"{category}-{index:02d}",
                retrieval_latency_ms=1.0,
                question_end_to_end_latency_ms=999.0,
            )
        )

    aggregate = aggregate_archive_v1_p02_results(entries)

    assert aggregate["retrieval_latency_p95_ms"] == 1.0
    assert aggregate["question_end_to_end_latency_p95_ms"] == 999.0
    assert "citations" not in aggregate


def test_runnable_records_end_to_end_latency(monkeypatch) -> None:
    """Runnable 应记录完整 answer_archive_question 调用耗时。"""
    from evals.archive import runnable as run_app

    observed: list[tuple[object, dict[str, object]]] = []

    def capture(value: object, **kwargs: object) -> object:
        observed.append((value, kwargs))
        return value

    monkeypatch.setattr(run_app, "eval_wrap", capture, raising=False)
    monkeypatch.setattr(run_app, "answer_archive_question", lambda **_: None)

    runnable = run_app.ArchiveQuestionRunnable.create()
    asyncio.run(runnable.run(run_app.ArchiveQuestionArgs(question="测试问题")))

    latency_events = [
        value
        for value, kwargs in observed
        if kwargs.get("name") == "archive_question_end_to_end_latency_ms"
    ]
    assert len(latency_events) == 1
    assert isinstance(latency_events[0], float)
