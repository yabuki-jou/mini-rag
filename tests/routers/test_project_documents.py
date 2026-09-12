"""验证 FR-032 项目内上传接口的 HTTP 契约。"""

from collections.abc import Generator
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.db import get_session
from app.main import app
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    Document,
    DocumentStatus,
    ParsedSnapshot,
    Project,
    User,
)
import app.services.document_service as document_service_module
from app.services.archive_parser_service import (
    ArchiveLocationType,
    ParsedArchiveDocument,
    ParsedFragment,
)
from tests.support.auth import auth_headers
from tests.support.archive_files import docx_bytes, text_pdf_bytes


@pytest.fixture
def project_document_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, Engine, Path], None, None]:
    """提供项目上传接口所需的隔离 SQLite 数据库和原文件目录。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        """让测试库验证项目、文档和归档扩展记录的外键关系。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    storage_root = tmp_path / "files"
    monkeypatch.setattr(settings, "file_storage_dir", storage_root)

    def override_get_session() -> Generator[Session, None, None]:
        """为每个请求创建连接同一测试数据库的 Session。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    try:
        yield client, engine, storage_root
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_user(engine: Engine, name: str = "owner") -> UUID:
    """创建可通过生产 Bearer 依赖认证的项目所有者。"""
    user = User(name=name)
    user_id = user.id
    with Session(engine) as session:
        session.add(user)
        session.commit()
    return user_id


def create_project(
    client: TestClient,
    engine: Engine,
    user_id: UUID,
    *,
    name: str = "上传测试工程",
) -> dict[str, object]:
    """通过现有项目 API 创建上传目标，避免测试伪造项目授权范围。"""
    response = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={
            "name": name,
            "description": "仅使用虚构资料验证项目内上传。",
            "use_demo_checklist": False,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_upload_txt_creates_unparsed_project_document_and_archive_record(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """合法 TXT 上传必须只创建项目内原文件和两条 UPLOADED 记录。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))
    file_content = "虚构施工方案正文。".encode("utf-8")

    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("施工方案.txt", file_content, "text/plain")},
    )

    assert response.status_code == 201
    payload = response.json()
    document_id = UUID(payload["id"])
    assert payload["filename"] == "施工方案.txt"
    assert payload["file_hash"] == sha256(file_content).hexdigest()
    assert payload["status"] == "UPLOADED"
    assert "kb_id" not in payload
    assert "storage_path" not in payload

    with Session(engine) as session:
        project = session.get(Project, project_id)
        document = session.get(Document, document_id)
        archive_document = session.get(ArchiveDocument, document_id)

    assert project is not None
    assert project.active_document_count == 1
    assert document is not None
    assert document.project_id == project_id
    assert document.kb_id == project.kb_id
    assert document.status == DocumentStatus.UPLOADED
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.UPLOADED
    assert archive_document.current_snapshot_id is None
    stored_files = list(storage_root.rglob("施工方案.txt"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == file_content


def test_parse_uploaded_txt_creates_parsed_snapshot(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """项目内 TXT 普通解析必须生成定位快照并进入 PARSED。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))
    file_content = (
        "项目施工方案标题\n编制单位：示例建设公司\n版本号：V1.0\n"
        "施工阶段：主体结构。"
    ).encode("utf-8")

    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("施工方案.txt", file_content, "text/plain")},
    )
    document_id = UUID(upload_response.json()["id"])

    parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )

    assert upload_response.status_code == 201
    assert parse_response.status_code == 200
    assert parse_response.json()["status"] == "PARSED"
    assert parse_response.json()["last_error"] == {"code": None, "message": None}

    with Session(engine) as session:
        document = session.get(Document, document_id)
        archive_document = session.get(ArchiveDocument, document_id)
        snapshot = session.exec(
            select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
        ).one()

    assert document is not None
    assert document.status == DocumentStatus.UPLOADED
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PARSED
    assert archive_document.current_snapshot_id == snapshot.id
    assert snapshot.snapshot_hash
    assert len(snapshot.snapshot_hash) == 64
    assert snapshot.parser_version == "archive-v1-parser-v1"
    assert snapshot.text_character_count > 20
    assert snapshot.fragment_count == 4

    snapshot_path = Path(snapshot.snapshot_storage_path)
    assert snapshot_path.is_file()
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_payload["document_id"] == str(document_id)
    assert snapshot_payload["snapshot_hash"] == snapshot.snapshot_hash
    assert snapshot_payload["fragments"][0]["location_start"] == 1
    stored_files = [path for path in storage_root.rglob("*") if path.is_file()]
    assert len(stored_files) == 2


def test_parse_short_txt_persists_parse_failed_summary(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """有效文本不足时必须保留原文档并记录可重试的受控失败。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))

    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("空壳资料.txt", "过短文本".encode("utf-8"), "text/plain")},
    )
    document_id = UUID(upload_response.json()["id"])

    parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )

    assert upload_response.status_code == 201
    assert parse_response.status_code == 422
    assert parse_response.json() == {
        "error": {
            "code": "PARSE_TEXT_UNAVAILABLE",
            "message": "文档未提取到足够的有效文本。",
        }
    }

    with Session(engine) as session:
        document = session.get(Document, document_id)
        archive_document = session.get(ArchiveDocument, document_id)
        snapshots = list(
            session.exec(
                select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
            ).all()
        )

    assert document is not None
    assert document.status == DocumentStatus.UPLOADED
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PARSE_FAILED
    assert archive_document.current_snapshot_id is None
    assert archive_document.last_error_code == "PARSE_TEXT_UNAVAILABLE"
    assert archive_document.last_error_summary == "文档未提取到足够的有效文本。"
    assert snapshots == []


def test_parse_retry_keeps_parse_failed_for_repeated_failure(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """失败记录重试仍失败时必须更新同一受控错误而不生成快照。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))

    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("空壳资料.txt", "过短文本".encode("utf-8"), "text/plain")},
    )
    document_id = UUID(upload_response.json()["id"])
    first_parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )

    retry_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse-retry",
        headers=auth_headers(engine, user_id),
    )

    assert upload_response.status_code == 201
    assert first_parse_response.status_code == 422
    assert retry_response.status_code == 422
    assert retry_response.json() == {
        "error": {
            "code": "PARSE_TEXT_UNAVAILABLE",
            "message": "文档未提取到足够的有效文本。",
        }
    }

    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        snapshots = list(
            session.exec(
                select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
            ).all()
        )

    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PARSE_FAILED
    assert archive_document.last_error_code == "PARSE_TEXT_UNAVAILABLE"
    assert archive_document.last_error_summary == "文档未提取到足够的有效文本。"
    assert snapshots == []
    with Session(engine) as session:
        assert session.exec(
            select(ArchiveAuditLog).where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.operation_type == "PARSE_RETRIED",
            )
        ).all() == []


def test_parse_retry_recovers_failed_document_without_reupload(
    project_document_api: tuple[TestClient, Engine, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解析器暂时失败后，专用重试必须复用原文件并进入 PARSED。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))

    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("待重试资料.txt", "过短文本".encode("utf-8"), "text/plain")},
    )
    document_id = UUID(upload_response.json()["id"])
    first_parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )

    retry_fragment = ParsedFragment(
        location_type=ArchiveLocationType.TEXT_LINE_RANGE,
        location_start=1,
        location_end=1,
        content="重试后恢复的项目施工资料正文，包含足够的有效文本。",
    )
    monkeypatch.setattr(
        document_service_module,
        "parse_archive_document",
        lambda _: ParsedArchiveDocument(
            fragments=(retry_fragment,),
            effective_text_characters=len(retry_fragment.content),
            snapshot_hash="a" * 64,
        ),
    )

    retry_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse-retry",
        headers=auth_headers(engine, user_id),
    )

    assert upload_response.status_code == 201
    assert first_parse_response.status_code == 422
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "PARSED"
    assert retry_response.json()["last_error"] == {"code": None, "message": None}

    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        snapshot = session.exec(
            select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
        ).one()

    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PARSED
    assert archive_document.current_snapshot_id == snapshot.id
    assert snapshot.snapshot_hash == "a" * 64
    with Session(engine) as session:
        audit_logs = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.project_id == project_id,
                    ArchiveAuditLog.operation_type == "PARSE_RETRIED",
                )
            ).all()
        )
    assert len(audit_logs) == 1
    assert audit_logs[0].actor_id == user_id
    assert audit_logs[0].redacted_summary == {"status": "PARSED"}


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("项目资料.txt", "text/plain"),
        ("项目资料.md", "text/markdown"),
        ("项目资料.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("project-material.pdf", "application/pdf"),
    ],
)
def test_parse_project_document_supports_all_frozen_formats(
    project_document_api: tuple[TestClient, Engine, Path],
    filename: str,
    content_type: str,
) -> None:
    """项目解析路由必须复用 P01 冻结的 PDF/DOCX/TXT/MD 四种定位规则。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))
    extension = Path(filename).suffix
    content = {
        ".txt": "项目施工资料正文，包含足够的有效文本用于路由验收。".encode("utf-8"),
        ".md": "# 项目资料\n项目施工资料正文，包含足够的有效文本用于路由验收。".encode("utf-8"),
        ".docx": docx_bytes("项目施工资料正文，包含足够的有效文本用于路由验收。"),
        ".pdf": text_pdf_bytes("Project material contains enough extractable text."),
    }[extension]

    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": (filename, content, content_type)},
    )
    document_id = UUID(upload_response.json()["id"])
    parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )

    assert upload_response.status_code == 201
    assert parse_response.status_code == 200
    assert parse_response.json()["status"] == "PARSED"
    with Session(engine) as session:
        archive_document = session.get(ArchiveDocument, document_id)
        snapshot = session.exec(
            select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
        ).one()
    assert archive_document is not None
    assert archive_document.status == ArchiveDocumentStatus.PARSED
    assert snapshot.fragment_count > 0
    assert Path(snapshot.snapshot_storage_path).is_file()


def test_parse_again_after_parsed_does_not_replace_snapshot(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """普通解析重复调用必须稳定拒绝且不替换已有快照。"""
    client, engine, _ = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))
    content = "项目施工资料正文，包含足够的有效文本用于快照保护验收。".encode("utf-8")

    upload_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("项目资料.txt", content, "text/plain")},
    )
    document_id = UUID(upload_response.json()["id"])
    first_parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )
    with Session(engine) as session:
        first_snapshot = session.exec(
            select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
        ).one()
        snapshot_path = Path(first_snapshot.snapshot_storage_path)
        first_snapshot_id = first_snapshot.id
        first_snapshot_bytes = snapshot_path.read_bytes()

    second_parse_response = client.post(
        f"/projects/{project_id}/documents/{document_id}/parse",
        headers=auth_headers(engine, user_id),
    )

    assert upload_response.status_code == 201
    assert first_parse_response.status_code == 200
    assert second_parse_response.status_code == 409
    assert second_parse_response.json() == {
        "error": {
            "code": "PARSE_NOT_ALLOWED",
            "message": "当前文档状态不允许普通解析。",
        }
    }
    assert snapshot_path.read_bytes() == first_snapshot_bytes
    with Session(engine) as session:
        snapshots = list(
            session.exec(
                select(ParsedSnapshot).where(ParsedSnapshot.document_id == document_id)
            ).all()
        )
    assert len(snapshots) == 1
    assert snapshots[0].id == first_snapshot_id


def test_duplicate_project_file_returns_existing_summary_without_new_resources(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """同项目相同哈希必须返回已有文档摘要，且不留下第二份资源。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))
    file_content = "虚构且完全相同的项目资料。".encode("utf-8")

    first_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("项目资料.txt", file_content, "text/plain")},
    )
    duplicate_response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("相同内容副本.txt", file_content, "text/plain")},
    )

    assert first_response.status_code == 201
    first_payload = first_response.json()
    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {
        "error": {
            "code": "DUPLICATE_FILE",
            "message": "当前项目已存在相同原文件。",
            "details": {
                "id": first_payload["id"],
                "filename": "项目资料.txt",
                "status": "UPLOADED",
            },
        }
    }

    with Session(engine) as session:
        project = session.get(Project, project_id)
        documents = list(
            session.exec(select(Document).where(Document.project_id == project_id)).all()
        )
        archive_documents = list(session.exec(select(ArchiveDocument)).all())

    assert project is not None
    assert project.active_document_count == 1
    assert [document.id for document in documents] == [UUID(first_payload["id"])]
    assert [archive_document.document_id for archive_document in archive_documents] == [
        UUID(first_payload["id"])
    ]
    stored_files = [path for path in storage_root.rglob("*") if path.is_file()]
    assert [path.name for path in stored_files] == ["项目资料.txt"]


def test_same_file_hash_can_be_uploaded_to_another_project_without_leakage(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """同一用户的不同项目允许相同哈希，响应不得泄露另一项目的内部范围。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    first_project = create_project(client, engine, user_id, name="第一上传工程")
    second_project = create_project(client, engine, user_id, name="第二上传工程")
    first_project_id = UUID(str(first_project["id"]))
    second_project_id = UUID(str(second_project["id"]))
    file_content = "两个项目均可使用的虚构资料。".encode("utf-8")

    first_response = client.post(
        f"/projects/{first_project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("共享资料.txt", file_content, "text/plain")},
    )
    second_response = client.post(
        f"/projects/{second_project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("共享资料.txt", file_content, "text/plain")},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["id"] != second_payload["id"]
    assert first_payload["file_hash"] == second_payload["file_hash"]
    assert "project_id" not in second_payload
    assert "kb_id" not in second_payload
    assert "storage_path" not in second_payload

    with Session(engine) as session:
        first_project_record = session.get(Project, first_project_id)
        second_project_record = session.get(Project, second_project_id)
        first_documents = list(
            session.exec(
                select(Document).where(Document.project_id == first_project_id)
            ).all()
        )
        second_documents = list(
            session.exec(
                select(Document).where(Document.project_id == second_project_id)
            ).all()
        )

    assert first_project_record is not None
    assert second_project_record is not None
    assert first_project_record.active_document_count == 1
    assert second_project_record.active_document_count == 1
    assert [str(document.id) for document in first_documents] == [first_payload["id"]]
    assert [str(document.id) for document in second_documents] == [second_payload["id"]]
    stored_files = [path for path in storage_root.rglob("共享资料.txt") if path.is_file()]
    assert len(stored_files) == 2


def test_other_user_cannot_upload_document_to_project(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """项目上下文必须在文件预检和落盘前拒绝其他用户上传。"""
    client, engine, storage_root = project_document_api
    owner_id = create_user(engine, "owner")
    other_user_id = create_user(engine, "other")
    project_payload = create_project(client, engine, owner_id)
    project_id = UUID(str(project_payload["id"]))

    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, other_user_id),
        files={"file": ("越权资料.txt", b"unauthorized upload", "text/plain")},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"

    with Session(engine) as session:
        project = session.get(Project, project_id)
        documents = list(
            session.exec(select(Document).where(Document.project_id == project_id)).all()
        )
        archive_documents = list(session.exec(select(ArchiveDocument)).all())

    assert project is not None
    assert project.active_document_count == 0
    assert documents == []
    assert archive_documents == []
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []


def test_upload_larger_than_twenty_mebibytes_creates_no_project_resources(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """超过 FR-032 的 20 MiB 限制时，不得写入文件或创建任何业务记录。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))
    oversized_content = b"x" * (20 * 1024 * 1024 + 1)

    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("超限资料.txt", oversized_content, "text/plain")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "FILE_TOO_LARGE",
            "message": "上传文件不能超过 20 MiB。",
        }
    }

    with Session(engine) as session:
        project = session.get(Project, project_id)
        documents = list(
            session.exec(select(Document).where(Document.project_id == project_id)).all()
        )
        archive_documents = list(session.exec(select(ArchiveDocument)).all())

    assert project is not None
    assert project.active_document_count == 0
    assert documents == []
    assert archive_documents == []
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []


def test_upload_when_project_has_one_hundred_documents_creates_no_resources(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """项目容量达到 100 份时，必须在原文件落盘前拒绝新的上传。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))

    # active_document_count 是项目聚合内维护的容量事实；数据库约束只负责阻止超过 100。
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        project.active_document_count = 100
        session.add(project)
        session.commit()

    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("第101份资料.txt", b"fictional archive document", "text/plain")},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_DOCUMENT_LIMIT_REACHED"

    with Session(engine) as session:
        project = session.get(Project, project_id)
        documents = list(
            session.exec(select(Document).where(Document.project_id == project_id)).all()
        )
        archive_documents = list(session.exec(select(ArchiveDocument)).all())

    assert project is not None
    assert project.active_document_count == 100
    assert documents == []
    assert archive_documents == []
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []


def test_upload_unsupported_file_type_creates_no_project_resources(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """不支持的扩展名必须按 API 契约返回 415，且不能产生半成品。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))

    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("恶意程序.exe", b"not an archive document", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED"

    with Session(engine) as session:
        project = session.get(Project, project_id)
        documents = list(
            session.exec(select(Document).where(Document.project_id == project_id)).all()
        )
        archive_documents = list(session.exec(select(ArchiveDocument)).all())

    assert project is not None
    assert project.active_document_count == 0
    assert documents == []
    assert archive_documents == []
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []


def test_unsupported_file_type_precedes_project_capacity_limit(
    project_document_api: tuple[TestClient, Engine, Path],
) -> None:
    """类型、大小、容量同时可能失败时，必须遵守 FR-032 的预检顺序。"""
    client, engine, storage_root = project_document_api
    user_id = create_user(engine)
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(str(project_payload["id"]))

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        project.active_document_count = 100
        session.add(project)
        session.commit()

    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_headers(engine, user_id),
        files={"file": ("满容量恶意程序.exe", b"unsupported", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED"

    with Session(engine) as session:
        project = session.get(Project, project_id)
        documents = list(
            session.exec(select(Document).where(Document.project_id == project_id)).all()
        )
        archive_documents = list(session.exec(select(ArchiveDocument)).all())

    assert project is not None
    assert project.active_document_count == 100
    assert documents == []
    assert archive_documents == []
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []
