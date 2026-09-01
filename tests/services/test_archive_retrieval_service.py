"""验证 AV1-P11 正式检索的授权范围、阈值和结果转换。"""

from uuid import UUID, uuid4

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.core.errors import AppError
from app.models import ArchiveDocumentStatus, Project
from app.services import archive_retrieval_service
from tests.routers.test_project_archive_catalog import (
    _add_pending_document,
    _confirmed_document,
)
from tests.routers.test_project_documents import create_user, project_document_api
from tests.support.auth import auth_headers


def test_build_archive_query_expression_is_stable_and_strips_whitespace() -> None:
    """查询表达必须固定且只规范化首尾空白。"""
    assert archive_retrieval_service._build_archive_query_expression(
        "  项目阶段  "
    ) == "档案证据检索问题：项目阶段"


class FakeEmbeddings:
    """返回固定查询向量，避免单测加载本地 BGE。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [0.1, 0.2]


class FakeCollection:
    """记录 Chroma 查询参数并返回列式结果。"""

    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture(autouse=True)
def stub_archive_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离检索测试的本地大模型加载，重排规则由各用例显式覆盖。"""
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        lambda *, query, contents: [0.5] * len(contents),
        raising=False,
    )


def _result(document_id: UUID, other_id: UUID) -> dict:
    return {
        "ids": [["a" * 64, "b" * 64]],
        "documents": [["正式证据", "待确认内容"]],
        "metadatas": [[
            {
                "document_id": str(document_id),
                "filename": "正式资料.docx",
                "location_type": "DOCX_PARAGRAPH",
                "location_start": 3,
                "location_end": 3,
            },
            {
                "document_id": str(other_id),
                "filename": "待确认资料.txt",
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 1,
                "location_end": 2,
            },
        ]],
        "distances": [[0.12, 0.05]],
    }


def test_retrieval_uses_formal_scope_and_filters_result_documents(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """检索必须使用正式文档集合、完整四重范围和结果二次范围校验。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, formal_id, _ = _confirmed_document(client, engine, user_id)
    pending_id = _add_pending_document(engine, project_id)
    collection = FakeCollection(_result(formal_id, pending_id))
    embeddings = FakeEmbeddings()
    reranker_queries: list[str] = []

    def fake_score(*, query: str, contents: list[str]) -> list[float]:
        reranker_queries.append(query)
        return [0.5] * len(contents)

    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(archive_retrieval_service, "score_archive_candidates", fake_score)
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="正式证据是什么？",
            top_k=5,
            session=session,
        )

    assert [item.document_id for item in response.items] == [formal_id]
    where = collection.calls[0]["where"]
    assert {key for clause in where["$and"] for key in clause} >= {
        "user_id",
        "project_id",
        "kb_id",
        "document_id",
    }
    document_clause = next(clause for clause in where["$and"] if "document_id" in clause)
    assert document_clause["document_id"]["$in"] == [str(formal_id)]
    assert embeddings.queries == [
        "为这个句子生成表示以用于检索相关文章：档案证据检索问题：正式证据是什么？"
    ]
    assert reranker_queries == ["档案证据检索问题：正式证据是什么？"]


def test_retrieval_applies_distance_threshold_and_returns_evidence_fields(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """低于独立重排阈值的候选被过滤，并保留文件名、定位和摘录。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    collection = FakeCollection(
        {
            "ids": [["a" * 64, "b" * 64]],
            "documents": [["命中", "低相关"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 2,
                    "location_end": 2,
                },
                {
                    "document_id": str(document_id),
                    "filename": "资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 8,
                    "location_end": 8,
                },
            ]],
            "distances": [[0.1, 0.8]],
        }
    )
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        lambda *, query, contents: [0.9, 0.1],
    )
    monkeypatch.setattr(settings, "archive_reranker_score_threshold", 0.5, raising=False)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="命中",
            top_k=5,
            session=session,
        )

    assert response.returned_count == 1
    item = response.items[0]
    assert item.excerpt == "命中"
    assert item.location_type == "PDF_PAGE"
    assert item.location_start == item.location_end == 2
    assert item.score == pytest.approx(0.9)


def test_retrieval_returns_empty_without_formal_documents(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有正式档案时返回空结果，且不调用 Chroma。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={"name": "只有待确认资料", "description": None},
    ).json()
    project_id = UUID(project["id"])
    pending_id = _add_pending_document(engine, project_id)
    called = False

    def fail_collection():
        nonlocal called
        called = True
        raise AssertionError("no formal document must not query Chroma")

    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", fail_collection)
    with Session(engine) as session:
        project_row = session.get(Project, project_id)
        assert project_row is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project_row.kb_id,
            query="不存在",
            top_k=5,
            session=session,
        )

    assert response.items == []
    assert response.returned_count == 0
    assert called is False


def test_retrieval_records_eval_scope_and_final_items(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评测观测必须保留服务端构造的范围和最终进入问答的候选。"""
    _, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(project_document_api[0], engine, user_id)
    collection = FakeCollection(_result(document_id, uuid4()))
    observed: list[tuple[object, dict[str, object]]] = []

    def capture(value: object, **kwargs: object) -> object:
        observed.append((value, kwargs))
        return value

    monkeypatch.setattr(archive_retrieval_service, "eval_wrap", capture)
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="正式证据是什么？",
            top_k=5,
            session=session,
        )

    observed_names = {str(kwargs["name"]) for _, kwargs in observed}
    assert {"archive_retrieval_scope", "archive_retrieval_result"} <= observed_names
    assert all(kwargs["purpose"] == "state" for _, kwargs in observed)
    observed_by_name = {str(kwargs["name"]): value for value, kwargs in observed}
    assert observed_by_name["archive_retrieval_scope"] == {
        "formal_document_count": 1,
        "scope_keys": ["user_id", "project_id", "kb_id", "document_id"],
    }
    assert observed_by_name["archive_retrieval_result"]["returned_count"] == 1
    assert observed_by_name["archive_retrieval_result"]["items"][0]["document_id"] == str(document_id)


def test_retrieval_reranks_only_validated_top_twenty_candidates(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重排只能处理范围校验后的 Top-20，并保持最终 API 只返回请求数量。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    pending_id = _add_pending_document(engine, project_id)
    collection = FakeCollection(
        {
            "ids": [["a" * 64, "b" * 64, "c" * 64, "d" * 64]],
            "documents": [["低重排分数", "高重排分数", "待确认内容", "中等重排分数"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 1,
                    "location_end": 1,
                },
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 2,
                    "location_end": 2,
                },
                {
                    "document_id": str(pending_id),
                    "filename": "待确认资料.txt",
                    "location_type": "TEXT_LINE_RANGE",
                    "location_start": 1,
                    "location_end": 1,
                },
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 3,
                    "location_end": 3,
                },
            ]],
            "distances": [[0.1, 0.2, 0.01, 0.3]],
        }
    )
    observed_contents: list[str] = []

    def fake_score(*, query: str, contents: list[str]) -> list[float]:
        assert query == "档案证据检索问题：项目阶段"
        observed_contents.extend(contents)
        return [0.1, 0.9, 0.4]

    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        fake_score,
        raising=False,
    )
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="项目阶段",
            top_k=1,
            session=session,
        )

    assert collection.calls[0]["n_results"] == 20
    assert observed_contents == ["低重排分数", "高重排分数", "中等重排分数"]
    assert response.returned_count == 1
    assert response.items[0].excerpt == "高重排分数"


def test_retrieval_passes_all_top_twenty_candidates_to_reranker(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选池扩大后，位置靠后的合格候选也必须进入重排，最终仍只返回请求数量。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    items = [
        {
            "document_id": str(document_id),
            "filename": "正式资料.pdf",
            "location_type": "PDF_PAGE",
            "location_start": index + 1,
            "location_end": index + 1,
        }
        for index in range(20)
    ]
    collection = FakeCollection(
        {
            "ids": [[f"{index:064d}" for index in range(20)]],
            "documents": [[f"候选-{index + 1}" for index in range(20)]],
            "metadatas": [[item for item in items]],
            "distances": [[0.1 + index * 0.01 for index in range(20)]],
        }
    )
    observed_contents: list[str] = []

    def fake_score(*, query: str, contents: list[str]) -> list[float]:
        assert query == "档案证据检索问题：项目阶段"
        observed_contents.extend(contents)
        return [1.0 if content == "候选-20" else 0.0 for content in contents]

    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        fake_score,
        raising=False,
    )
    monkeypatch.setattr(settings, "retrieval_distance_threshold", None)
    monkeypatch.setattr(settings, "archive_reranker_score_threshold", None, raising=False)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="项目阶段",
            top_k=1,
            session=session,
        )

    assert collection.calls[0]["n_results"] == 20
    assert len(observed_contents) == 20
    assert response.returned_count == 1
    assert response.items[0].excerpt == "候选-20"


def test_retrieval_does_not_apply_legacy_distance_threshold_before_reranking(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧距离阈值不能在重排前排除已通过正式范围校验的候选。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    collection = FakeCollection(
        {
            "ids": [["a" * 64, "b" * 64]],
            "documents": [["低重排分数", "高重排分数"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 1,
                    "location_end": 1,
                },
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 2,
                    "location_end": 2,
                },
            ]],
            "distances": [[0.1, 0.9]],
        }
    )
    observed_contents: list[str] = []

    def fake_score(*, query: str, contents: list[str]) -> list[float]:
        assert query == "档案证据检索问题：项目阶段"
        observed_contents.extend(contents)
        return [0.9 if content == "高重排分数" else 0.1 for content in contents]

    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        fake_score,
        raising=False,
    )
    monkeypatch.setattr(settings, "retrieval_distance_threshold", 0.2)
    monkeypatch.setattr(settings, "archive_reranker_score_threshold", None, raising=False)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="项目阶段",
            top_k=1,
            session=session,
        )

    assert collection.calls[0]["n_results"] == 20
    assert observed_contents == ["低重排分数", "高重排分数"]
    assert response.returned_count == 1
    assert response.items[0].excerpt == "高重排分数"


def test_retrieval_returns_reranker_score_with_compatible_dense_score(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最终重排分数必须单独返回，且保留既有 Chroma 分数字段。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    collection = FakeCollection(
        {
            "ids": [["a" * 64]],
            "documents": [["正式证据"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 1,
                    "location_end": 1,
                }
            ]],
            "distances": [[0.1]],
        }
    )
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        lambda *, query, contents: [0.75],
    )

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="项目阶段",
            top_k=1,
            session=session,
        )

    assert response.items[0].score == pytest.approx(0.9)
    assert response.items[0].reranker_score == pytest.approx(0.75)


def test_retrieval_orders_equal_reranker_scores_by_distance_then_chunk_id(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重排分数并列时必须按原始 distance 和 Chunk ID 产生稳定顺序。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    collection = FakeCollection(
        {
            "ids": [["c" * 64, "b" * 64, "a" * 64]],
            "documents": [["距离较远", "同距离后", "同距离前"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": index,
                    "location_end": index,
                }
                for index in (1, 2, 3)
            ]],
            "distances": [[0.3, 0.1, 0.1]],
        }
    )
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        lambda *, query, contents: [0.5, 0.5, 0.5],
    )

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_chunks(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="项目阶段",
            top_k=3,
            session=session,
        )

    assert [item.chunk_id for item in response.items] == ["a" * 64, "b" * 64, "c" * 64]


def test_retrieval_diagnostics_keeps_full_validated_top_twenty_before_threshold(
    project_document_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C3-A 诊断必须看到完整校验候选，不能被公开阈值或返回数量截断。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_id, document_id, _ = _confirmed_document(client, engine, user_id)
    collection = FakeCollection(
        {
            "ids": [["a" * 64, "b" * 64]],
            "documents": [["普通候选", "标准证据"]],
            "metadatas": [[
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 1,
                    "location_end": 1,
                },
                {
                    "document_id": str(document_id),
                    "filename": "正式资料.pdf",
                    "location_type": "PDF_PAGE",
                    "location_start": 2,
                    "location_end": 2,
                },
            ]],
            "distances": [[0.1, 0.8]],
        }
    )
    monkeypatch.setattr(archive_retrieval_service, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(archive_retrieval_service, "get_final_collection", lambda: collection)
    monkeypatch.setattr(
        archive_retrieval_service,
        "score_archive_candidates",
        lambda *, query, contents: [0.2, 0.9],
    )
    monkeypatch.setattr(settings, "archive_reranker_score_threshold", 0.95, raising=False)

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        response = archive_retrieval_service.retrieve_archive_diagnostics(
            user_id=user_id,
            project_id=project_id,
            kb_id=project.kb_id,
            query="标准证据",
            expected_evidence={
                "relative_path": "documents/正式资料.pdf",
                "items": [{
                    "location_type": "PDF_PAGE",
                    "location_start": 2,
                    "location_end": 2,
                    "excerpt": "标准证据",
                }],
            },
            session=session,
        )

    assert response.chroma_candidate_count == 2
    assert response.candidate_count == 2
    assert len(response.candidates) == 2
    assert response.candidates[1].dense_rank == 2
    assert response.candidates[1].reranker_rank == 1
    assert response.candidates[1].matches_expected_evidence is True
    assert response.candidates[1].reranker_score == pytest.approx(0.9)
    serialized = response.model_dump_json()
    for forbidden in ("正式资料.pdf", "标准证据", str(document_id)):
        assert forbidden not in serialized
