"""验证制度 Collection 维度重建脚本的安全约束与失败恢复。

所有测试均使用 mock，不连接真实 Chroma、PostgreSQL 或 Embedding 模型。
"""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from scripts import policy_collection_embedding_rebuild as rebuild


class FakeCollection:
    """模拟 Chroma Collection 的最小行为。

    维度锁定规则：新建 Collection 维度未定，第一次 upsert 时锁定为该向量维度；
    之后 query 其他维度会被拒绝。
    """

    def __init__(
        self,
        name: str = "mini_rag_knowledge_chunks_v1",
        count: int = 0,
        metric: str = "cosine",
        locked_dimension: int | None = 512,
        fail_on_query: bool = False,
        fail_on_upsert: bool = False,
        fail_on_delete: bool = False,
    ) -> None:
        self.name = name
        self._count = count
        self.configuration = {"hnsw": {"space": metric}}
        self._locked_dimension = locked_dimension
        self._fail_on_query = fail_on_query
        self._fail_on_upsert = fail_on_upsert
        self._fail_on_delete = fail_on_delete
        self.stored_ids: list[str] = []

    def count(self) -> int:
        return self._count

    def query(self, query_embeddings, n_results=1, where=None):
        if self._fail_on_query:
            raise RuntimeError("query failed")
        dim = len(query_embeddings[0])
        if self._locked_dimension is not None and dim != self._locked_dimension:
            raise RuntimeError(f"dimension mismatch {dim}")
        if where:
            return {"ids": [["canary"]], "metadatas": [[{}]]}
        return {"ids": [[]]}

    def upsert(self, ids, documents, embeddings, metadatas):
        if self._fail_on_upsert:
            raise RuntimeError("upsert failed")
        if self._locked_dimension is None and embeddings:
            self._locked_dimension = len(embeddings[0])
        self.stored_ids.extend(ids)
        self._count = len(self.stored_ids)

    def delete(self, where=None, ids=None):
        if self._fail_on_delete:
            raise RuntimeError("delete failed")
        if ids:
            self.stored_ids = [i for i in self.stored_ids if i not in ids]
        else:
            self.stored_ids = []
        self._count = len(self.stored_ids)


class FakeChromaClient:
    """模拟 Chroma HTTP 客户端。"""

    def __init__(self, collection: FakeCollection | None = None) -> None:
        self._collection = collection or FakeCollection()
        self.deleted_collections: list[str] = []

    def get_or_create_collection(self, name, embedding_function=None, configuration=None):
        self._collection.name = name
        return self._collection

    def delete_collection(self, name):
        self.deleted_collections.append(name)
        # 真实 Chroma 删除后 get_or_create 会得到全新空 Collection，维度未定。
        self._collection.stored_ids = []
        self._collection._count = 0
        self._collection._locked_dimension = None


class FakeEmbeddings:
    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    def embed_documents(self, texts):
        return [[0.0] * self.dimension for _ in texts]


@pytest.fixture
def fake_settings(monkeypatch):
    """固定 settings 为 768 维旧制度 Collection。"""
    monkeypatch.setattr(settings, "chroma_collection", "mini_rag_knowledge_chunks_v1")
    monkeypatch.setattr(settings, "chroma_final_collection", "archive_final_chunks")
    monkeypatch.setattr(settings, "embedding_dimension", 768)


@pytest.fixture
def fake_chroma(monkeypatch, fake_settings):
    collection = FakeCollection()
    client = FakeChromaClient(collection)
    monkeypatch.setattr(rebuild, "_get_chroma_client", lambda: client)
    monkeypatch.setattr(rebuild, "_get_embedding_dimension", lambda: 768)
    return client, collection


@pytest.fixture
def fake_postgres_empty(monkeypatch, fake_settings):
    """模拟 PostgreSQL 无待重建文档。"""

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            pass

    monkeypatch.setattr(
        rebuild,
        "_query_postgres_pending_documents",
        lambda session: (0, 0),
    )
    monkeypatch.setattr(rebuild, "Session", lambda **kw: FakeSession())


def test_preflight_rejects_wrong_target_name(fake_settings, monkeypatch):
    """目标名不等于 settings.chroma_collection 时必须拒绝。"""
    monkeypatch.setattr(settings, "chroma_collection", "mini_rag_knowledge_chunks_v1")
    with pytest.raises(rebuild.RebuildError, match="TARGET_COLLECTION_MISMATCH"):
        rebuild._validate_target_collection_name("other_collection")


def test_preflight_rejects_archive_collection(fake_settings, monkeypatch):
    """禁止操作智慧档案正式 Collection。"""
    # 让目标名等于 chroma_collection 才能通过第一关，触发 archive 检查。
    monkeypatch.setattr(settings, "chroma_collection", "archive_final_chunks")
    with pytest.raises(rebuild.RebuildError, match="ARCHIVE_COLLECTION_FORBIDDEN"):
        rebuild._validate_target_collection_name("archive_final_chunks")


def test_preflight_rejects_experiment_collection_suffix(fake_settings, monkeypatch):
    """禁止操作带实验后缀的 Collection。"""
    monkeypatch.setattr(settings, "chroma_collection", "archive_final_chunks_v2")
    with pytest.raises(
        rebuild.RebuildError, match="ARCHIVE_OR_EXPERIMENT_COLLECTION_FORBIDDEN"
    ):
        rebuild._validate_target_collection_name("archive_final_chunks_v2")


def test_preflight_rejects_non_768_config(monkeypatch, fake_settings):
    """配置维度不是 768 必须拒绝。"""
    monkeypatch.setattr(settings, "embedding_dimension", 512)
    with pytest.raises(rebuild.RebuildError, match="CONFIG_DIMENSION_INVALID"):
        rebuild.run_preflight()


def test_preflight_rejects_non_768_model(monkeypatch, fake_settings):
    """实际模型输出维度不是 768 必须拒绝。"""
    monkeypatch.setattr(rebuild, "_get_embedding_dimension", lambda: 512)
    with pytest.raises(rebuild.RebuildError, match="MODEL_DIMENSION_INVALID"):
        rebuild.run_preflight()


def test_preflight_stops_when_collection_not_empty(fake_settings, fake_chroma, fake_postgres_empty):
    """Collection 非空必须停止且不得调用删除。"""
    _, collection = fake_chroma
    collection._count = 5
    with pytest.raises(rebuild.RebuildError, match="COLLECTION_NOT_EMPTY"):
        rebuild.run_preflight()
    client, _ = fake_chroma
    assert client.deleted_collections == []


def test_preflight_stops_when_postgres_pending(fake_settings, fake_chroma, monkeypatch):
    """PostgreSQL 存在待重建文档必须停止。"""
    monkeypatch.setattr(
        rebuild,
        "_query_postgres_pending_documents",
        lambda session: (3, 0),
    )
    monkeypatch.setattr(rebuild, "Session", lambda **kw: SimpleNamespace(close=lambda: None))
    with pytest.raises(rebuild.RebuildError, match="POSTGRES_PENDING_DOCUMENTS"):
        rebuild.run_preflight()


def test_preflight_already_768_is_idempotent(fake_settings, fake_chroma, fake_postgres_empty):
    """已是 768 维时必须幂等退出且不得删除。"""
    _, collection = fake_chroma
    collection._locked_dimension = 768
    result = rebuild.run_preflight()
    assert result.status == "already_768"
    client, _ = fake_chroma
    assert client.deleted_collections == []


def test_preflight_512_empty_collection_requires_rebuild(fake_settings, fake_chroma, fake_postgres_empty):
    """空 512 维 Collection 应进入重建路径。"""
    result = rebuild.run_preflight()
    assert result.status == "rebuild_required"
    assert result.collection_count == 0


def test_canary_query_uses_three_field_filter(fake_settings, fake_chroma, fake_postgres_empty, monkeypatch):
    """768 canary 查询必须使用服务端生成的三字段范围过滤。"""
    captured = {}

    original_verify = rebuild._verify_canary

    def spy_verify(collection, identity, dimension):
        captured["identity"] = identity
        captured["dimension"] = dimension
        return original_verify(collection, identity, dimension)

    monkeypatch.setattr(rebuild, "_verify_canary", spy_verify)
    rebuild.run_apply()
    assert set(captured["identity"].keys()) == {"user_id", "kb_id", "document_id"}
    assert captured["dimension"] == 768


def test_apply_restores_512_on_write_failure(fake_settings, fake_chroma, fake_postgres_empty, monkeypatch):
    """写入失败时必须执行 512 维空库恢复。"""
    _, collection = fake_chroma
    collection._fail_on_upsert = True

    restored = []

    def fake_restore(client, name):
        restored.append(name)

    monkeypatch.setattr(rebuild, "_restore_512_empty_collection", fake_restore)
    with pytest.raises(rebuild.RebuildError, match="REBUILD_FAILED"):
        rebuild.run_apply()
    assert restored == ["mini_rag_knowledge_chunks_v1"]


def test_apply_restores_512_on_query_failure(fake_settings, fake_chroma, fake_postgres_empty, monkeypatch):
    """canary 验证查询失败时必须执行 512 维空库恢复。"""
    # 只在 _verify_canary 阶段失败，不影响预检的维度探测。
    def fail_verify(collection, identity, dimension):
        raise rebuild.RebuildError("CANARY_QUERY_EMPTY", "canary 范围查询未命中。")

    restored = []
    monkeypatch.setattr(rebuild, "_verify_canary", fail_verify)
    monkeypatch.setattr(rebuild, "_restore_512_empty_collection", lambda client, name: restored.append(name))
    with pytest.raises(rebuild.RebuildError, match="CANARY_QUERY_EMPTY"):
        rebuild.run_apply()
    assert restored == ["mini_rag_knowledge_chunks_v1"]


def test_apply_success_clears_chunk_collection_cache(fake_settings, fake_chroma, fake_postgres_empty, monkeypatch):
    """成功后必须清理 get_chunk_collection 缓存。"""
    cleared = []
    monkeypatch.setattr(
        rebuild.get_chunk_collection,
        "cache_clear",
        lambda: cleared.append(True),
    )
    result = rebuild.run_apply()
    assert result.status == "rebuild_succeeded"
    assert cleared == [True]


def test_apply_success_accepts_768_rejects_512(fake_settings, fake_chroma, fake_postgres_empty):
    """成功后 768 查询接受、512 查询拒绝、canary 为 0 条。"""
    _, collection = fake_chroma
    rebuild.run_apply()
    assert collection.count() == 0
    assert rebuild._probe_collection_dimension(collection, 768) is True
    assert rebuild._probe_collection_dimension(collection, 512) is False


def test_apply_does_not_run_without_preflight_pass(fake_settings, fake_chroma, monkeypatch):
    """预检失败时 apply 不得执行删除。"""
    _, collection = fake_chroma
    collection._count = 10
    client, _ = fake_chroma
    with pytest.raises(rebuild.RebuildError):
        rebuild.run_apply()
    assert client.deleted_collections == []
