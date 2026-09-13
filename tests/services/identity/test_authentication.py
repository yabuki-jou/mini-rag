"""验证身份服务的账号凭据和可撤销令牌行为。"""

from collections.abc import Generator
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.core.errors import AppError
from app.models import AuthSession, User
from app.schemas.account import (
    AuthLoginRequest,
    AuthRefreshRequest,
    AuthRegisterRequest,
)
from app.services.identity.authentication import (
    authenticate_access_token,
    login,
    logout,
    refresh_access_token,
    register_user,
    set_existing_user_password,
)


TEST_JWT_SECRET = "service-auth-test-secret-that-is-long-enough"


@pytest.fixture
def db_session(monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    """创建只供身份服务单测使用的内存 SQLite 会话。"""
    monkeypatch.setattr(settings, "auth_jwt_secret", SecretStr(TEST_JWT_SECRET))
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def register_payload(username: str = "service_user") -> AuthRegisterRequest:
    """创建测试注册请求。"""
    return AuthRegisterRequest(
        username=username,
        name="服务测试用户",
        password="safe-password-123",
    )


def login_payload(username: str = "service_user") -> AuthLoginRequest:
    """创建测试登录请求。"""
    return AuthLoginRequest(username=username, password="safe-password-123")


def assert_error(error: AppError, code: str) -> None:
    """断言业务错误只暴露稳定代码。"""
    assert error.code == code


def test_register_stores_only_password_hash(db_session: Session) -> None:
    """注册应保存单向密码哈希而不是明文密码。"""
    user = register_user(payload=register_payload(), session=db_session)

    saved = db_session.get(User, user.id)
    assert saved is not None
    assert saved.password_hash
    assert saved.password_hash != "safe-password-123"
    assert "safe-password-123" not in saved.password_hash


def test_register_duplicate_username_has_stable_conflict(db_session: Session) -> None:
    """重复用户名应统一映射为 USERNAME_CONFLICT。"""
    register_user(payload=register_payload(), session=db_session)

    with pytest.raises(AppError) as exc_info:
        register_user(payload=register_payload(), session=db_session)

    assert_error(exc_info.value, "USERNAME_CONFLICT")


def test_login_unknown_user_and_wrong_password_are_indistinguishable(
    db_session: Session,
) -> None:
    """未知用户和错误密码应返回同一个认证错误。"""
    register_user(payload=register_payload(), session=db_session)

    with pytest.raises(AppError) as unknown:
        login(
            payload=AuthLoginRequest(username="missing_user", password="wrong"),
            session=db_session,
        )
    with pytest.raises(AppError) as wrong:
        login(
            payload=AuthLoginRequest(username="service_user", password="wrong"),
            session=db_session,
        )

    assert_error(unknown.value, "INVALID_CREDENTIALS")
    assert_error(wrong.value, "INVALID_CREDENTIALS")


def test_login_authenticate_refresh_and_logout_revoke_both_tokens(
    db_session: Session,
) -> None:
    """刷新不轮换 Refresh Token，注销后两类令牌都应失效。"""
    user = register_user(payload=register_payload(), session=db_session)
    token_pair = login(payload=login_payload(), session=db_session)
    original_refresh = token_pair.refresh_token
    auth_session = db_session.exec(
        select(AuthSession).where(AuthSession.user_id == user.id)
    ).one()
    original_refresh_hash = auth_session.refresh_token_hash

    principal = authenticate_access_token(
        token=token_pair.access_token,
        session=db_session,
    )
    assert principal.user.id == user.id
    refreshed = refresh_access_token(
        payload=AuthRefreshRequest(refresh_token=original_refresh),
        session=db_session,
    )
    assert refreshed.access_token
    saved_auth_session = db_session.get(AuthSession, auth_session.id)
    assert saved_auth_session is not None
    assert saved_auth_session.refresh_token_hash == original_refresh_hash

    logout(principal=principal, session=db_session)
    logout(principal=principal, session=db_session)

    with pytest.raises(AppError) as access_error:
        authenticate_access_token(token=token_pair.access_token, session=db_session)
    with pytest.raises(AppError) as refresh_error:
        refresh_access_token(
            payload=AuthRefreshRequest(refresh_token=original_refresh),
            session=db_session,
        )
    assert_error(access_error.value, "INVALID_TOKEN")
    assert_error(refresh_error.value, "INVALID_TOKEN")


def test_set_existing_user_password_success_and_errors(db_session: Session) -> None:
    """历史用户初始化密码应支持成功、缺失用户和用户名冲突分支。"""
    user = User(name="历史用户")
    other = User(username="occupied", name="已有用户")
    db_session.add(user)
    db_session.add(other)
    db_session.commit()

    set_existing_user_password(
        user_id=str(user.id),
        username="legacy_user",
        password="legacy-password-123",
        session=db_session,
    )
    saved = db_session.get(User, user.id)
    assert saved is not None
    assert saved.username == "legacy_user"
    assert saved.password_hash
    assert saved.password_hash != "legacy-password-123"

    with pytest.raises(AppError) as missing:
        set_existing_user_password(
            user_id=str(uuid4()),
            username="missing_user",
            password="legacy-password-123",
            session=db_session,
        )
    with pytest.raises(AppError) as conflict:
        set_existing_user_password(
            user_id=str(user.id),
            username="occupied",
            password="legacy-password-123",
            session=db_session,
        )
    assert_error(missing.value, "USER_NOT_FOUND")
    assert_error(conflict.value, "USERNAME_CONFLICT")
