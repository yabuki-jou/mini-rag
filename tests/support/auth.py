"""为既有受保护路由测试签发真实 Bearer Access Token 的辅助函数。"""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.core.config import settings
from app.core.security import (
    access_token_expiry,
    create_token,
    hash_refresh_token,
    refresh_token_expiry,
)
from app.models import AuthSession


TEST_JWT_SECRET = "shared-router-test-secret-that-is-long-enough"


def auth_headers(engine: Engine, user_id: UUID) -> dict[str, str]:
    """为已存在测试用户创建未撤销会话并返回真实 Access Bearer 请求头。

    旧业务路由测试无需重复走注册登录流程，但仍通过生产认证依赖验证 JWT、会话和用户，
    不再模拟 ``X-User-ID`` 后门。
    """
    settings.auth_jwt_secret = SecretStr(TEST_JWT_SECRET)
    now = datetime.now(timezone.utc)
    refresh_expires_at = refresh_token_expiry(now)
    auth_session = AuthSession(
        user_id=user_id,
        refresh_token_hash="",
        expires_at=refresh_expires_at,
    )
    session_id = auth_session.id
    refresh_token = create_token(
        user_id=user_id,
        session_id=session_id,
        token_type="refresh",
        expires_at=refresh_expires_at,
    )
    auth_session.refresh_token_hash = hash_refresh_token(refresh_token)
    with Session(engine) as session:
        session.add(auth_session)
        session.commit()

    access_token = create_token(
        user_id=user_id,
        session_id=session_id,
        token_type="access",
        expires_at=access_token_expiry(now),
    )
    return {"Authorization": f"Bearer {access_token}"}
