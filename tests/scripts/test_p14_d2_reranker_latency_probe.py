"""P14-D2 Reranker 延迟探针的确定性行为测试。"""

from scripts.p14_d2_reranker_latency_probe import (
    build_fixed_pairs,
    build_prediction_kwargs,
    percentile,
)


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([100.0, 200.0, 300.0, 400.0], 0.95) == 385.0


def test_default_prediction_kwargs_do_not_override_model_processing() -> None:
    assert build_prediction_kwargs(None) == {}


def test_short_prediction_kwargs_limit_text_tokens() -> None:
    assert build_prediction_kwargs(256) == {
        "processing_kwargs": {
            "text": {"max_length": 256, "truncation": True},
        }
    }


def test_fixed_pairs_have_one_thirty_candidate_batch_per_question() -> None:
    pairs = build_fixed_pairs()
    assert len(pairs) == 12
    assert all(len(question_pairs) == 30 for question_pairs in pairs)
    assert all(
        len(pair) == 2
        for question_pairs in pairs
        for pair in question_pairs
    )
