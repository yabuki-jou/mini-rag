"""验证正式 Embedding 重建脚本的安全门和失败恢复契约。"""

from types import SimpleNamespace
from unittest.mock import Mock

import chromadb
import pytest

from app.core.config import settings
from scripts import archive_v1_formal_embedding_rebuild as rebuild


def test_formal_collection_name_must_match_configured_target() -> None:
    """正式脚本只能操作配置中的正式 Collection。"""
    assert rebuild.validate_formal_collection_name(settings.chroma_final_collection)

    with pytest.raises(ValueError, match="正式 Collection"):
        rebuild.validate_formal_collection_name("archive_final_chunks_exp_other")


def test_rebuild_requires_explicit_confirmation_before_connecting(monkeypatch) -> None:
    """缺少安全开关时不得连接 Chroma 或数据库。"""
    client = Mock()
    monkeypatch.setattr(rebuild, "get_chroma_client", lambda: client)

    with pytest.raises(SystemExit):
        rebuild.main([])

    client.get_collection.assert_not_called()
    client.get_or_create_collection.assert_not_called()


def test_non_empty_formal_collection_is_rejected_before_delete(monkeypatch) -> None:
    """正式 Collection 非空时必须在删除前稳定失败。"""
    collection = SimpleNamespace(name=settings.chroma_final_collection, count=lambda: 1)
    client = Mock()
    client.get_collection.return_value = collection
    monkeypatch.setattr(rebuild, "get_chroma_client", lambda: client)

    with pytest.raises(RuntimeError, match="非空"):
        rebuild.prepare_empty_formal_collection(client=client)

    client.delete_collection.assert_not_called()


def test_missing_formal_collection_is_treated_as_absent() -> None:
    """Chroma 明确的不存在错误才允许进入新建流程。"""
    client = Mock()
    client.get_collection.side_effect = chromadb.errors.NotFoundError("missing")

    assert rebuild._get_existing_collection(client) is None


def test_unexpected_collection_error_is_not_swallowed() -> None:
    """连接或权限错误必须继续暴露，不能伪装成 Collection 不存在。"""
    client = Mock()
    client.get_collection.side_effect = RuntimeError("connection failed")

    with pytest.raises(RuntimeError, match="connection failed"):
        rebuild._get_existing_collection(client)


def test_restore_ignores_only_missing_collection() -> None:
    """恢复时目标已被清理可以继续创建，其他删除错误不能被吞掉。"""
    replacement = SimpleNamespace(
        name=settings.chroma_final_collection,
        count=lambda: 0,
        configuration={"hnsw": {"space": "cosine"}},
    )
    client = Mock()
    client.delete_collection.side_effect = chromadb.errors.NotFoundError("missing")
    client.get_or_create_collection.return_value = replacement

    assert rebuild._restore_empty_formal_collection(client) is replacement


def test_restore_does_not_swallow_connection_error() -> None:
    """恢复时连接错误必须继续抛出。"""
    client = Mock()
    client.delete_collection.side_effect = RuntimeError("connection failed")

    with pytest.raises(RuntimeError, match="connection failed"):
        rebuild._restore_empty_formal_collection(client)


def test_success_path_removes_canary_and_matches_summary(monkeypatch) -> None:
    """成功重建后 canary 不残留，Collection 数量等于重建汇总。"""
    class FakeCollection:
        name = settings.chroma_final_collection
        configuration = {"hnsw": {"space": "cosine"}}

        def __init__(self):
            self._ids = set()

        def count(self):
            return len(self._ids)

        def upsert(self, *, ids, **_):
            self._ids.update(ids)

        def get(self, *, ids, **_):
            return {"ids": [item for item in ids if item in self._ids]}

        def delete(self, *, ids):
            self._ids.difference_update(ids)

    old_collection = FakeCollection()
    new_collection = FakeCollection()
    client = Mock()
    client.get_collection.return_value = old_collection
    client.get_or_create_collection.return_value = new_collection
    monkeypatch.setattr(rebuild, "validate_formal_settings", lambda: None)
    def fake_rebuild(**kwargs):
        kwargs["target_collection"].upsert(ids=["chunk-1", "chunk-2"])
        return SimpleNamespace(
            document_count=2,
            chunk_count=2,
            contextual_chunk_count=2,
        )

    monkeypatch.setattr(rebuild, "rebuild_confirmed_documents", fake_rebuild)
    monkeypatch.setattr(rebuild, "Session", lambda _: _FakeSession())

    result = rebuild.run_rebuild(confirm_empty_rebuild=True, client=client)

    assert result.document_count == 2
    assert result.chunk_count == 2
    assert new_collection.count() == 2
    assert rebuild._CANARY_ID not in new_collection._ids


class _FakeSession:
    """为成功路径测试提供不连接数据库的会话上下文。"""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_d1_formal_target_requires_exact_name() -> None:
    """D1 仅在显式允许时接受精确正式 Collection 名称。"""
    from scripts import archive_v1_d1_embedding_rebuild as d1

    session = Mock()
    session.exec.return_value.all.return_value = []
    formal = SimpleNamespace(name=settings.chroma_final_collection)

    summary = d1.rebuild_confirmed_documents(
        session=session,
        target_collection=formal,
        allow_formal_collection=True,
    )
    assert summary.chunk_count == 0

    with pytest.raises(ValueError, match="正式重建"):
        d1.rebuild_confirmed_documents(
            session=session,
            target_collection=SimpleNamespace(name="archive_final_chunks_exp_other"),
            allow_formal_collection=True,
        )


def test_failure_recreates_empty_cosine_collection(monkeypatch) -> None:
    """重建中途失败时必须清理部分写入并恢复空 cosine Collection。"""
    old_collection = SimpleNamespace(
        name=settings.chroma_final_collection,
        count=lambda: 0,
        configuration={"hnsw": {"space": "cosine"}},
    )
    replacement = SimpleNamespace(
        name=settings.chroma_final_collection,
        count=lambda: 0,
        configuration={"hnsw": {"space": "cosine"}},
    )
    client = Mock()
    client.get_collection.return_value = old_collection
    client.get_or_create_collection.return_value = replacement
    monkeypatch.setattr(rebuild, "get_chroma_client", lambda: client)
    monkeypatch.setattr(rebuild, "validate_formal_settings", lambda: None)
    monkeypatch.setattr(rebuild, "_verify_canary_dimension", lambda collection: None)
    monkeypatch.setattr(rebuild, "_verify_canary_absent", lambda collection: None)
    monkeypatch.setattr(rebuild, "rebuild_confirmed_documents", Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        rebuild.run_rebuild(confirm_empty_rebuild=True, client=client)

    assert client.delete_collection.call_count == 2
    assert all(
        call.kwargs == {"name": settings.chroma_final_collection}
        for call in client.delete_collection.call_args_list
    )
    assert client.get_or_create_collection.call_args.kwargs["configuration"] == {
        "hnsw": {"space": "cosine"}
    }
