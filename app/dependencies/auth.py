"""从 Bearer Access Token 建立当前用户与认证会话。"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError
from app.dependencies.database import SessionDep
from app.models import User
from app.services.auth_service import AuthenticatedPrincipal, authenticate_access_token


# auto_error=False 使缺失、格式错误与过期 Token 都走统一 AppError 契约。
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_authentication(
    session: SessionDep,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedPrincipal:
    """验证 Bearer Access Token，并读取未撤销的当前认证会话。

    客户端传入的 ``X-User-ID`` 不参与本函数；所有资源授权仅可信任 Token 中
    已验证的用户和会话声明。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "AUTHENTICATION_REQUIRED", "请提供 Bearer Access Token。")
    return authenticate_access_token(token=credentials.credentials, session=session)


CurrentAuthenticationDep = Annotated[
    AuthenticatedPrincipal,
    Depends(get_current_authentication),
]


def get_current_user(current_authentication: CurrentAuthenticationDep) -> User:
    """为既有资源所有权依赖提供经过 JWT 验证的用户实体。"""
    return current_authentication.user


# 旧业务路由继续声明 CurrentUserDep，但身份来源已完全切换到 Bearer Token。
CurrentUserDep = Annotated[User, Depends(get_current_user)]
