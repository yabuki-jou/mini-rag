"""验证 FR-042 P04 的正式目录查询和安全投影。"""

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.core.errors import AppError
from app.models import (
    ArchiveDocument,
    ArchiveDocumentStatus,
    ArchiveDocumentType,
    ArchiveFieldName,
    ArchiveFieldValue,
    ArchiveOperation,
    ArchiveOperationStatus,
    ArchiveOperationType,
    Document,
    FieldEvidence,
    FieldReviewStatus,
    FieldSource,
    ParsedSnapshot,
    Project,
    ProjectStage,
)
from app.services.archive.catalog import (
    list_agent_formal_archives,
    render_agent_catalog_text,
)
from tests.routers.test_project_documents import create_project, create_user, project_document_api


def _add_archive(
    engine: Engine,
    project_id: UUID,
    owner_id: UUID,
    *,
    filename: str,
    confirmed_at: datetime | None,
    document_type: ArchiveDocumentType = ArchiveDocumentType.CONSTRUCTION,
    project_stage: ProjectStage = ProjectStage.CONSTRUCTION,
    title: str = "施工方案",
    document_date: date | None = date(2026, 9, 1),
    organization: str = "甲方单位",
    status: ArchiveDocumentStatus = ArchiveDocumentStatus.CONFIRMED,
    blocked: bool = False,
    source: FieldSource = FieldSource.MANUAL,
    with_evidence: bool = True,
) -> UUID:
    """写入测试所需的最小档案事实，避免调用真实解析和索引流程。"""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        document_id = uuid4()
        document = Document(
            id=document_id,
            kb_id=project.kb_id,
            project_id=project_id,
            filename=filename,
            storage_path=f"tests/{filename}",
            file_hash=uuid4().hex + "0" * 32,
        )
        session.add(document)
        session.flush()
        if status == ArchiveDocumentStatus.CONFIRMED:
            snapshot_id = uuid4()
            archive = ArchiveDocument(
                document_id=document_id,
                status=status,
                current_snapshot_id=snapshot_id,
                confirmed_by=owner_id,
                confirmed_at=confirmed_at,
                final_index_snapshot_hash="a" * 64,
                final_chunk_count=1,
            )
            session.add(archive)
            session.add(
                ParsedSnapshot(
                    id=snapshot_id,
                    document_id=document_id,
                    snapshot_storage_path=f"tests/{filename}.snapshot",
                    snapshot_hash="b" * 64,
                    parser_name="test",
                    parser_version="1",
                    normalization_version="1",
                    text_character_count=10,
                    fragment_count=1,
                )
            )
        else:
            session.add(ArchiveDocument(document_id=document_id, status=status))
        session.flush()
        if status == ArchiveDocumentStatus.CONFIRMED:
            values = {
                ArchiveFieldName.TITLE: ArchiveFieldValue(
                    document_id=document_id,
                    field_name=ArchiveFieldName.TITLE,
                    text_value=title,
                    review_status=FieldReviewStatus.VALUE_CONFIRMED,
                    source=source,
                ),
                ArchiveFieldName.DOCUMENT_TYPE: ArchiveFieldValue(
                    document_id=document_id,
                    field_name=ArchiveFieldName.DOCUMENT_TYPE,
                    text_value=document_type.value,
                    review_status=FieldReviewStatus.VALUE_CONFIRMED,
                    source=source,
                ),
                ArchiveFieldName.DOCUMENT_DATE: ArchiveFieldValue(
                    document_id=document_id,
                    field_name=ArchiveFieldName.DOCUMENT_DATE,
                    date_value=document_date,
                    review_status=(
                        FieldReviewStatus.VALUE_CONFIRMED
                        if document_date is not None
                        else FieldReviewStatus.EMPTY_ACCEPTED
                    ),
                    source=source if document_date is not None else None,
                ),
                ArchiveFieldName.AUTHORING_ORGANIZATION: ArchiveFieldValue(
                    document_id=document_id,
                    field_name=ArchiveFieldName.AUTHORING_ORGANIZATION,
                    text_value=organization,
                    review_status=FieldReviewStatus.VALUE_CONFIRMED,
                    source=source,
                ),
                ArchiveFieldName.PROJECT_STAGE: ArchiveFieldValue(
                    document_id=document_id,
                    field_name=ArchiveFieldName.PROJECT_STAGE,
                    text_value=project_stage.value,
                    review_status=FieldReviewStatus.VALUE_CONFIRMED,
                    source=source,
                ),
            }
            for field in values.values():
                session.add(field)
            session.flush()
            if with_evidence:
                for field in values.values():
                    if field.field_name == ArchiveFieldName.DOCUMENT_DATE and document_date is None:
                        continue
                    session.add(
                        FieldEvidence(
                            field_value_id=field.id,
                            snapshot_id=snapshot_id,
                            excerpt=f"{field.field_name.value} 原文",
                            location_type="TEXT_LINE_RANGE",
                            location_start=1,
                            location_end=1,
                        )
                    )
        if blocked:
            session.add(
                ArchiveOperation(
                    document_id=document_id,
                    operation_type=ArchiveOperationType.DELETE,
                    operation_status=ArchiveOperationStatus.RUNNING,
                    visibility_blocking=True,
                )
            )
        session.commit()
        return document_id


def test_agent_confirmed_document_titles_only_use_visible_formal_scope(
    project_document_api: tuple,
) -> None:
    """证据候选标题只来自当前项目可见正式档案，且不要求标题原文证据。"""
    from app.services.archive.catalog import list_agent_confirmed_document_titles

    client, engine, _ = project_document_api
    user_id = create_user(engine, "agent-evidence-title")
    project_id = UUID(str(create_project(client, engine, user_id)["id"]))
    visible_id = _add_archive(
        engine,
        project_id,
        user_id,
        filename="design.docx",
        title="设计说明",
        confirmed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        with_evidence=False,
    )
    blocked_id = _add_archive(
        engine,
        project_id,
        user_id,
        filename="blocked.docx",
        title="不可见标题",
        confirmed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        blocked=True,
    )
    pending_id = _add_archive(
        engine,
        project_id,
        user_id,
        filename="pending.docx",
        title="待确认标题",
        confirmed_at=None,
        status=ArchiveDocumentStatus.PENDING_CONFIRMATION,
    )

    with Session(engine) as session:
        titles = list_agent_confirmed_document_titles(
            project_id=project_id,
            document_ids={visible_id, blocked_id, pending_id, uuid4()},
            session=session,
        )

    assert titles == {visible_id: "设计说明"}


def test_agent_catalog_enforces_formal_scope_filters_sorting_and_pagination(
    project_document_api: tuple,
) -> None:
    """目录只读当前项目正式档案，并覆盖五类筛选、稳定排序和分页。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine, "catalog-owner")
    project_id = UUID(str(create_project(client, engine, user_id)["id"]))
    first = _add_archive(
        engine,
        project_id,
        user_id,
        filename="first.txt",
        confirmed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    second = _add_archive(
        engine,
        project_id,
        user_id,
        filename="second.txt",
        confirmed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        document_type=ArchiveDocumentType.DESIGN,
        project_stage=ProjectStage.DESIGN,
        document_date=date(2026, 9, 2),
        organization="乙方单位",
    )
    _add_archive(
        engine,
        project_id,
        user_id,
        filename="blocked.txt",
        confirmed_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        blocked=True,
    )
    _add_archive(
        engine,
        project_id,
        user_id,
        filename="pending.txt",
        confirmed_at=None,
        status=ArchiveDocumentStatus.PENDING_CONFIRMATION,
    )

    common = dict(
        project_id=project_id,
        document_type=None,
        project_stage=None,
        document_date_from=None,
        document_date_to=None,
        document_date_is_null=False,
        authoring_organization=None,
        session=Session(engine),
    )
    try:
        page = list_agent_formal_archives(page=1, page_size=1, **common)
        assert page.total == 2
        assert len(page.items) == 1
        assert page.items[0]["filename"] == "second.txt"
        assert list_agent_formal_archives(page=2, page_size=1, **common).items[0]["filename"] == "first.txt"

        assert list_agent_formal_archives(
            page=1,
            page_size=20,
            **{**common, "document_type": ArchiveDocumentType.DESIGN},
        ).items[0]["filename"] == "second.txt"
        assert list_agent_formal_archives(
            page=1,
            page_size=20,
            **{**common, "project_stage": ProjectStage.DESIGN},
        ).total == 1
        assert list_agent_formal_archives(
            page=1,
            page_size=20,
            **{**common, "document_date_from": date(2026, 9, 2)},
        ).total == 1
        assert list_agent_formal_archives(
            page=1,
            page_size=20,
            **{**common, "document_date_to": date(2026, 9, 1)},
        ).total == 1
        assert list_agent_formal_archives(
            page=1,
            page_size=20,
            **{**common, "document_date_is_null": True},
        ).total == 0
        assert list_agent_formal_archives(
            page=1,
            page_size=20,
            **{**common, "authoring_organization": "乙方单位"},
        ).total == 1
        assert first != second
    finally:
        common["session"].close()


def test_agent_catalog_projection_and_text_are_redacted_and_deterministic(
    project_document_api: tuple,
) -> None:
    """目录投影只含五字段安全信息，正文逐字遵守分页模板。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine, "catalog-projection")
    project_id = UUID(str(create_project(client, engine, user_id)["id"]))
    _add_archive(
        engine,
        project_id,
        user_id,
        filename="empty-date.txt",
        confirmed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        document_date=None,
    )
    with Session(engine) as session:
        page = list_agent_formal_archives(
            project_id=project_id,
            page=1,
            page_size=20,
            document_type=None,
            project_stage=None,
            document_date_from=None,
            document_date_to=None,
            document_date_is_null=False,
            authoring_organization=None,
            session=session,
        )
    serialized = str(page)
    assert set(page.items[0]) == {"document_ref", "filename", "confirmed_at", "fields"}
    assert set(page.items[0]["fields"]) == {
        "TITLE",
        "DOCUMENT_TYPE",
        "DOCUMENT_DATE",
        "AUTHORING_ORGANIZATION",
        "PROJECT_STAGE",
    }
    assert all(set(value) == {"value", "source", "has_source_evidence"} for value in page.items[0]["fields"].values())
    assert "未登记" in render_agent_catalog_text(page)
    assert render_agent_catalog_text(page) == (
        "第 1 页，本页 1 份，共 1 份：\n"
        "1. 文件名：empty-date.txt；标题：施工方案（source=MANUAL, has_source_evidence=true）；"
        "资料类型：CONSTRUCTION（source=MANUAL, has_source_evidence=true）；"
        "文档日期：未登记（source=null, has_source_evidence=false）；"
        "编制单位：甲方单位（source=MANUAL, has_source_evidence=true）；"
        "项目阶段：CONSTRUCTION（source=MANUAL, has_source_evidence=true）"
    )
    for forbidden in (str(project_id), "version", "KEYWORDS", "正文", "UUID"):
        assert forbidden not in serialized


def test_agent_catalog_rejects_invalid_page_and_empty_result(
    project_document_api: tuple,
) -> None:
    """目录参数错误受控，空目录保持稳定的空结果。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine, "catalog-empty")
    project_id = UUID(str(create_project(client, engine, user_id)["id"]))
    with Session(engine) as session:
        empty = list_agent_formal_archives(
            project_id=project_id,
            page=1,
            page_size=20,
            document_type=None,
            project_stage=None,
            document_date_from=None,
            document_date_to=None,
            document_date_is_null=False,
            authoring_organization=None,
            session=session,
        )
        assert empty.total == 0
        assert empty.items == []
        with pytest.raises(AppError, match="页大小"):
            list_agent_formal_archives(
                project_id=project_id,
                page=1,
                page_size=21,
                document_type=None,
                project_stage=None,
                document_date_from=None,
                document_date_to=None,
                document_date_is_null=False,
                authoring_organization=None,
                session=session,
            )
        with pytest.raises(AppError, match="日期为空"):
            list_agent_formal_archives(
                project_id=project_id,
                page=1,
                page_size=20,
                document_type=None,
                project_stage=None,
                document_date_from=date(2026, 1, 1),
                document_date_to=None,
                document_date_is_null=True,
                authoring_organization=None,
                session=session,
            )
