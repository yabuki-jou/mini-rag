"""验证 D1 Embedding 替换实验的 Collection 隔离与只读业务重建契约。"""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.core.config import settings
from app.models import ArchiveDocumentStatus
from scripts import archive_v1_d1_embedding_rebuild as rebuild


def test_experiment_collection_name_must_be_run_scoped_and_distinct() -> None:
    """实验 Collection 必须使用明确前缀，且不能覆盖正式 Collection。"""
    name = "archive_final_chunks_exp_bge_base_20260902"

    assert rebuild.validate_experiment_collection_name(name) == name

    with pytest.raises(ValueError, match="实验 Collection"):
        rebuild.validate_experiment_collection_name(settings.chroma_final_collection)
    with pytest.raises(ValueError, match="实验 Collection"):
        rebuild.validate_experiment_collection_name("archive_final_chunks_v2")


def test_rebuild_writes_only_explicit_target_and_does_not_commit_business_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1 重建只能写入注入的实验 Collection，不能创建或提交业务操作。"""
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    project_id = UUID("00000000-0000-0000-0000-000000000002")
    kb_id = UUID("00000000-0000-0000-0000-000000000003")
    document_id = UUID("00000000-0000-0000-0000-000000000004")
    snapshot_id = UUID("00000000-0000-0000-0000-000000000005")
    archive_document = SimpleNamespace(
        document_id=document_id,
        status=ArchiveDocumentStatus.CONFIRMED,
        current_snapshot_id=snapshot_id,
    )
    document = SimpleNamespace(
        id=document_id,
        project_id=project_id,
        kb_id=kb_id,
        filename="施工方案.txt",
    )
    project = SimpleNamespace(id=project_id, kb_id=kb_id, owner_id=user_id)
    snapshot = SimpleNamespace(id=snapshot_id, document_id=document_id)
    session = Mock()
    session.exec.return_value.all.return_value = [archive_document]
    session.get.side_effect = lambda model, key: {
        document_id: document,
        project_id: project,
        snapshot_id: snapshot,
    }.get(key)
    target_collection = SimpleNamespace(name="archive_final_chunks_exp_bge_base_20260902")
    embedded_chunk = SimpleNamespace(embedding=[0.1], chunk_id="c" * 64)

    monkeypatch.setattr(settings, "archive_embedding_context_mode", "none")
    monkeypatch.setattr(rebuild, "list_visibility_blocked_document_ids", lambda *_: set())
    monkeypatch.setattr(rebuild, "build_final_chunks", lambda **_: ("chunk",))
    monkeypatch.setattr(rebuild, "embed_final_chunks", lambda **_: [embedded_chunk])
    insert = Mock(return_value=1)
    monkeypatch.setattr(rebuild, "insert_final_chunks", insert)

    summary = rebuild.rebuild_confirmed_documents(
        session=session,
        target_collection=target_collection,
    )

    assert summary.document_count == 1
    assert summary.chunk_count == 1
    insert.assert_called_once()
    assert insert.call_args.kwargs["collection"] is target_collection
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.flush.assert_not_called()


def test_get_experiment_collection_uses_cosine_and_exact_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """实验 Collection 必须以精确名称创建并固定 cosine 距离。"""
    name = "archive_final_chunks_exp_bge_base_20260902"
    collection = SimpleNamespace(
        name=name,
        configuration={"hnsw": {"space": "cosine"}},
    )
    client = Mock()
    client.get_or_create_collection.return_value = collection
    monkeypatch.setattr(rebuild, "get_chroma_client", lambda: client)

    assert rebuild.get_experiment_collection(name) is collection
    client.get_or_create_collection.assert_called_once_with(
        name=name,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )
