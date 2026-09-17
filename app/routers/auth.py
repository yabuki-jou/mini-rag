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
    """公开注册一个账号密码用户，不返回密码或密码哈希。"""
    return register_user(payload=payload, session=session)


@router.get("/me", response_model=UserRead)
def me_endpoint(current_user: CurrentUserDep) -> User:
    """返回当前 Access Token 对应的用户信息，不暴露密码字段。"""
    return current_user

@router.post("/login", response_model=AuthTokenPairRead)
def login_endpoint(payload: AuthLoginRequest, session: SessionDep) -> AuthTokenPairRead:
    """验证账号密码并创建可撤销认证会话。"""
    return login(payload=payload, session=session)


@router.post("/refresh", response_model=AuthAccessTokenRead)
def refresh_endpoint(payload: AuthRefreshRequest, session: SessionDep) -> AuthAccessTokenRead:
    """使用 Refresh Token 签发新的 Access Token，不轮换 Refresh Token。"""
    return refresh_access_token(payload=payload, session=session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_endpoint(
    current_authentication: CurrentAuthenticationDep,
    session: SessionDep,
) -> Response:
    """撤销当前 Access Token 对应的认证会话。"""
    logout(principal=current_authentication, session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
