"""从 Bearer Access Token 建立当前用户与认证会话。"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError
from app.dependencies.database import SessionDep
from app.models import User
from app.services.identity.authentication import AuthenticatedPrincipal, authenticate_access_token


# auto_error=False 使缺失、格式错误与过期 Token 都走统一 AppError 契约。
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_authentication(
    session: SessionDep,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedPrincipal:
    """验证 Bearer Access Token，并读取未撤销的当前认证会话。

    Args:
        session: 当前请求使用的业务数据库会话。
        credentials: FastAPI 解析出的 Authorization 凭据；缺失时为 ``None``。

    Returns:
        由已验证 Token 和数据库会话共同确定的认证主体。

    Raises:
        AppError: 凭据缺失、用途错误、过期、签名无效或认证会话已撤销时抛出。

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
    """为既有资源所有权依赖提供经过 JWT 验证的用户实体。

    Args:
        current_authentication: 已完成 Token、会话和用户存在性校验的认证主体。

    Returns:
        认证主体关联的用户实体。
    """
    return current_authentication.user


# 旧业务路由继续声明 CurrentUserDep，但身份来源已完全切换到 Bearer Token。
CurrentUserDep = Annotated[User, Depends(get_current_user)]
