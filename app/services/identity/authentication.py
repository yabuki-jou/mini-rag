"""实现 FR-019～FR-021 的注册、登录、刷新和注销业务规则。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from secrets import compare_digest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.core.errors import AppError
from app.core.security import (
    TokenPayload,
    access_token_expiry,
    create_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.models import AuthSession, User, utc_now
from app.schemas.account import (
    AuthLoginRequest,
    AuthRefreshRequest,
    AuthTokenPairRead,
    AuthAccessTokenRead,
    AuthRegisterRequest,
)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """受保护请求已验证的用户和可撤销认证会话。"""

    user: User
    session: AuthSession


def register_user(*, payload: AuthRegisterRequest, session: Session) -> User:
    """注册拥有完整账号密码凭据的新用户。"""
    user = User(
        username=payload.username,
        name=payload.name,
        password_hash=hash_password(payload.password),
    )
    try:
        session.add(user)
        session.commit()
        session.refresh(user)
    except IntegrityError as exc:
        session.rollback()
        raise AppError(409, "USERNAME_CONFLICT", "用户名已被使用。") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "REGISTER_FAILED", "账号注册失败。") from exc
    return user


def login(*, payload: AuthLoginRequest, session: Session) -> AuthTokenPairRead:
    """验证账号密码并创建一个新的可撤销认证会话。"""
    user = session.exec(select(User).where(User.username == payload.username)).first()
    if user is None or user.password_hash is None:
        raise _invalid_credentials()
    if not verify_password(payload.password, user.password_hash):
        raise _invalid_credentials()

    now = datetime.now(timezone.utc)
    refresh_expires_at = refresh_token_expiry(now)
    auth_session = AuthSession(
        user_id=user.id,
        refresh_token_hash="",  # 签发后立即改为摘要；数据库中不会提交空值。
        expires_at=refresh_expires_at,
    )
    refresh_token = create_token(
        user_id=user.id,
        session_id=auth_session.id,
        token_type="refresh",
        expires_at=refresh_expires_at,
    )
    auth_session.refresh_token_hash = hash_refresh_token(refresh_token)
    access_expires_at = access_token_expiry(now)
    access_token = create_token(
        user_id=user.id,
        session_id=auth_session.id,
        token_type="access",
        expires_at=access_expires_at,
    )
    try:
        session.add(auth_session)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(500, "LOGIN_FAILED", "登录会话创建失败。") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "LOGIN_FAILED", "登录会话创建失败。") from exc

    return AuthTokenPairRead(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        access_expires_in=int((access_expires_at - now).total_seconds()),
        refresh_expires_in=int((refresh_expires_at - now).total_seconds()),
    )


def refresh_access_token(*, payload: AuthRefreshRequest, session: Session) -> AuthAccessTokenRead:
    """使用有效且未撤销的 Refresh Token 签发新的 Access Token，不轮换 Refresh Token。"""
    token_payload = decode_token(token=payload.refresh_token, expected_type="refresh")
    auth_session = _get_usable_session(
        token_payload=token_payload,
        token_hash=hash_refresh_token(payload.refresh_token),
        session=session,
    )
    now = datetime.now(timezone.utc)
    expires_at = access_token_expiry(now)
    auth_session.updated_at = utc_now()
    try:
        session.add(auth_session)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "TOKEN_REFRESH_FAILED", "认证令牌刷新失败。") from exc
    return AuthAccessTokenRead(
        access_token=create_token(
            user_id=token_payload.user_id,
            session_id=token_payload.session_id,
            token_type="access",
            expires_at=expires_at,
        ),
        token_type="bearer",
        access_expires_in=int((expires_at - now).total_seconds()),
    )


def authenticate_access_token(*, token: str, session: Session) -> AuthenticatedPrincipal:
    """验证 Access Token，并检查用户和其认证会话当前仍可使用。"""
    token_payload = decode_token(token=token, expected_type="access")
    auth_session = _get_usable_session(token_payload=token_payload, token_hash=None, session=session)
    user = session.get(User, token_payload.user_id)
    if user is None:
        raise AppError(401, "INVALID_TOKEN", "认证令牌无效。")
    return AuthenticatedPrincipal(user=user, session=auth_session)


def logout(*, principal: AuthenticatedPrincipal, session: Session) -> None:
    """撤销当前认证会话；重复注销保持幂等。"""
    if principal.session.revoked_at is not None:
        return
    principal.session.revoked_at = utc_now()
    principal.session.updated_at = utc_now()
    try:
        session.add(principal.session)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "LOGOUT_FAILED", "注销失败。") from exc


def set_existing_user_password(
    *,
    user_id: str,
    username: str,
    password: str,
    session: Session,
) -> None:
    """为指定历史用户初始化登录凭据，不提供 HTTP 后门。"""
    from uuid import UUID

    user = session.get(User, UUID(user_id))
    if user is None:
        raise AppError(404, "USER_NOT_FOUND", "指定用户不存在。")
    duplicate = session.exec(
        select(User.id).where(User.username == username, User.id != user.id)
    ).first()
    if duplicate is not None:
        raise AppError(409, "USERNAME_CONFLICT", "用户名已被使用。")
    user.username = username
    user.password_hash = hash_password(password)
    user.updated_at = utc_now()
    try:
        session.add(user)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(409, "USERNAME_CONFLICT", "用户名已被使用。") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "PASSWORD_INITIALIZATION_FAILED", "密码初始化失败。") from exc


def _get_usable_session(
    *,
    token_payload: TokenPayload,
    token_hash: str | None,
    session: Session,
) -> AuthSession:
    """校验 JWT 声明对应会话、撤销状态和 Refresh Token 摘要。"""
    auth_session = session.get(AuthSession, token_payload.session_id)
    if (
        auth_session is None
        or auth_session.user_id != token_payload.user_id
        or auth_session.revoked_at is not None
        or _is_expired(auth_session.expires_at)
        or (token_hash is not None and not compare_digest(auth_session.refresh_token_hash, token_hash))
    ):
        raise AppError(401, "INVALID_TOKEN", "认证令牌无效或会话已失效。")
    return auth_session


def _is_expired(value: datetime) -> bool:
    """兼容 SQLite 无时区返回值，按 UTC 判断会话是否到期。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def _invalid_credentials() -> AppError:
    """返回不区分用户名和密码原因的登录失败响应。"""
    return AppError(401, "INVALID_CREDENTIALS", "用户名或密码错误。")
