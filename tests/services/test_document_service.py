"""验证 Chunk 身份和文档重新解析的状态一致性。"""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import AppError
from app.models import Document, DocumentStatus, KnowledgeBase, User
from app.services import document_service
from app.services.chunk_service import TextChunk, build_chunk_id
from app.services.embedding_service import EmbeddedChunk
from app.services.parser_service import ParsedPage


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """创建文档处理测试专用的内存 SQLite Session。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def create_processing_records(
    session: Session,
) -> tuple[KnowledgeBase, Document]:
    """创建文档处理服务需要的用户、知识库和文档记录。"""
    user = User(name="owner")
    knowledge_base = KnowledgeBase(owner_id=user.id, name="制度库")
    document = Document(
        kb_id=knowledge_base.id,
        filename="policy.txt",
        storage_path=str(Path("policy.txt").resolve()),
        file_hash="a" * 64,
    )
    session.add(user)
    session.add(knowledge_base)
    session.add(document)
    session.commit()
    return knowledge_base, document


def test_chunk_id_is_stable_and_changes_with_content_or_position() -> None:
    """相同输入应产生相同 ID，正文或位置变化必须改变 ID。"""
    document_id = uuid4()
    base_chunk = TextChunk(
        page=1,
        start_index=0,
        chunk_index=0,
        content="专业培训上限为 3000 元。",
    )
    content_changed = TextChunk(
        page=1,
        start_index=0,
        chunk_index=0,
        content="专业培训上限为 5000 元。",
    )
    position_changed = TextChunk(
        page=2,
        start_index=0,
        chunk_index=1,
        content=base_chunk.content,
    )

    first_id = build_chunk_id(document_id, base_chunk)

    assert first_id == build_chunk_id(document_id, base_chunk)
    assert first_id != build_chunk_id(document_id, content_changed)
    assert first_id != build_chunk_id(document_id, position_changed)


def test_reparse_deletes_old_chunks_before_inserting_new_chunks(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复解析必须先清旧 Chunk，最终数量不能从 2 累积为 4。"""
    knowledge_base, document = create_processing_records(db_session)
    text_chunks = [
        TextChunk(1, 0, 0, "第一段"),
        TextChunk(1, 3, 1, "第二段"),
    ]
    embedded_chunks = [
        EmbeddedChunk(
            chunk_id="a" * 64,
            page=1,
            start_index=0,
            chunk_index=0,
            content="第一段",
            embedding=[0.0],
        ),
        EmbeddedChunk(
            chunk_id="b" * 64,
            page=1,
            start_index=3,
            chunk_index=1,
            content="第二段",
            embedding=[0.0],
        ),
    ]
    events: list[str] = []
    delete_mock = Mock(side_effect=lambda **_: events.append("delete"))
    insert_mock = Mock(
        side_effect=lambda **_: events.append("insert") or 2
    )
    monkeypatch.setattr(document_service, "delete_document_chunks", delete_mock)
    monkeypatch.setattr(
        document_service,
        "parse_document",
        lambda _: [ParsedPage(page=1, content="第一段第二段")],
    )
    monkeypatch.setattr(document_service, "split_pages", lambda _: text_chunks)
    monkeypatch.setattr(
        document_service,
        "embed_chunks",
        lambda *_: embedded_chunks,
    )
    monkeypatch.setattr(document_service, "insert_chunks", insert_mock)

    first_result = document_service.process_document(
        document,
        knowledge_base,
        db_session,
    )
    second_result = document_service.process_document(
        first_result,
        knowledge_base,
        db_session,
    )

    assert events == ["delete", "insert", "delete", "insert"]
    assert delete_mock.call_count == 2
    assert insert_mock.call_count == 2
    assert second_result.status == DocumentStatus.READY
    assert second_result.chunk_count == 2


def test_parse_failure_marks_document_failed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解析异常应持久化 FAILED、清空 Chunk 数并保留安全摘要。"""
    knowledge_base, document = create_processing_records(db_session)
    monkeypatch.setattr(
        document_service,
        "delete_document_chunks",
        Mock(return_value=0),
    )
    monkeypatch.setattr(
        document_service,
        "parse_document",
        Mock(
            side_effect=AppError(
                422,
                "DOCUMENT_PARSE_FAILED",
                "文档解析失败。",
            )
        ),
    )

    with pytest.raises(AppError) as exc_info:
        document_service.process_document(
            document,
            knowledge_base,
            db_session,
        )

    db_session.refresh(document)
    assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
    assert document.status == DocumentStatus.FAILED
    assert document.chunk_count == 0
    assert document.error_message == "文档解析失败。"
