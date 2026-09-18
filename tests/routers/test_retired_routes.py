"""验证通用 RAG 与旧制度 Agent 路由已从公开 API 下线。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


RETIRED_PATHS = (
    "/knowledge-bases",
    "/knowledge-bases/{kb_id}/documents",
    "/knowledge-bases/{kb_id}/retrieval-test",
    "/chat-sessions",
    "/agent-sessions",
)
ARCHIVE_AGENT_SESSION_PATH = "/projects/{project_id}/agent-sessions"


def test_retired_routes_are_absent_from_openapi_and_return_default_404() -> None:
    """旧入口既不出现在契约中，也不保留兼容路由。"""
    client = TestClient(app)
    openapi_paths = client.get("/openapi.json").json()["paths"]

    for path in RETIRED_PATHS:
        assert path not in openapi_paths

    requests = (
        ("GET", "/knowledge-bases"),
        ("POST", "/knowledge-bases"),
        ("GET", f"/knowledge-bases/{uuid4()}/documents"),
        ("POST", f"/knowledge-bases/{uuid4()}/documents"),
        ("POST", f"/knowledge-bases/{uuid4()}/retrieval-test"),
        ("POST", "/chat-sessions"),
        ("POST", "/agent-sessions"),
    )
    for method, path in requests:
        response = client.request(method, path)
        assert response.status_code == 404


def test_archive_agent_session_route_remains_in_openapi() -> None:
    """智慧档案 Agent 的项目嵌套路由继续作为当前入口。"""
    client = TestClient(app)

    openapi_paths = client.get("/openapi.json").json()["paths"]

    assert ARCHIVE_AGENT_SESSION_PATH in openapi_paths
