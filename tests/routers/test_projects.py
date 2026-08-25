"""验证 FR-030 项目 CRUD、项目隔离和空项目删除边界。"""

from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models import ChecklistItem, Document, KnowledgeBase, Project, User
from tests.support.auth import auth_headers


@pytest.fixture
def project_api() -> Generator[tuple[TestClient, Engine], None, None]:
    """提供隔离 SQLite 数据库和真实的项目路由依赖。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        """让隔离测试执行与 PostgreSQL 一致的外键约束。"""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        """为每个 HTTP 请求建立连接测试数据库的会话。"""
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
    """在测试数据库中创建模拟身份用户，并返回其 UUID。"""
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
    name: str = "示例工程",
    use_demo_checklist: bool = False,
) -> dict[str, object]:
    """经 HTTP 创建项目，供各测试复用。"""
    response = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={
            "name": name,
            "description": "用于路由测试的虚构项目。",
            "use_demo_checklist": use_demo_checklist,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_project_creates_private_knowledge_base_and_demo_checklist(
    project_api: tuple[TestClient, Engine],
) -> None:
    """创建项目应隐藏内部知识库，并在同一事务复制五项虚构清单。"""
    client, engine = project_api
    user_id = create_user(engine, "owner")

    response = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={
            "name": "  示例工程  ",
            "description": "虚构演示项目",
            "use_demo_checklist": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "示例工程"
    assert payload["uses_demo_checklist"] is True
    assert payload["active_document_count"] == 0
    assert payload["version"] == 1
    assert "kb_id" not in payload
    assert "owner_id" not in payload

    with Session(engine) as session:
        project = session.get(Project, UUID(payload["id"]))
        assert project is not None
        knowledge_base = session.get(KnowledgeBase, project.kb_id)
        assert knowledge_base is not None
        assert knowledge_base.owner_id == user_id
        checklist_items = list(
            session.exec(
                select(ChecklistItem).where(ChecklistItem.project_id == project.id)
            ).all()
        )

    assert len(checklist_items) == 5
    assert {item.name for item in checklist_items} == {
        "项目合同",
        "设计说明",
        "施工方案",
        "项目会议纪要",
        "验收报告",
    }


def test_openapi_describes_project_creation_and_virtual_demo_template(
    project_api: tuple[TestClient, Engine],
) -> None:
    """Swagger 契约必须公开项目接口，并说明内置清单只是虚构演示规则。"""
    client, _ = project_api

    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    assert "post" in openapi["paths"]["/projects"]
    create_schema = openapi["components"]["schemas"]["ProjectCreate"]
    assert "不代表法定或行业归档要求" in create_schema["properties"]["use_demo_checklist"]["description"]
    assert "虚构演示规则" in openapi["info"]["description"]


def test_project_name_is_trimmed_and_unique_within_one_user(
    project_api: tuple[TestClient, Engine],
) -> None:
    """同一用户的去首尾空格同名项目必须返回稳定冲突。"""
    client, engine = project_api
    user_id = create_user(engine, "owner")
    create_project(client, engine, user_id, name="示例工程")

    response = client.post(
        "/projects",
        headers=auth_headers(engine, user_id),
        json={"name": "  示例工程  ", "use_demo_checklist": False},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_NAME_CONFLICT"


def test_project_list_only_returns_current_users_projects(
    project_api: tuple[TestClient, Engine],
) -> None:
    """项目列表必须按当前用户范围过滤，并返回公共分页结构。"""
    client, engine = project_api
    owner_id = create_user(engine, "owner")
    other_id = create_user(engine, "other")
    create_project(client, engine, owner_id, name="项目 A")
    create_project(client, engine, owner_id, name="项目 B")
    create_project(client, engine, other_id, name="其他用户项目")

    response = client.get(
        "/projects?page=1&page_size=20",
        headers=auth_headers(engine, owner_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total"] == 2
    assert {item["name"] for item in payload["items"]} == {"项目 A", "项目 B"}


def test_update_project_checks_version_and_keeps_newer_value(
    project_api: tuple[TestClient, Engine],
) -> None:
    """项目更新必须使用乐观锁，旧版本不能覆盖较新的项目字段。"""
    client, engine = project_api
    user_id = create_user(engine, "owner")
    project = create_project(client, engine, user_id)

    update_response = client.patch(
        f"/projects/{project['id']}",
        headers=auth_headers(engine, user_id),
        json={
            "name": "已更名工程",
            "description": "更新后的说明",
            "expected_version": 1,
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["name"] == "已更名工程"
    assert update_response.json()["version"] == 2

    stale_response = client.patch(
        f"/projects/{project['id']}",
        headers=auth_headers(engine, user_id),
        json={"description": "不能覆盖", "expected_version": 1},
    )

    assert stale_response.status_code == 409
    assert stale_response.json()["error"]["code"] == "VERSION_CONFLICT"
    detail_response = client.get(
        f"/projects/{project['id']}",
        headers=auth_headers(engine, user_id),
    )
    assert detail_response.json()["description"] == "更新后的说明"


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_other_user_cannot_access_project(
    project_api: tuple[TestClient, Engine],
    method: str,
) -> None:
    """另一用户读取、修改或删除项目均必须得到所有权拒绝。"""
    client, engine = project_api
    owner_id = create_user(engine, "owner")
    other_id = create_user(engine, "other")
    project = create_project(client, engine, owner_id)
    request_json = {"description": "越权修改", "expected_version": 1} if method == "patch" else None

    response = client.request(
        method,
        f"/projects/{project['id']}",
        headers=auth_headers(engine, other_id),
        json=request_json,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"


def test_delete_empty_project_keeps_its_orphaned_knowledge_base(
    project_api: tuple[TestClient, Engine],
) -> None:
    """删除空项目只清理项目级数据，不得删除可能承载旧资料的知识库。"""
    client, engine = project_api
    user_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, user_id, use_demo_checklist=True)

    with Session(engine) as session:
        project = session.get(Project, UUID(project_payload["id"]))
        assert project is not None
        knowledge_base_id = project.kb_id

    response = client.delete(
        f"/projects/{project_payload['id']}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 204
    with Session(engine) as session:
        assert session.get(Project, UUID(project_payload["id"])) is None
        assert session.get(KnowledgeBase, knowledge_base_id) is not None
        assert list(
            session.exec(
                select(ChecklistItem).where(ChecklistItem.project_id == UUID(project_payload["id"]))
            ).all()
        ) == []


def test_delete_project_with_any_document_is_rejected_without_mutation(
    project_api: tuple[TestClient, Engine],
) -> None:
    """项目只要有现存文档记录，就禁止删除且不能影响原有资源。"""
    client, engine = project_api
    user_id = create_user(engine, "owner")
    project_payload = create_project(client, engine, user_id)
    project_id = UUID(project_payload["id"])

    with Session(engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        document = Document(
            kb_id=project.kb_id,
            project_id=project.id,
            filename="现存资料.txt",
            storage_path="tests/pytest_docs/现存资料.txt",
            file_hash="a" * 64,
        )
        session.add(document)
        session.commit()
        document_id = document.id

    response = client.delete(
        f"/projects/{project_id}",
        headers=auth_headers(engine, user_id),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_HAS_DOCUMENTS"
    with Session(engine) as session:
        assert session.get(Project, project_id) is not None
        assert session.get(Document, document_id) is not None
