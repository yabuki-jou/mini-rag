"""验证本地正式档案 Reranker 的评分与故障边界。"""

import pytest

from app.core.errors import AppError
from app.services import archive_reranker_service


class FakeCrossEncoder:
    """模拟本地模型的批量评分接口，避免单测加载真实权重。"""

    def __init__(self, scores: list[float] | Exception) -> None:
        self.scores = scores
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.pairs = pairs
        if isinstance(self.scores, Exception):
            raise self.scores
        return self.scores


def test_score_archive_candidates_uses_local_model_in_original_candidate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评分服务必须批量传入查询与原始 Chunk 正文，且保持候选顺序。"""
    model = FakeCrossEncoder([0.2, 0.9])
    monkeypatch.setattr(
        archive_reranker_service,
        "get_archive_reranker",
        lambda: model,
    )

    scores = archive_reranker_service.score_archive_candidates(
        query="项目当前阶段是什么？",
        contents=["项目处于施工阶段。", "项目处于竣工验收阶段。"],
    )

    assert model.pairs == [
        ("项目当前阶段是什么？", "项目处于施工阶段。"),
        ("项目当前阶段是什么？", "项目处于竣工验收阶段。"),
    ]
    assert scores == [0.2, 0.9]


def test_score_archive_candidates_maps_model_failure_to_stable_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地模型故障必须显式失败，不能由调用方静默退回纯稠密排序。"""
    monkeypatch.setattr(
        archive_reranker_service,
        "get_archive_reranker",
        lambda: FakeCrossEncoder(RuntimeError("model failure")),
    )

    with pytest.raises(AppError) as raised:
        archive_reranker_service.score_archive_candidates(
            query="项目当前阶段是什么？",
            contents=["项目处于施工阶段。"],
        )

    assert raised.value.status_code == 503
    assert raised.value.code == "RERANKER_UNAVAILABLE"
