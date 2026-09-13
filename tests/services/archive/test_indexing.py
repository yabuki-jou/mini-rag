"""验证 P09 ArchiveOperation(INDEX) 的事务生命周期和幂等契约。"""

from collections.abc import Generator
import json
import logging
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveFieldName,
    ArchiveFieldValue,
    FieldEvidence,
    FieldReviewStatus,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    Document,
    KnowledgeBase,
    ParsedSnapshot,
    Project,
    User,
)
from app.services.archive import indexing as archive_index_service
from app.services.archive.final_chunks import (
    ArchiveEmbeddedChunk,
    ArchiveFinalChunk,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """提供启用外键约束的隔离 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _confirmed_document(
    session: Session,
    tmp_path: Path,
) -> tuple[Document, UUID, UUID]:
    """创建一份已确认且带当前快照的项目文档。"""
    user = User(name="index-owner")
    session.add(user)
    session.commit()
    user_id = user.id
    knowledge_base = KnowledgeBase(owner_id=user.id, name="index-kb")
    session.add(knowledge_base)
    session.commit()
    project = Project(owner_id=user.id, kb_id=knowledge_base.id, name="index-project")
    session.add(project)
    session.commit()
    document = Document(
        kb_id=knowledge_base.id,
        project_id=project.id,
        filename="施工方案.txt",
        storage_path="C:/tmp/施工方案.txt",
        file_hash="b" * 64,
    )
    session.add(document)
    session.flush()
    archive_document = ArchiveDocument(
        document_id=document.id,
        status=ArchiveDocumentStatus.PARSED,
    )
    session.add(archive_document)
    session.flush()
    snapshot_payload = {
        "document_id": str(document.id),
        "snapshot_hash": "a" * 64,
        "parser_version": "archive-parser-v1",
        "fragments": [
            {
                "location_type": "TEXT_LINE_RANGE",
                "location_start": 1,
                "location_end": 1,
                "content": "施工方案正文",
                "anchor_text": "施工",
            }
        ],
    }
    snapshot_path = tmp_path / "parsed_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot_payload, ensure_ascii=False), encoding="utf-8")
    snapshot = ParsedSnapshot(
        document_id=document.id,
        snapshot_storage_path=str(snapshot_path),
        snapshot_hash="a" * 64,
        parser_name="archive_parser_service",
        parser_version="archive-parser-v1",
        normalization_version="archive-normalization-v1",
        text_character_count=8,
        fragment_count=1,
    )
    session.add(snapshot)
    session.flush()
    archive_document.status = ArchiveDocumentStatus.CONFIRMED
    archive_document.current_snapshot_id = snapshot.id
    archive_document.confirmed_by = user_id
    archive_document.confirmed_at = document.created_at
    archive_document.final_index_snapshot_hash = snapshot.snapshot_hash
    session.add(archive_document)
    session.commit()
    return document, user_id, snapshot.id


def _fake_chunks() -> tuple[ArchiveFinalChunk, ...]:
    """返回固定的无向量 Final Chunk。"""
    return (
        ArchiveFinalChunk(
            chunk_id="c" * 64,
            content="施工方案正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
            snapshot_hash="a" * 64,
            parser_version="archive-parser-v1",
        ),
    )


def _fake_embedded_chunks() -> list[ArchiveEmbeddedChunk]:
    """返回固定的带向量 Final Chunk。"""
    chunk = _fake_chunks()[0]
    return [
        ArchiveEmbeddedChunk(
            chunk_id=chunk.chunk_id,
            content=chunk.content,
            location_type=chunk.location_type,
            location_start=chunk.location_start,
            location_end=chunk.location_end,
            normalized_anchor=chunk.normalized_anchor,
            snapshot_hash=chunk.snapshot_hash,
            parser_version=chunk.parser_version,
            embedding=[0.1, 0.2],
        )
    ]


def _two_fake_chunks() -> tuple[ArchiveFinalChunk, ...]:
    """返回两个定位不同的 Final Chunk，用于验证字段证据不会跨片段扩散。"""
    return (
        ArchiveFinalChunk(
            chunk_id="c" * 64,
            content="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
            snapshot_hash="a" * 64,
            parser_version="archive-parser-v1",
        ),
        ArchiveFinalChunk(
            chunk_id="d" * 64,
            content="例会记录正文",
            location_type="TEXT_LINE_RANGE",
            location_start=2,
            location_end=2,
            normalized_anchor="例会",
            snapshot_hash="a" * 64,
            parser_version="archive-parser-v1",
        ),
    )


def test_index_confirmed_document_records_success_and_chunk_count(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认文档索引成功后应记录 INDEX 成功、快照哈希和 Chunk 数量。"""
    document, _, snapshot_id = _confirmed_document(db_session, tmp_path)
    monkeypatch.setattr(archive_index_service, "build_final_chunks", lambda **_: _fake_chunks())
    monkeypatch.setattr(
        archive_index_service,
        "embed_final_chunks",
        lambda **_: _fake_embedded_chunks(),
    )
    monkeypatch.setattr(archive_index_service, "insert_final_chunks", lambda **_: 1)

    result = archive_index_service.index_confirmed_document(
        document=document,
        session=db_session,
    )

    assert result.chunk_count == 1
    assert result.snapshot_hash == "a" * 64
    operation = db_session.get(ArchiveOperation, result.operation_id)
    archive_document = db_session.get(ArchiveDocument, document.id)
    assert operation is not None
    assert operation.operation_type == ArchiveOperationType.INDEX
    assert operation.operation_status == ArchiveOperationStatus.SUCCEEDED
    assert operation.last_completed_step == "CHROMA_UPSERT"
    assert archive_document is not None
    assert archive_document.current_snapshot_id == snapshot_id
    assert archive_document.final_index_snapshot_hash == "a" * 64
    assert archive_document.final_chunk_count == 1


def test_index_builds_embedding_context_from_confirmed_archive_fields(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """索引只将已确认档案字段作为向量上下文，原文引用仍由 Final Chunk 管理。"""
    document, _, _ = _confirmed_document(db_session, tmp_path)
    db_session.add_all(
        [
            ArchiveFieldValue(
                document_id=document.id,
                field_name=ArchiveFieldName.TITLE,
                text_value="施工方案",
                review_status=FieldReviewStatus.VALUE_CONFIRMED,
            ),
            ArchiveFieldValue(
                document_id=document.id,
                field_name=ArchiveFieldName.PROJECT_STAGE,
                text_value="CONSTRUCTION",
                review_status=FieldReviewStatus.VALUE_CONFIRMED,
            ),
        ]
    )
    db_session.commit()
    captured: dict[str, object] = {}
    monkeypatch.setattr(archive_index_service, "build_final_chunks", lambda **_: _fake_chunks())
    # 本测试验证历史 labeled 语义，不能受开发机 .env 的实验模式影响。
    monkeypatch.setattr(archive_index_service.settings, "archive_embedding_context_mode", "labeled")

    def capture_embedding(**kwargs):
        captured.update(kwargs)
        return _fake_embedded_chunks()

    monkeypatch.setattr(archive_index_service, "embed_final_chunks", capture_embedding)
    monkeypatch.setattr(archive_index_service, "insert_final_chunks", lambda **_: 1)

    archive_index_service.index_confirmed_document(document=document, session=db_session)

    assert captured["embedding_context"] == (
        "档案文件：施工方案.txt\n档案标题：施工方案\n项目阶段：CONSTRUCTION"
    )


def test_embedding_context_modes_keep_only_confirmed_values() -> None:
    """对照实验的三种模式必须保持相同确认字段边界且不污染原文。"""
    document = Document(filename="施工方案.txt")
    fields = [
        ArchiveFieldValue(
            field_name=ArchiveFieldName.TITLE,
            text_value="施工方案",
            review_status=FieldReviewStatus.VALUE_CONFIRMED,
        ),
        ArchiveFieldValue(
            field_name=ArchiveFieldName.PROJECT_STAGE,
            text_value="CONSTRUCTION",
            review_status=FieldReviewStatus.VALUE_CONFIRMED,
        ),
        ArchiveFieldValue(
            field_name=ArchiveFieldName.KEYWORDS,
            json_value=["应急", "演练"],
            review_status=FieldReviewStatus.PENDING_CHECK,
        ),
    ]

    assert archive_index_service.build_embedding_context(
        document=document,
        fields=fields,
        mode="none",
    ) == ""
    assert archive_index_service.build_embedding_context(
        document=document,
        fields=fields,
        mode="values",
    ) == "施工方案\nCONSTRUCTION"
    assert archive_index_service.build_embedding_context(
        document=document,
        fields=fields,
        mode="labeled",
    ) == "档案文件：施工方案.txt\n档案标题：施工方案\n项目阶段：CONSTRUCTION"


def test_evidence_value_contexts_include_only_confirmed_fields_with_current_evidence() -> None:
    """evidence_values 必须按定位分配字段值，并排除无证据或未确认字段。"""
    snapshot_id = UUID("00000000-0000-0000-0000-000000000002")
    title = ArchiveFieldValue(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        field_name=ArchiveFieldName.TITLE,
        text_value="施工方案",
        review_status=FieldReviewStatus.VALUE_CONFIRMED,
    )
    no_source = ArchiveFieldValue(
        id=UUID("00000000-0000-0000-0000-000000000011"),
        field_name=ArchiveFieldName.PROJECT_STAGE,
        text_value="CONSTRUCTION",
        review_status=FieldReviewStatus.VALUE_CONFIRMED,
        no_source_evidence=True,
    )
    pending = ArchiveFieldValue(
        id=UUID("00000000-0000-0000-0000-000000000012"),
        field_name=ArchiveFieldName.KEYWORDS,
        json_value=["例会"],
        review_status=FieldReviewStatus.PENDING_CHECK,
    )
    evidences = [
        FieldEvidence(
            field_value_id=title.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        ),
        FieldEvidence(
            field_value_id=no_source.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        ),
        FieldEvidence(
            field_value_id=pending.id,
            snapshot_id=snapshot_id,
            excerpt="例会记录正文",
            location_type="TEXT_LINE_RANGE",
            location_start=2,
            location_end=2,
            normalized_anchor="例会",
        ),
    ]

    contexts = archive_index_service.build_evidence_value_contexts(
        chunks=_two_fake_chunks(),
        fields=[pending, no_source, title],
        evidences=evidences,
        snapshot_id=snapshot_id,
    )

    assert contexts == {"c" * 64: "施工方案"}


def test_evidence_value_contexts_require_matching_snapshot_anchor_and_stable_order() -> None:
    """证据上下文应拒绝过期或锚点不符记录，并按字段枚举顺序连接。"""
    snapshot_id = UUID("00000000-0000-0000-0000-000000000002")
    title = ArchiveFieldValue(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        field_name=ArchiveFieldName.TITLE,
        text_value="施工方案",
        review_status=FieldReviewStatus.VALUE_CONFIRMED,
    )
    stage = ArchiveFieldValue(
        id=UUID("00000000-0000-0000-0000-000000000011"),
        field_name=ArchiveFieldName.PROJECT_STAGE,
        text_value="CONSTRUCTION",
        review_status=FieldReviewStatus.VALUE_CONFIRMED,
    )
    evidences = [
        FieldEvidence(
            field_value_id=stage.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        ),
        FieldEvidence(
            field_value_id=title.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="不匹配锚点",
        ),
        FieldEvidence(
            field_value_id=title.id,
            snapshot_id=UUID("00000000-0000-0000-0000-000000000003"),
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        ),
        FieldEvidence(
            field_value_id=title.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        ),
    ]

    contexts = archive_index_service.build_evidence_value_contexts(
        chunks=_two_fake_chunks(),
        fields=[stage, title],
        evidences=evidences,
        snapshot_id=snapshot_id,
    )

    assert contexts == {"c" * 64: "施工方案\nCONSTRUCTION"}


def test_index_passes_evidence_contexts_without_changing_legacy_modes(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新模式只传递按 Chunk 分配的字段值，不改变既有全局模式的契约。"""
    document, _, snapshot_id = _confirmed_document(db_session, tmp_path)
    title = ArchiveFieldValue(
        document_id=document.id,
        field_name=ArchiveFieldName.TITLE,
        text_value="施工方案",
        review_status=FieldReviewStatus.VALUE_CONFIRMED,
    )
    db_session.add(title)
    db_session.flush()
    db_session.add(
        FieldEvidence(
            field_value_id=title.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        )
    )
    db_session.commit()
    captured: dict[str, object] = {}
    monkeypatch.setattr(archive_index_service, "build_final_chunks", lambda **_: _two_fake_chunks())
    monkeypatch.setattr(archive_index_service.settings, "archive_embedding_context_mode", "evidence_values")

    def capture_embedding(**kwargs):
        captured.update(kwargs)
        return _fake_embedded_chunks()

    monkeypatch.setattr(archive_index_service, "embed_final_chunks", capture_embedding)
    monkeypatch.setattr(archive_index_service, "insert_final_chunks", lambda **_: 1)

    result = archive_index_service.index_confirmed_document(document=document, session=db_session)

    assert captured["embedding_context"] == ""
    assert captured["embedding_contexts"] == {"c" * 64: "施工方案"}
    assert result.contextual_chunk_count == 1


def test_index_records_safe_evidence_context_count(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """evidence_values 索引应记录不含档案内容或标识的上下文覆盖数量。"""
    document, _, snapshot_id = _confirmed_document(db_session, tmp_path)
    title = ArchiveFieldValue(
        document_id=document.id,
        field_name=ArchiveFieldName.TITLE,
        text_value="施工方案",
        review_status=FieldReviewStatus.VALUE_CONFIRMED,
    )
    db_session.add(title)
    db_session.flush()
    db_session.add(
        FieldEvidence(
            field_value_id=title.id,
            snapshot_id=snapshot_id,
            excerpt="施工阶段正文",
            location_type="TEXT_LINE_RANGE",
            location_start=1,
            location_end=1,
            normalized_anchor="施工",
        )
    )
    db_session.commit()
    monkeypatch.setattr(archive_index_service, "build_final_chunks", lambda **_: _two_fake_chunks())
    monkeypatch.setattr(archive_index_service.settings, "archive_embedding_context_mode", "evidence_values")
    monkeypatch.setattr(archive_index_service, "embed_final_chunks", lambda **_: _fake_embedded_chunks())
    monkeypatch.setattr(archive_index_service, "insert_final_chunks", lambda **_: 1)
    caplog.set_level(logging.INFO, logger=archive_index_service.__name__)

    archive_index_service.index_confirmed_document(document=document, session=db_session)

    assert "archive_index_embedding_context mode=evidence_values contextual_chunk_count=1" in caplog.text
    assert document.filename not in caplog.text
    assert "施工方案" not in caplog.text


def test_index_rejects_other_running_document_operation(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """已有运行中操作时不能并行启动 INDEX。"""
    document, _, _ = _confirmed_document(db_session, tmp_path)
    running = ArchiveOperation(
        document_id=document.id,
        operation_type=ArchiveOperationType.SUGGEST,
        operation_status=ArchiveOperationStatus.RUNNING,
    )
    db_session.add(running)
    db_session.commit()

    with pytest.raises(AppError) as exc_info:
        archive_index_service.index_confirmed_document(
            document=document,
            session=db_session,
        )

    assert exc_info.value.code == "DOCUMENT_OPERATION_IN_PROGRESS"
    operations = db_session.exec(select(ArchiveOperation)).all()
    assert len(operations) == 1


def test_index_failure_marks_operation_failed_without_claiming_success(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """向量写入失败时 INDEX 必须失败并保存稳定错误摘要。"""
    document, _, _ = _confirmed_document(db_session, tmp_path)
    monkeypatch.setattr(archive_index_service, "build_final_chunks", lambda **_: _fake_chunks())
    monkeypatch.setattr(
        archive_index_service,
        "embed_final_chunks",
        lambda **_: _fake_embedded_chunks(),
    )
    monkeypatch.setattr(
        archive_index_service,
        "insert_final_chunks",
        lambda **_: (_ for _ in ()).throw(
            AppError(503, "VECTOR_UNAVAILABLE", "无法连接 Chroma 向量服务。")
        ),
    )

    with pytest.raises(AppError) as exc_info:
        archive_index_service.index_confirmed_document(
            document=document,
            session=db_session,
        )

    assert exc_info.value.code == "VECTOR_UNAVAILABLE"
    operation = db_session.exec(select(ArchiveOperation)).one()
    archive_document = db_session.get(ArchiveDocument, document.id)
    assert operation.operation_status == ArchiveOperationStatus.FAILED
    assert operation.failure_code == "VECTOR_UNAVAILABLE"
    assert archive_document is not None
    assert archive_document.final_chunk_count == 0
    assert archive_document.last_error_code == "VECTOR_UNAVAILABLE"


def test_index_retry_increments_attempt_number_after_failure(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前一次 INDEX 失败后再次执行应递增尝试次数并可成功完成。"""
    document, _, _ = _confirmed_document(db_session, tmp_path)
    failed_operation = ArchiveOperation(
        document_id=document.id,
        operation_type=ArchiveOperationType.INDEX,
        operation_status=ArchiveOperationStatus.FAILED,
        attempt_no=1,
        failure_code="VECTOR_UNAVAILABLE",
    )
    db_session.add(failed_operation)
    db_session.commit()
    monkeypatch.setattr(archive_index_service, "build_final_chunks", lambda **_: _fake_chunks())
    monkeypatch.setattr(
        archive_index_service,
        "embed_final_chunks",
        lambda **_: _fake_embedded_chunks(),
    )
    monkeypatch.setattr(archive_index_service, "insert_final_chunks", lambda **_: 1)

    result = archive_index_service.index_confirmed_document(
        document=document,
        session=db_session,
    )

    operation = db_session.get(ArchiveOperation, result.operation_id)
    assert operation is not None
    assert operation.attempt_no == 2
    assert operation.operation_status == ArchiveOperationStatus.SUCCEEDED


def test_index_reuses_successful_operation_for_same_snapshot(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一快照已有成功 INDEX 时应幂等返回，不重复写入向量。"""
    document, _, _ = _confirmed_document(db_session, tmp_path)
    operation = ArchiveOperation(
        document_id=document.id,
        operation_type=ArchiveOperationType.INDEX,
        operation_status=ArchiveOperationStatus.SUCCEEDED,
        last_completed_step="CHROMA_UPSERT",
    )
    archive_document = db_session.get(ArchiveDocument, document.id)
    assert archive_document is not None
    archive_document.final_chunk_count = 1
    db_session.add_all([operation, archive_document])
    db_session.commit()
    monkeypatch.setattr(
        archive_index_service,
        "insert_final_chunks",
        lambda **_: pytest.fail("同一快照成功索引不应重复写入"),
    )

    result = archive_index_service.index_confirmed_document(
        document=document,
        session=db_session,
    )

    assert result.operation_id == operation.id
    assert result.chunk_count == 1
