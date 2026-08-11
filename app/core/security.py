"""提供密码哈希和区分用途的 JWT 基础能力。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import settings
from app.core.errors import AppError


JWT_ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]
password_hasher = PasswordHash.recommended()


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """验证通过后可供认证服务使用的最小 JWT 声明。"""

    user_id: UUID
    session_id: UUID
    token_type: TokenType
    expires_at: datetime


def hash_password(password: str) -> str:
    """使用 Argon2 对待保存密码计算单向哈希。"""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证用户提交密码是否匹配数据库中的 Argon2 哈希。"""
    try:
        return password_hasher.verify(password, password_hash)
    except (ValueError, TypeError):
        # 历史或损坏哈希不能泄露具体原因，统一视为认证失败。
        return False


def hash_refresh_token(token: str) -> str:
    """返回 Refresh Token 的 SHA-256 十六进制摘要，供数据库唯一保存。"""
    return sha256(token.encode("utf-8")).hexdigest()


def create_token(
    *,
    user_id: UUID,
    session_id: UUID,
    token_type: TokenType,
    expires_at: datetime,
) -> str:
    """签发含用户、会话、用途和到期时间的 JWT。"""
    secret = _require_jwt_secret()
    issued_at = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "typ": token_type,
            # 同一秒内刷新也必须产生一个新的 Access Token 字符串。
            "jti": str(uuid4()),
            "iat": issued_at,
            "exp": _as_utc(expires_at),
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )


def access_token_expiry(now: datetime | None = None) -> datetime:
    """计算新的 Access Token 到期时间。"""
    return _as_utc(now or datetime.now(timezone.utc)) + timedelta(
        minutes=settings.auth_access_token_minutes
    )


def refresh_token_expiry(now: datetime | None = None) -> datetime:
    """计算新的 Refresh Token 与会话到期时间。"""
    return _as_utc(now or datetime.now(timezone.utc)) + timedelta(
        days=settings.auth_refresh_token_days
    )


def decode_token(*, token: str, expected_type: TokenType) -> TokenPayload:
    """验证签名、到期与用途，并解析认证服务需要的最小声明。"""
    try:
        payload = jwt.decode(
            token,
            _require_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "sid", "typ", "exp"]},
        )
    except ExpiredSignatureError as exc:
        raise AppError(401, "TOKEN_EXPIRED", "认证令牌已过期。") from exc
    except InvalidTokenError as exc:
        raise AppError(401, "INVALID_TOKEN", "认证令牌无效。") from exc

    if payload.get("typ") != expected_type:
        raise AppError(401, "INVALID_TOKEN", "认证令牌用途不正确。")

    try:
        expires_at = datetime.fromtimestamp(float(payload["exp"]), tz=timezone.utc)
        return TokenPayload(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            token_type=expected_type,
            expires_at=expires_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(401, "INVALID_TOKEN", "认证令牌声明无效。") from exc


def _require_jwt_secret() -> str:
    """读取非空 JWT 密钥；未配置时拒绝认证而非使用示例默认值。"""
    if settings.auth_jwt_secret is None:
        raise AppError(503, "AUTH_NOT_CONFIGURED", "认证服务尚未配置。")
    secret = settings.auth_jwt_secret.get_secret_value().strip()
    if not secret or secret == "replace-with-a-long-random-secret":
        raise AppError(503, "AUTH_NOT_CONFIGURED", "认证服务尚未配置。")
    return secret


def _as_utc(value: datetime) -> datetime:
    """将 SQLite 可能返回的无时区时间按 UTC 解释，统一用于安全比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
