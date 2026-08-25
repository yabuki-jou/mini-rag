"""验证 FR-031 清单读取、派生状态和项目隔离。"""

from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import (
    ArchiveAuditLog,
    ArchiveDocument,
    ArchiveDocumentStatus,
    ChecklistItem,
    ChecklistLink,
    ChecklistLinkStatus,
    Document,
    Project,
    User,
    utc_now,
)
from tests.support.auth import auth_headers


@pytest.fixture
def checklist_api() -> Generator[tuple[TestClient, Engine], None, None]:
    """提供带外键约束的隔离 SQLite 数据库和真实项目路由。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        """使测试库执行与 PostgreSQL 一致的外键检查。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        """为每个 HTTP 请求创建连接同一测试数据库的会话。"""
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    try:
        yield client, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_user(engine: Engine, name: str) -> UUID:
    """创建路由认证所需的用户，并返回其 ID。"""
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
    use_demo_checklist: bool,
    name: str = "清单测试工程",
) -> dict[str, object]:
    """通过项目 API 创建测试项目。"""
    response = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={
            "name": name,
            "description": "仅使用虚构资料验证清单读取。",
            "use_demo_checklist": use_demo_checklist,
        },
    )
    assert response.status_code == 201
    return response.json()


def create_confirmed_checklist_link(
    engine: Engine,
    *,
    project_id: UUID,
    owner_id: UUID,
    checklist_item_name: str,
) -> tuple[UUID, UUID]:
    """直接准备后续接口尚未提供的确认档案与确认关联事实。"""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        checklist_item = session.exec(
            select(ChecklistItem).where(
                ChecklistItem.project_id == project_id,
                ChecklistItem.name == checklist_item_name,
            )
        ).one()
        checklist_item_id = checklist_item.id
        document = Document(
            kb_id=project.kb_id,
            project_id=project.id,
            filename="确认施工方案.txt",
            storage_path="tests/pytest_docs/确认施工方案.txt",
            file_hash="a" * 64,
        )
        session.add(document)
        session.commit()
        archive_document = ArchiveDocument(
            document_id=document.id,
            status=ArchiveDocumentStatus.CONFIRMED,
            current_snapshot_id=uuid4(),
            confirmed_by=owner_id,
            confirmed_at=utc_now(),
            final_index_snapshot_hash="b" * 64,
        )
        session.add(archive_document)
        session.commit()
        checklist_link = ChecklistLink(
            document_id=document.id,
            checklist_item_id=checklist_item_id,
            status=ChecklistLinkStatus.CONFIRMED,
            confirmed_by=owner_id,
            confirmed_at=utc_now(),
        )
        link_id = checklist_link.id
        session.add(checklist_link)
        session.commit()
    return checklist_item_id, link_id


def test_list_demo_checklist_derives_missing_and_not_provided(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """无确认关联时，必需项和可选项必须派生为不同的未满足状态。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project = create_project(client, engine, owner_id, use_demo_checklist=True)

    response = client.get(
        f"/projects/{project['id']}/checklist-items",
        headers=auth_headers(engine, owner_id),
    )

    assert response.status_code == 200
    items_by_name = {item["name"]: item for item in response.json()["items"]}
    assert len(items_by_name) == 5
    assert items_by_name["施工方案"]["fulfillment_status"] == "MISSING"
    assert items_by_name["项目会议纪要"]["fulfillment_status"] == "NOT_PROVIDED"
    assert {item["confirmed_document_count"] for item in items_by_name.values()} == {0}


def test_create_checklist_item_increments_project_version_and_writes_redacted_audit(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """创建清单项必须同步写入项目版本和不包含业务文本的审计记录。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=False)
    project_id = UUID(str(project_payload["id"]))

    response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
        json={
            "name": "  施工方案  ",
            "document_type": "CONSTRUCTION",
            "is_required": True,
            "project_stage": "CONSTRUCTION",
            "description": "包含关键工序说明。",
            "expected_project_version": 1,
        },
    )

    assert response.status_code == 201
    response_payload = response.json()
    assert response_payload["project_version"] == 2
    assert response_payload["item"]["name"] == "施工方案"
    assert response_payload["item"]["fulfillment_status"] == "MISSING"
    assert response_payload["item"]["confirmed_document_count"] == 0

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        audit_log = session.exec(
            select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project_id)
        ).one()

    assert project.version == 2
    assert audit_log.operation_type == "CHECKLIST_ITEM_CREATED"
    assert audit_log.resource_type == "CHECKLIST_ITEM"
    assert audit_log.resource_id == UUID(response_payload["item"]["id"])
    assert audit_log.redacted_summary == {
        "document_type": "CONSTRUCTION",
        "is_required": True,
    }


def test_stale_project_version_creates_no_checklist_item_or_audit(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """陈旧创建请求必须返回冲突，且不能留下半成品或成功审计。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=False)
    project_id = UUID(str(project_payload["id"]))
    request_payload = {
        "name": "施工方案",
        "document_type": "CONSTRUCTION",
        "is_required": True,
        "project_stage": "CONSTRUCTION",
        "expected_project_version": 1,
    }

    first_response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
        json=request_payload,
    )
    stale_response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
        json=request_payload,
    )

    assert first_response.status_code == 201
    assert stale_response.status_code == 409
    assert stale_response.json()["error"]["code"] == "VERSION_CONFLICT"
    with Session(engine) as session:
        checklist_items = list(
            session.exec(select(ChecklistItem).where(ChecklistItem.project_id == project_id)).all()
        )
        audit_logs = list(
            session.exec(select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project_id)).all()
        )
        project = session.get(Project, project_id)

    assert len(checklist_items) == 1
    assert len(audit_logs) == 1
    assert project is not None
    assert project.version == 2


def test_list_checklist_counts_only_confirmed_document_and_link(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """只有同项目确认档案和确认关联同时存在时，清单项才会满足。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=True)
    project_id = UUID(str(project_payload["id"]))

    create_confirmed_checklist_link(
        engine,
        project_id=project_id,
        owner_id=owner_id,
        checklist_item_name="施工方案",
    )

    response = client.get(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
    )

    assert response.status_code == 200
    items_by_name = {item["name"]: item for item in response.json()["items"]}
    assert items_by_name["施工方案"]["fulfillment_status"] == "SATISFIED"
    assert items_by_name["施工方案"]["confirmed_document_count"] == 1
    assert items_by_name["项目合同"]["fulfillment_status"] == "MISSING"


def test_list_empty_checklist_returns_empty_items(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """没有复制演示模板的项目不输出项目级缺失结论。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project = create_project(client, engine, owner_id, use_demo_checklist=False)

    response = client.get(
        f"/projects/{project['id']}/checklist-items",
        headers=auth_headers(engine, owner_id),
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_update_required_flag_recomputes_unfulfilled_status(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """必需项改为可选项后，未满足状态必须立即重新派生。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=False)
    project_id = UUID(str(project_payload["id"]))
    create_response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
        json={
            "name": "施工方案",
            "document_type": "CONSTRUCTION",
            "is_required": True,
            "project_stage": "CONSTRUCTION",
            "expected_project_version": 1,
        },
    )
    item_id = UUID(create_response.json()["item"]["id"])

    response = client.patch(
        f"/projects/{project_id}/checklist-items/{item_id}",
        headers=auth_headers(engine, owner_id),
        json={"is_required": False, "expected_version": 1},
    )

    assert response.status_code == 200
    assert response.json()["is_required"] is False
    assert response.json()["fulfillment_status"] == "NOT_PROVIDED"
    assert response.json()["confirmed_document_count"] == 0


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_same_owner_cannot_modify_checklist_item_through_another_project(
    checklist_api: tuple[TestClient, Engine], method: str
) -> None:
    """同一用户也不能借另一个项目 ID 读取、修改或删除清单项。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    first_project = create_project(
        client,
        engine,
        owner_id,
        use_demo_checklist=False,
        name="项目一",
    )
    second_project = create_project(
        client,
        engine,
        owner_id,
        use_demo_checklist=False,
        name="项目二",
    )
    first_project_id = UUID(str(first_project["id"]))
    create_response = client.post(
        f"/projects/{first_project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
        json={
            "name": "施工方案",
            "document_type": "CONSTRUCTION",
            "is_required": True,
            "project_stage": "CONSTRUCTION",
            "expected_project_version": 1,
        },
    )
    item_id = UUID(create_response.json()["item"]["id"])
    target_url = f"/projects/{second_project['id']}/checklist-items/{item_id}"

    if method == "patch":
        response = client.patch(
            target_url,
            headers=auth_headers(engine, owner_id),
            json={"description": "越项目更新", "expected_version": 1},
        )
    else:
        response = client.delete(target_url, headers=auth_headers(engine, owner_id))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHECKLIST_ITEM_NOT_FOUND"
    with Session(engine) as session:
        assert session.get(ChecklistItem, item_id) is not None


def test_other_user_cannot_list_project_checklist(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """项目上下文必须在清单查询前拒绝其他用户。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    other_id = create_user(engine, "other")
    project = create_project(client, engine, owner_id, use_demo_checklist=True)

    response = client.get(
        f"/projects/{project['id']}/checklist-items",
        headers=auth_headers(engine, other_id),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"


def test_other_user_cannot_create_project_checklist_item(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """项目上下文必须在创建清单项前拒绝其他用户。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    other_id = create_user(engine, "other")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=False)
    project_id = UUID(str(project_payload["id"]))

    response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, other_id),
        json={
            "name": "越权清单项",
            "document_type": "CONSTRUCTION",
            "is_required": True,
            "project_stage": "CONSTRUCTION",
            "expected_project_version": 1,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"
    with Session(engine) as session:
        assert session.exec(
            select(ChecklistItem.id).where(ChecklistItem.project_id == project_id)
        ).first() is None


def test_update_description_preserves_link_but_matching_change_invalidates_it(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """说明改动不能破坏确认关联，名称改动必须立即使其失效。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=True)
    project_id = UUID(str(project_payload["id"]))
    item_id, link_id = create_confirmed_checklist_link(
        engine,
        project_id=project_id,
        owner_id=owner_id,
        checklist_item_name="施工方案",
    )

    description_response = client.patch(
        f"/projects/{project_id}/checklist-items/{item_id}",
        headers=auth_headers(engine, owner_id),
        json={"description": "更新后的说明", "expected_version": 1},
    )

    assert description_response.status_code == 200
    assert description_response.json()["fulfillment_status"] == "SATISFIED"
    assert description_response.json()["confirmed_document_count"] == 1
    assert description_response.json()["version"] == 2
    with Session(engine) as session:
        link_after_description_update = session.get(ChecklistLink, link_id)
        assert link_after_description_update is not None
        assert link_after_description_update.status == ChecklistLinkStatus.CONFIRMED

    matching_response = client.patch(
        f"/projects/{project_id}/checklist-items/{item_id}",
        headers=auth_headers(engine, owner_id),
        json={"name": "调整后的施工方案", "expected_version": 2},
    )

    assert matching_response.status_code == 200
    assert matching_response.json()["fulfillment_status"] == "MISSING"
    assert matching_response.json()["confirmed_document_count"] == 0
    assert matching_response.json()["version"] == 3
    with Session(engine) as session:
        link_after_matching_update = session.get(ChecklistLink, link_id)
        assert link_after_matching_update is not None
        audit_logs = list(
            session.exec(
                select(ArchiveAuditLog).where(
                    ArchiveAuditLog.project_id == project_id,
                    ArchiveAuditLog.operation_type == "CHECKLIST_ITEM_UPDATED",
                )
            ).all()
        )

    assert link_after_matching_update.status == ChecklistLinkStatus.INVALIDATED
    assert link_after_matching_update.invalidated_reason == "CHECKLIST_MATCHING_FIELDS_CHANGED"
    assert len(audit_logs) == 2


def test_stale_item_version_does_not_overwrite_newer_checklist_update(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """清单项旧版本更新不能覆盖已保存的新说明或新增成功审计。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=False)
    project_id = UUID(str(project_payload["id"]))
    create_response = client.post(
        f"/projects/{project_id}/checklist-items",
        headers=auth_headers(engine, owner_id),
        json={
            "name": "施工方案",
            "document_type": "CONSTRUCTION",
            "is_required": True,
            "project_stage": "CONSTRUCTION",
            "expected_project_version": 1,
        },
    )
    item_id = UUID(create_response.json()["item"]["id"])

    first_update = client.patch(
        f"/projects/{project_id}/checklist-items/{item_id}",
        headers=auth_headers(engine, owner_id),
        json={"description": "新说明", "expected_version": 1},
    )
    stale_update = client.patch(
        f"/projects/{project_id}/checklist-items/{item_id}",
        headers=auth_headers(engine, owner_id),
        json={"description": "不能覆盖", "expected_version": 1},
    )

    assert first_update.status_code == 200
    assert stale_update.status_code == 409
    assert stale_update.json()["error"]["code"] == "VERSION_CONFLICT"
    with Session(engine) as session:
        checklist_item = session.get(ChecklistItem, item_id)
        audit_logs = list(
            session.exec(select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project_id)).all()
        )

    assert checklist_item is not None
    assert checklist_item.description == "新说明"
    assert checklist_item.version == 2
    assert len(audit_logs) == 2


def test_delete_checklist_item_removes_links_and_keeps_delete_audit(
    checklist_api: tuple[TestClient, Engine],
) -> None:
    """删除清单项必须清理关联，但审计仍可通过资源 ID 追溯该操作。"""
    client, engine = checklist_api
    owner_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, owner_id, use_demo_checklist=True)
    project_id = UUID(str(project_payload["id"]))
    item_id, link_id = create_confirmed_checklist_link(
        engine,
        project_id=project_id,
        owner_id=owner_id,
        checklist_item_name="施工方案",
    )

    response = client.delete(
        f"/projects/{project_id}/checklist-items/{item_id}",
        headers=auth_headers(engine, owner_id),
    )

    assert response.status_code == 204
    with Session(engine) as session:
        assert session.get(ChecklistItem, item_id) is None
        assert session.get(ChecklistLink, link_id) is None
        audit_log = session.exec(
            select(ArchiveAuditLog).where(
                ArchiveAuditLog.project_id == project_id,
                ArchiveAuditLog.operation_type == "CHECKLIST_ITEM_DELETED",
            )
        ).one()

    assert audit_log.resource_id == item_id
    assert audit_log.redacted_summary == {}
