"""验证 P09 Final Chunk 规范化和独立 Chroma Collection 写入契约。"""

from pathlib import Path
import json
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import Mock

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.models import ParsedSnapshot
from app.services import archive_final_chunk_service


def _snapshot(tmp_path: Path, document_id: UUID) -> ParsedSnapshot:
    """创建带两个位置片段的临时解析快照。"""
    payload = {
        "document_id": str(document_id),
        "file_hash": "f" * 64,
        "snapshot_hash": "a" * 64,
        "parser_version": "archive-parser-v1",
        "normalization_version": "archive-normalization-v1",
        "fragments": [
            {
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 1,
                "location_end": 2,
                "content": "第一段正文",
                "anchor_text": "第一段",
            },
            {
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 3,
                "location_end": 3,
                "content": "第二段正文",
                "anchor_text": "第二段",
            },
        ],
    }
    path = tmp_path / "parsed_snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return ParsedSnapshot(
        document_id=document_id,
        snapshot_storage_path=str(path),
        snapshot_hash="a" * 64,
        parser_name="archive_parser_service",
        parser_version="archive-parser-v1",
        normalization_version="archive-normalization-v1",
        text_character_count=10,
        fragment_count=2,
    )


def test_build_final_chunks_is_stable_and_keeps_snapshot_locations(tmp_path: Path) -> None:
    """同一快照重复构建应得到稳定 ID 和可追溯定位元数据。"""
    document_id = UUID("00000000-0000-0000-0000-000000000001")
    snapshot = _snapshot(tmp_path, document_id)

    first = archive_final_chunk_service.build_final_chunks(
        document_id=document_id,
        snapshot=snapshot,
    )
    second = archive_final_chunk_service.build_final_chunks(
        document_id=document_id,
        snapshot=snapshot,
    )

    assert first == second
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert first[0].content == "第一段正文"
    assert first[0].location_type == "TEXT_LINE_RANGE"
    assert (first[0].location_start, first[0].location_end) == (1, 2)
    assert first[0].snapshot_hash == "a" * 64
    assert first[0].parser_version == "archive-parser-v1"


def test_build_final_chunks_rejects_snapshot_document_mismatch(tmp_path: Path) -> None:
    """Final Chunk 不能从其他文档的快照构建，避免跨文档引用泄露。"""
    document_id = UUID("00000000-0000-0000-0000-000000000001")
    other_document_id = UUID("00000000-0000-0000-0000-000000000002")
    snapshot = _snapshot(tmp_path, other_document_id)

    with pytest.raises(AppError) as exc_info:
        archive_final_chunk_service.build_final_chunks(
            document_id=document_id,
            snapshot=snapshot,
        )

    assert exc_info.value.code == "FINAL_SNAPSHOT_INVALID"


def test_ensure_final_collection_uses_separate_cosine_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final Collection 必须与既有制度 Collection 分离并固定 cosine。"""
    collection = SimpleNamespace(
        name=settings.chroma_final_collection,
        configuration={"hnsw": {"space": "cosine"}},
    )
    client = Mock()
    client.get_or_create_collection.return_value = collection
    monkeypatch.setattr(archive_final_chunk_service, "get_chroma_client", lambda: client)

    assert archive_final_chunk_service.ensure_final_collection() == settings.chroma_final_collection
    client.get_or_create_collection.assert_called_once_with(
        name=settings.chroma_final_collection,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )


def test_insert_final_chunks_writes_server_scoped_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final Chunk 写入必须携带服务端范围和快照追溯元数据。"""
    user_id = UUID("00000000-0000-0000-0000-000000000010")
    project_id = UUID("00000000-0000-0000-0000-000000000011")
    kb_id = UUID("00000000-0000-0000-0000-000000000012")
    document_id = UUID("00000000-0000-0000-0000-000000000013")
    collection = Mock()
    monkeypatch.setattr(archive_final_chunk_service, "get_final_collection", lambda: collection)
    chunk = archive_final_chunk_service.ArchiveEmbeddedChunk(
        chunk_id="c" * 64,
        content="确认后的原文",
        embedding=[0.1, 0.2],
        location_type="TEXT_LINE_RANGE",
        location_start=1,
        location_end=1,
        normalized_anchor="确认",
        snapshot_hash="a" * 64,
        parser_version="archive-parser-v1",
    )

    assert archive_final_chunk_service.insert_final_chunks(
        user_id=user_id,
        project_id=project_id,
        kb_id=kb_id,
        document_id=document_id,
        filename="施工方案.txt",
        chunks=[chunk],
    ) == 1

    kwargs = collection.upsert.call_args.kwargs
    assert kwargs["ids"] == ["c" * 64]
    assert kwargs["documents"] == ["确认后的原文"]
    assert kwargs["embeddings"] == [[0.1, 0.2]]
    assert kwargs["metadatas"] == [
        {
            "user_id": str(user_id),
            "project_id": str(project_id),
            "kb_id": str(kb_id),
            "document_id": str(document_id),
            "filename": "施工方案.txt",
            "snapshot_hash": "a" * 64,
            "parser_version": "archive-parser-v1",
            "location_type": "TEXT_LINE_RANGE",
            "location_start": 1,
            "location_end": 1,
            "excerpt_anchor": "确认",
        }
    ]


def test_insert_final_chunks_maps_chroma_failure_to_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chroma 写入异常只返回稳定错误，不泄露底层异常文本。"""
    collection = Mock()
    collection.upsert.side_effect = RuntimeError("internal archive host")
    monkeypatch.setattr(archive_final_chunk_service, "get_final_collection", lambda: collection)
    chunk = archive_final_chunk_service.ArchiveEmbeddedChunk(
        chunk_id="d" * 64,
        content="正文",
        embedding=[0.1],
        location_type="TEXT_LINE_RANGE",
        location_start=1,
        location_end=1,
        normalized_anchor=None,
        snapshot_hash="a" * 64,
        parser_version="archive-parser-v1",
    )

    with pytest.raises(AppError) as exc_info:
        archive_final_chunk_service.insert_final_chunks(
            user_id=UUID("00000000-0000-0000-0000-000000000010"),
            project_id=UUID("00000000-0000-0000-0000-000000000011"),
            kb_id=UUID("00000000-0000-0000-0000-000000000012"),
            document_id=UUID("00000000-0000-0000-0000-000000000013"),
            filename="施工方案.txt",
            chunks=[chunk],
        )

    assert exc_info.value.code == "VECTOR_UNAVAILABLE"
    assert "internal archive host" not in exc_info.value.message


def test_embed_final_chunks_uses_context_for_vector_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已确认档案上下文可以提高向量可检索性，但不能污染返回原文摘录。"""
    class CapturingEmbeddings:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        def embed_documents(self, values: list[str]) -> list[list[float]]:
            self.inputs = values
            return [[0.1] * settings.embedding_dimension for _ in values]

    embeddings = CapturingEmbeddings()
    monkeypatch.setattr(archive_final_chunk_service, "get_embeddings", lambda: embeddings)
    chunks = archive_final_chunk_service.build_final_chunks(
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot=_snapshot(tmp_path, UUID("00000000-0000-0000-0000-000000000001")),
    )

    embedded = archive_final_chunk_service.embed_final_chunks(
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        chunks=chunks,
        embedding_context="档案标题：施工方案\n资料类型：施工资料",
    )

    assert embeddings.inputs == [
        "档案标题：施工方案\n资料类型：施工资料\n原文片段：第一段正文",
        "档案标题：施工方案\n资料类型：施工资料\n原文片段：第二段正文",
    ]
    assert [item.content for item in embedded] == ["第一段正文", "第二段正文"]


def test_embed_final_chunks_uses_context_for_only_the_matching_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """字段值上下文只能影响有原文证据的 Chunk，不能扩散到同档案全部片段。"""
    class CapturingEmbeddings:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        def embed_documents(self, values: list[str]) -> list[list[float]]:
            self.inputs = values
            return [[0.1] * settings.embedding_dimension for _ in values]

    embeddings = CapturingEmbeddings()
    monkeypatch.setattr(archive_final_chunk_service, "get_embeddings", lambda: embeddings)
    chunks = archive_final_chunk_service.build_final_chunks(
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        snapshot=_snapshot(tmp_path, UUID("00000000-0000-0000-0000-000000000001")),
    )

    embedded = archive_final_chunk_service.embed_final_chunks(
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        chunks=chunks,
        embedding_contexts={chunks[0].chunk_id: "施工阶段"},
    )

    assert embeddings.inputs == ["施工阶段\n原文片段：第一段正文", "第二段正文"]
    assert [item.content for item in embedded] == ["第一段正文", "第二段正文"]
