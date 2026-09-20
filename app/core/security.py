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
    """验证通过后可供认证服务使用的最小 JWT 声明。

    Attributes:
        user_id: Token 所属用户 UUID。
        session_id: 数据库认证会话 UUID。
        token_type: Token 的用途类型。
        expires_at: 已解析的 UTC 到期时间。
    """

    user_id: UUID
    session_id: UUID
    token_type: TokenType
    expires_at: datetime


def hash_password(password: str) -> str:
    """使用 Argon2 对待保存密码计算单向哈希。

    Args:
        password: 用户提交的明文密码；只在调用期间存在，不应记录或返回。

    Returns:
        可保存到数据库、但不能还原明文密码的 Argon2 哈希。
    """
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证用户提交密码是否匹配数据库中的 Argon2 哈希。

    Args:
        password: 用户提交的明文密码。
        password_hash: 数据库中保存的 Argon2 哈希。

    Returns:
        密码匹配且哈希格式有效时返回 ``True``；其他情况返回 ``False``。
    """
    try:
        return password_hasher.verify(password, password_hash)
    except (ValueError, TypeError):
        # 历史或损坏哈希不能泄露具体原因，统一视为认证失败。
        return False


def hash_refresh_token(token: str) -> str:
    """返回 Refresh Token 的 SHA-256 十六进制摘要，供数据库唯一保存。

    Args:
        token: 仅在当前调用中可见的 Refresh Token 明文。

    Returns:
        可保存到数据库、不可逆还原原 Token 的 SHA-256 十六进制摘要。
    """
    return sha256(token.encode("utf-8")).hexdigest()


def create_token(
    *,
    user_id: UUID,
    session_id: UUID,
    token_type: TokenType,
    expires_at: datetime,
) -> str:
    """签发含用户、会话、用途和到期时间的 JWT。

    Args:
        user_id: Token 所属用户 UUID。
        session_id: 数据库认证会话 UUID。
        token_type: ``access`` 或 ``refresh``，用于隔离 Token 用途。
        expires_at: 绝对到期时间；无时区值按 UTC 解释。

    Returns:
        使用配置中的 JWT 密钥和 HS256 签名的 Token 字符串。

    Raises:
        AppError: JWT 密钥未配置或仍使用示例占位值。
    """
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
    """计算新的 Access Token 到期时间。

    Args:
        now: 计算基准时间；省略时使用当前 UTC 时间。

    Returns:
        按配置分钟数计算、带 UTC 时区的到期时间。
    """
    return _as_utc(now or datetime.now(timezone.utc)) + timedelta(
        minutes=settings.auth_access_token_minutes
    )


def refresh_token_expiry(now: datetime | None = None) -> datetime:
    """计算新的 Refresh Token 与会话到期时间。

    Args:
        now: 计算基准时间；省略时使用当前 UTC 时间。

    Returns:
        按配置天数计算、带 UTC 时区的到期时间。
    """
    return _as_utc(now or datetime.now(timezone.utc)) + timedelta(
        days=settings.auth_refresh_token_days
    )


def decode_token(*, token: str, expected_type: TokenType) -> TokenPayload:
    """验证签名、到期与用途，并解析认证服务需要的最小声明。

    Args:
        token: 客户端携带的 JWT 字符串。
        expected_type: 当前入口允许的 Token 用途；不匹配时拒绝令牌。

    Returns:
        已验证用户、认证会话、用途和到期时间的最小声明对象。

    Raises:
        AppError: 密钥未配置、签名/格式无效、已过期或声明不完整时抛出。
    """
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
    """将 SQLite 可能返回的无时区时间按 UTC 解释，统一用于安全比较。

    Args:
        value: 需要规范化的日期时间值。

    Returns:
        带 UTC 时区的日期时间值。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
