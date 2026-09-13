"""验证 D5 最小档案问答评测材料的离线契约。"""

from pathlib import Path

from pixie.harness.runner import load_dataset
from pixie.instrumentation.wrap import deserialize_wrap_data

from app.schemas.archive_retrieval import ArchiveRetrievalResponse


ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = ROOT / "evals" / "archive" / "datasets" / "archive-question-d5-smoke.json"


def test_d5_dataset_contains_four_question_only_entries() -> None:
    """数据集应只把问题交给 Runnable，并为输入边界准备候选。"""
    dataset = load_dataset(DATASET_PATH)

    assert len(dataset.entries) == 4
    assert all(set(entry.input_data) == {"question"} for entry in dataset.entries)
    for entry in dataset.entries:
        injected = next(
            item.value
            for item in entry.eval_input
            if item.name == "archive_question_retrieval"
        )
        retrieval = deserialize_wrap_data(injected)
        assert isinstance(retrieval, ArchiveRetrievalResponse)
        assert retrieval.returned_count == len(retrieval.items)


def test_d5_runnable_exposes_serial_semaphore() -> None:
    """Runnable 应串行调用同步生产问答服务，避免共享评测状态并发。"""
    from evals.archive.runnable import ArchiveQuestionRunnable

    runnable = ArchiveQuestionRunnable.create()

    assert runnable._semaphore._value == 1


def test_d5_contract_evaluator_checks_answer_and_decision_shape() -> None:
    """确定性评测器应同时检查回答、引用和证据充分性决策结构。"""
    from pixie import Evaluable
    from evals.archive.evaluators import archive_answer_contract

    evaluable = Evaluable(
        eval_input=[{"name": "question", "value": "归档状态是什么？"}],
        eval_output=[
            {
                "name": "archive_question_decision",
                "value": {"has_evidence": True, "retrieved_item_count": 1},
            },
            {
                "name": "archive_evidence_sufficiency_decision",
                "value": {
                    "strategy": "deepseek_structured_v1",
                    "sufficient": True,
                    "selected_citation_numbers": [1],
                    "retrieved_item_count": 1,
                },
            },
            {
                "name": "archive_question_response",
                "value": {
                    "answer_status": "ANSWERED",
                    "answer": "归档状态为 CONFIRMED。[S1]",
                    "citations": [
                        {
                            "chunk_id": "a" * 64,
                            "document_id": "00000000-0000-4000-8000-0000000000d5",
                            "filename": "PX-ALPHA.txt",
                            "location_type": "TEXT_LINE_RANGE",
                            "location_start": 1,
                            "location_end": 1,
                            "excerpt": "PX-ALPHA archive status: CONFIRMED.",
                            "score": 0.9,
                        }
                    ],
                },
            },
        ],
    )

    result = archive_answer_contract(evaluable)

    assert result.score == 1.0


def _citation(chunk_id: str) -> dict[str, object]:
    """构造结构完整的脱敏引用，供确定性契约测试复用。"""
    return {
        "chunk_id": chunk_id,
        "document_id": "00000000-0000-4000-8000-0000000000d5",
        "filename": "PX-ALPHA.txt",
        "location_type": "TEXT_LINE_RANGE",
        "location_start": 1,
        "location_end": 1,
        "excerpt": "PX-ALPHA archive status: CONFIRMED.",
        "score": 0.9,
    }


def test_d5_contract_allows_two_candidates_with_one_selected_citation() -> None:
    """候选数是检索结果数，不应强制等于最终回答引用数。"""
    from pixie import Evaluable
    from evals.archive.evaluators import archive_answer_contract

    evaluable = Evaluable(
        eval_input=[{"name": "question", "value": "责任单位是什么？"}],
        eval_output=[
            {
                "name": "archive_question_decision",
                "value": {"has_evidence": True, "retrieved_item_count": 2},
            },
            {
                "name": "archive_evidence_sufficiency_decision",
                "value": {
                    "strategy": "deepseek_structured_v1",
                    "sufficient": True,
                    "selected_citation_numbers": [1],
                    "retrieved_item_count": 2,
                },
            },
            {
                "name": "archive_question_response",
                "value": {
                    "answer_status": "ANSWERED",
                    "answer": "责任单位为 North Star Build Lab。[S1]",
                    "citations": [_citation("a" * 64)],
                },
            },
        ],
    )

    assert archive_answer_contract(evaluable).score == 1.0


def test_d5_contract_allows_nonempty_candidates_but_insufficient_refusal() -> None:
    """候选非空但模型判定证据不足时，拒答和空引用是合法结果。"""
    from pixie import Evaluable
    from evals.archive.evaluators import archive_answer_contract

    evaluable = Evaluable(
        eval_input=[{"name": "question", "value": "预算总额是多少？"}],
        eval_output=[
            {
                "name": "archive_question_decision",
                "value": {"has_evidence": True, "retrieved_item_count": 1},
            },
            {
                "name": "archive_evidence_sufficiency_decision",
                "value": {
                    "strategy": "deepseek_structured_v1",
                    "sufficient": False,
                    "selected_citation_numbers": [],
                    "retrieved_item_count": 1,
                },
            },
            {
                "name": "archive_question_response",
                "value": {
                    "answer_status": "REFUSED_NO_EVIDENCE",
                    "answer": "正式档案中没有足够依据。",
                    "citations": [],
                },
            },
        ],
    )

    assert archive_answer_contract(evaluable).score == 1.0


def _contract_evaluable(
    *,
    response: dict[str, object],
    decision: dict[str, object],
    sufficiency: dict[str, object],
) -> object:
    """构造确定性契约评测输入，集中保持三个观测点的名称一致。"""
    from pixie import Evaluable

    return Evaluable(
        eval_input=[{"name": "question", "value": "测试问题"}],
        eval_output=[
            {"name": "archive_question_decision", "value": decision},
            {
                "name": "archive_evidence_sufficiency_decision",
                "value": sufficiency,
            },
            {"name": "archive_question_response", "value": response},
        ],
    )


def test_d5_contract_rejects_selected_numbers_count_mismatch() -> None:
    """ANSWERED 的最终引用数不匹配 selected 数量时必须失败。"""
    from evals.archive.evaluators import archive_answer_contract

    evaluable = _contract_evaluable(
        decision={"has_evidence": True, "retrieved_item_count": 2},
        sufficiency={
            "sufficient": True,
            "selected_citation_numbers": [1, 2],
            "retrieved_item_count": 2,
        },
        response={
            "answer_status": "ANSWERED",
            "answer": "仅引用一条。[S1]",
            "citations": [_citation("a" * 64)],
        },
    )

    assert archive_answer_contract(evaluable).score < 0.5


def test_d5_contract_rejects_refusal_with_citations_or_selected_numbers() -> None:
    """REFUSED_NO_EVIDENCE 带引用或 selected 编号时必须失败。"""
    from evals.archive.evaluators import archive_answer_contract

    evaluable = _contract_evaluable(
        decision={"has_evidence": True, "retrieved_item_count": 1},
        sufficiency={
            "sufficient": False,
            "selected_citation_numbers": [1],
            "retrieved_item_count": 1,
        },
        response={
            "answer_status": "REFUSED_NO_EVIDENCE",
            "answer": "正式档案中没有足够依据。",
            "citations": [_citation("b" * 64)],
        },
    )

    assert archive_answer_contract(evaluable).score < 0.5


def test_d5_contract_rejects_answered_when_sufficiency_is_false() -> None:
    """sufficient=false 时不得返回 ANSWERED。"""
    from evals.archive.evaluators import archive_answer_contract

    evaluable = _contract_evaluable(
        decision={"has_evidence": True, "retrieved_item_count": 1},
        sufficiency={
            "sufficient": False,
            "selected_citation_numbers": [],
            "retrieved_item_count": 1,
        },
        response={
            "answer_status": "ANSWERED",
            "answer": "预算为 100。[S1]",
            "citations": [_citation("c" * 64)],
        },
    )

    assert archive_answer_contract(evaluable).score < 0.5
