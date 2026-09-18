"""提供 FR-019～FR-021 的账号密码认证 HTTP 接口。"""

from fastapi import APIRouter, Response, status

from app.dependencies import CurrentAuthenticationDep, CurrentUserDep, SessionDep
from app.models import User
from app.schemas import (
    AuthAccessTokenRead,
    AuthLoginRequest,
    AuthRefreshRequest,
    AuthRegisterRequest,
    AuthTokenPairRead,
    UserRead,
)
from app.services.identity.authentication import login, logout, refresh_access_token, register_user


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register_endpoint(payload: AuthRegisterRequest, session: SessionDep) -> User:
    """公开注册一个账号密码用户，不返回密码或密码哈希。

    Args:
        payload: 经过 Schema 校验的注册信息；密码只交给认证 Service 做哈希处理。
        session: 当前请求的业务数据库会话。

    Returns:
        新建用户的公开字段，对应 HTTP 201 响应。
    """
    return register_user(payload=payload, session=session)


@router.get("/me", response_model=UserRead)
def me_endpoint(current_user: CurrentUserDep) -> User:
    """返回当前 Access Token 对应的用户信息，不暴露密码字段。

    Args:
        current_user: 由已验证的 Access Token 解析出的当前用户。
    """
    return current_user

@router.post("/login", response_model=AuthTokenPairRead)
def login_endpoint(payload: AuthLoginRequest, session: SessionDep) -> AuthTokenPairRead:
    """验证账号密码并创建可撤销认证会话。

    Args:
        payload: 登录账号和密码；认证失败由 Service 映射为统一业务错误。
        session: 用于读取用户并保存认证会话状态的业务数据库会话。

    Returns:
        Access Token 与 Refresh Token 对；Router 不记录或回显密码。
    """
    return login(payload=payload, session=session)


@router.post("/refresh", response_model=AuthAccessTokenRead)
def refresh_endpoint(payload: AuthRefreshRequest, session: SessionDep) -> AuthAccessTokenRead:
    """使用 Refresh Token 签发新的 Access Token，不轮换 Refresh Token。

    Args:
        payload: 只接受 Refresh Token 的请求体，不能用 Access Token 代替。
        session: 用于校验 Refresh Token 会话状态的业务数据库会话。

    Returns:
        新的 Access Token；Refresh Token 本身不在本端点轮换。
    """
    return refresh_access_token(payload=payload, session=session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_endpoint(
    current_authentication: CurrentAuthenticationDep,
    session: SessionDep,
) -> Response:
    """撤销当前 Access Token 对应的认证会话。

    Args:
        current_authentication: 由 Bearer Token 依赖验证出的当前认证主体，不能由客户端
            直接提交用户标识替代。
        session: 用于更新认证会话撤销状态的业务数据库会话。

    Returns:
        空的 HTTP 204 响应，不返回 Token 或用户敏感信息。
    """
    logout(principal=current_authentication, session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
