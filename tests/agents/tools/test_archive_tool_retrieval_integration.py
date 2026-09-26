"""验证 FR-042 证据工具至真实档案检索服务的跨层门控路径。"""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.agents.tools.archive_tools import (
    ArchiveToolRuntime,
    _ArchiveEvidenceToolInput,
    build_archive_tools,
)
from app.core.config import settings
from app.models import Project
from app.services.archive import retrieval as retrieval_service
from tests.routers.test_project_archive_catalog import _confirmed_document
from tests.routers.test_project_documents import create_user, project_document_api


class _FakeEmbeddings:
    """记录送入 Embedding 边界的完整查询表达。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        """记录并向检索服务提供固定向量。

        Args:
            query: 已由生产服务构造的完整查询表达。
        """
        self.queries.append(query)
        return [0.1, 0.2]


class _FakeCollection:
    """返回两路固定候选并保留 Chroma 查询参数供断言。"""

    def __init__(self, results: list[dict[str, object]]) -> None:
        """保存预设的两路结果。

        Args:
            results: 按调用顺序返回的 Chroma 列式结果。
        """
        self.results = results
        self.calls: list[dict[str, object]] = []

    def query(self, **kwargs: object) -> dict[str, object]:
        """记录正式检索参数并返回对应结果。

        Args:
            kwargs: 生产服务构造的向量、数量和范围过滤参数。
        """
        self.calls.append(kwargs)
        return self.results[len(self.calls) - 1]


def _chroma_result(
    *,
    document_id: UUID,
    chunk_ids: list[str],
    label: str,
    target_chunk_id: str | None = None,
) -> dict[str, list[list[object]]]:
    """构造符合生产列式解析契约的固定 Chroma 结果。

    Args:
        document_id: 本地已确认档案的数据库标识。
        chunk_ids: 本路返回的候选标识。
        label: 用于区分原始路与补充路的测试内容前缀。
        target_chunk_id: 可选的补充路目标证据候选标识。
    """
    documents = [
        "补充路目标证据" if chunk_id == target_chunk_id else f"{label}摘录-{index}"
        for index, chunk_id in enumerate(chunk_ids)
    ]
    return {
        "ids": [chunk_ids],
        "documents": [documents],
        "metadatas": [[
            {
                "document_id": str(document_id),
                "filename": "confirmed-archive.txt",
                "location_type": "TEXT_LINE_RANGE",
                "location_start": index + 1,
                "location_end": index + 1,
            }
            for index in range(len(chunk_ids))
        ]],
        "distances": [[index / 100 for index in range(len(chunk_ids))]],
    }


def test_serialized_graph_tool_runs_dual_retrieval_with_current_user_gate(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """序列化用户消息应门控真实检索服务，同时保留工具查询并返回补充证据。

    Args:
        project_document_api: 使用本地隔离 SQLite 与临时文件的项目测试环境。
        monkeypatch: 替换 Embedding、Chroma 和 CrossEncoder 三个外部推理边界。
    """
    _client, engine, _storage_root = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(_client, engine, user_id)
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        kb_id = project.kb_id

    gate_question = "世界银行贷款的批准金额是多少？"
    model_query = "贷款承诺金额"
    input_state = _ArchiveEvidenceToolInput.model_validate(
        {
            "query": model_query,
            "state": {"messages": [HumanMessage(content=gate_question)]},
            "tool_call_id": "world-bank-tool-call",
        }
    ).model_dump(mode="python")
    serialized_message = input_state["state"]["messages"][-1]
    assert serialized_message["type"] == "human"
    assert serialized_message["content"] == gate_question

    original_ids = [f"{index:064x}" for index in range(30)]
    target_chunk_id = f"{1000:064x}"
    supplemental_ids = [original_ids[0], *[f"{index + 1000:064x}" for index in range(29)]]
    results = [
        _chroma_result(
            document_id=document_id,
            chunk_ids=original_ids,
            label="原始路",
        ),
        _chroma_result(
            document_id=document_id,
            chunk_ids=supplemental_ids,
            label="补充路",
            target_chunk_id=target_chunk_id,
        ),
    ]
    embeddings = _FakeEmbeddings()
    collection = _FakeCollection(results)
    reranker_inputs: list[tuple[str, list[str]]] = []

    def fake_score(*, query: str, contents: list[str]) -> list[float]:
        reranker_inputs.append((query, contents))
        return [1.0 if content == "补充路目标证据" else 0.0 for content in contents]

    monkeypatch.setattr(retrieval_service, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(retrieval_service, "score_archive_candidates", fake_score)
    monkeypatch.setattr(settings, "archive_reranker_candidate_k", 30, raising=False)
    runtime = ArchiveToolRuntime(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        session_factory=lambda: Session(engine),
    )
    _, evidence_tool = build_archive_tools(runtime=runtime)

    result = evidence_tool.func(
        query=input_state["query"],
        state=input_state["state"],
        tool_call_id=input_state["tool_call_id"],
    )

    assert [call["n_results"] for call in collection.calls] == [30, 30]
    assert collection.calls[0]["where"] == collection.calls[1]["where"]
    assert collection.calls[0]["where"] == {
        "$and": [
            {"user_id": str(user_id)},
            {"project_id": str(project_id)},
            {"kb_id": str(kb_id)},
            {"document_id": {"$in": [str(document_id)]}},
        ]
    }
    assert embeddings.queries == [
        "为这个句子生成表示以用于检索相关文章：档案证据检索问题：贷款承诺金额",
        "为这个句子生成表示以用于检索相关文章：档案证据检索问题：IBRD IDA",
    ]
    assert reranker_inputs == [
        (
            "档案证据检索问题：IBRD IDA",
            [
                *[f"原始路摘录-{index}" for index in range(30)],
                *[
                    "补充路目标证据" if chunk_id == target_chunk_id else f"补充路摘录-{index}"
                    for index, chunk_id in enumerate(supplemental_ids[1:], start=1)
                ],
            ],
        )
    ]
    assert len(reranker_inputs[0][1]) == 59
    assert sum("原始路摘录-0" == excerpt for excerpt in reranker_inputs[0][1]) == 1
    assert result["found"] is True
    assert len(result["results"]) == 8
    assert result["results"][0]["excerpt"] == "补充路目标证据"
    assert result["results"][0]["filename"] == "confirmed-archive.txt"
