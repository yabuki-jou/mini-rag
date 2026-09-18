"""验证 FR-019～FR-021 的账号密码与 Bearer 认证闭环。"""

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.core.security import create_token
from app.db import get_session
from app.main import app
from app.models import AuthSession, User
from app.services.identity.authentication import set_existing_user_password


TEST_JWT_SECRET = "auth-router-test-secret-that-is-long-enough"


@pytest.fixture
def auth_api(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, Engine], None, None]:
    """提供不读取真实 .env 或 PostgreSQL 的认证 API 隔离环境。"""
    monkeypatch.setattr(settings, "auth_jwt_secret", SecretStr(TEST_JWT_SECRET))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    try:
        yield client, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def register(client: TestClient, username: str = "demo_user") -> dict:
    """注册一名可登录测试用户，并返回不含秘密的响应体。"""
    response = client.post(
        "/auth/register",
        json={"username": username, "name": "演示用户", "password": "safe-password-123"},
    )
    assert response.status_code == 201, response.json()
    return response.json()


def login(client: TestClient, username: str = "demo_user") -> dict:
    """登录测试用户并返回本测试内使用的 Token 对。"""
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "safe-password-123"},
    )
    assert response.status_code == 200, response.json()
    return response.json()


def bearer(token: str) -> dict[str, str]:
    """生成 FastAPI 测试客户端使用的 Authorization 请求头。"""
    return {"Authorization": f"Bearer {token}"}


def test_register_normalizes_username_and_never_returns_password(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """注册应使用全局小写用户名，并且响应不泄露凭据。"""
    client, engine = auth_api
    result = register(client, "Demo_User")

    assert result["username"] == "demo_user"
    assert "password" not in result
    assert "password_hash" not in result
    with Session(engine) as session:
        saved_user = session.get(User, UUID(result["id"]))
        assert saved_user is not None
        assert saved_user.password_hash is not None
        assert saved_user.password_hash != "safe-password-123"


def test_register_rejects_case_normalized_username_conflict(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """大小写不同但规范化后相同的用户名不能创建第二条记录。"""
    client, _ = auth_api
    register(client, "Demo_User")

    response = client.post(
        "/auth/register",
        json={"username": "demo_user", "name": "另一用户", "password": "safe-password-123"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "USERNAME_CONFLICT"


def test_login_returns_generic_error_for_wrong_password_and_unknown_username(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """登录失败不区分用户名是否存在，也不暴露旧用户缺少凭据。"""
    client, engine = auth_api
    register(client)
    with Session(engine) as session:
        session.add(User(username="legacy_user", name="旧用户", password_hash=None))
        session.commit()

    wrong_password = client.post(
        "/auth/login",
        json={"username": "demo_user", "password": "wrong-password"},
    )
    unknown_user = client.post(
        "/auth/login",
        json={"username": "not-found", "password": "wrong-password"},
    )
    old_user = client.post(
        "/auth/login",
        json={"username": "legacy_user", "password": "wrong-password"},
    )

    for response in (wrong_password, unknown_user, old_user):
        assert response.status_code == 401
        assert response.json()["error"] == {
            "code": "INVALID_CREDENTIALS",
            "message": "用户名或密码错误。",
        }


def test_login_refresh_and_bearer_protection(auth_api: tuple[TestClient, Engine]) -> None:
    """Refresh 只返回新 Access，受保护接口只认可 Access Bearer Token。"""
    client, engine = auth_api
    register(client)
    token_pair = login(client)

    assert token_pair["token_type"] == "bearer"
    assert token_pair["access_expires_in"] == 30 * 60
    assert token_pair["refresh_expires_in"] == 7 * 24 * 60 * 60
    with Session(engine) as session:
        access_claims = jwt.decode(
            token_pair["access_token"],
            TEST_JWT_SECRET,
            algorithms=["HS256"],
        )
        auth_session = session.get(AuthSession, UUID(access_claims["sid"]))
        assert auth_session is not None
        assert auth_session.refresh_token_hash != token_pair["refresh_token"]

    refresh_response = client.post("/auth/refresh", json={"refresh_token": token_pair["refresh_token"]})
    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert set(refreshed) == {"access_token", "token_type", "access_expires_in"}
    assert refreshed["access_token"] != token_pair["access_token"]

    assert client.get("/projects", headers=bearer(token_pair["access_token"])).status_code == 200
    assert client.get("/projects").status_code == 401
    assert client.get(
        "/projects",
        headers={"X-User-ID": "00000000-0000-0000-0000-000000000001"},
    ).status_code == 401
    assert client.get("/projects", headers=bearer(token_pair["refresh_token"])).status_code == 401


def test_auth_me_returns_current_user_without_password_fields(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """当前用户接口应返回 UserRead，且只接受 Access Token。"""
    client, _ = auth_api
    registered = register(client)
    token_pair = login(client)

    response = client.get("/auth/me", headers=bearer(token_pair["access_token"]))

    assert response.status_code == 200
    result = response.json()
    assert result == registered
    assert "password" not in result
    assert "password_hash" not in result


def test_auth_me_rejects_missing_and_refresh_tokens(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """当前用户接口缺少认证或使用 Refresh Token 时均应返回 401。"""
    client, _ = auth_api
    register(client)
    token_pair = login(client)

    assert client.get("/auth/me").status_code == 401
    refresh_response = client.get("/auth/me", headers=bearer(token_pair["refresh_token"]))
    assert refresh_response.status_code == 401


def test_logout_revokes_access_and_refresh_tokens(auth_api: tuple[TestClient, Engine]) -> None:
    """注销当前会话后，原 Access 与 Refresh Token 均不能再使用。"""
    client, _ = auth_api
    register(client)
    token_pair = login(client)

    logout_response = client.post("/auth/logout", headers=bearer(token_pair["access_token"]))
    assert logout_response.status_code == 204
    assert client.get("/projects", headers=bearer(token_pair["access_token"])).status_code == 401
    refresh_response = client.post("/auth/refresh", json={"refresh_token": token_pair["refresh_token"]})
    assert refresh_response.status_code == 401
    assert refresh_response.json()["error"]["code"] == "INVALID_TOKEN"


def test_register_rejects_weak_password(auth_api: tuple[TestClient, Engine]) -> None:
    """注册接口应在服务端拒绝短于八位的密码。"""
    client, _ = auth_api

    response = client.post(
        "/auth/register",
        json={"username": "weak_user", "name": "弱密码用户", "password": "short"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_protected_route_rejects_forged_and_expired_access_tokens(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """受保护接口不得接受伪造或已过期的 Access Token。"""
    client, _ = auth_api
    register(client)
    token_pair = login(client)
    claims = jwt.decode(
        token_pair["access_token"],
        TEST_JWT_SECRET,
        algorithms=["HS256"],
    )
    expired_token = create_token(
        user_id=UUID(claims["sub"]),
        session_id=UUID(claims["sid"]),
        token_type="access",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert client.get("/projects", headers=bearer("forged-token")).status_code == 401
    expired_response = client.get("/projects", headers=bearer(expired_token))
    assert expired_response.status_code == 401
    assert expired_response.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_local_legacy_password_initialization_enables_login(
    auth_api: tuple[TestClient, Engine],
) -> None:
    """历史用户只能经本地服务函数补齐凭据，之后才可以登录。"""
    client, engine = auth_api
    legacy_user = User(name="历史用户")
    with Session(engine) as session:
        session.add(legacy_user)
        session.commit()
        legacy_user_id = legacy_user.id

    with Session(engine) as session:
        set_existing_user_password(
            user_id=str(legacy_user_id),
            username="legacy_user",
            password="safe-password-123",
            session=session,
        )

    response = client.post(
        "/auth/login",
        json={"username": "legacy_user", "password": "safe-password-123"},
    )
    assert response.status_code == 200
