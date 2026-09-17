"""定义账号认证、用户和知识库接口的数据结构。"""

from datetime import datetime
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,49}$")


class AuthRegisterRequest(BaseModel):
    """注册账号密码用户时允许客户端提交的字段。

    Attributes:
        username: 全局唯一的登录标识，规范化为小写。
        name: 用户显示名称。
        password: 仅用于本次注册的明文密码，不会出现在响应或日志中。
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> str:
        """去除首尾空格并将用户名统一为小写，保证唯一索引语义稳定。"""
        if not isinstance(value, str):
            return value  # type: ignore[return-value]
        normalized = value.strip().lower()
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise ValueError("username 必须为 3～50 位小写字母、数字、下划线或连字符。")
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> str:
        """显示名称不允许只包含首尾空格。"""
        if not isinstance(value, str):
            return value  # type: ignore[return-value]
        return value.strip()


class AuthLoginRequest(BaseModel):
    """登录时客户端提交的账号密码。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> str:
        """与注册使用相同的大小写规范，避免相同账号产生不同查询。"""
        if not isinstance(value, str):
            return value  # type: ignore[return-value]
        return value.strip().lower()


class AuthRefreshRequest(BaseModel):
    """刷新 Access Token 时提交的 Refresh Token。"""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class AuthAccessTokenRead(BaseModel):
    """刷新成功时返回的短期 Access Token，不回显 Refresh Token。"""

    access_token: str
    token_type: str
    access_expires_in: int


class AuthTokenPairRead(AuthAccessTokenRead):
    """首次登录成功时返回的 Access/Refresh Token 对。"""

    refresh_token: str
    refresh_expires_in: int


class UserRead(BaseModel):
    """返回给客户端的用户信息。

    Attributes:
        id: 用户的全局唯一标识。
        username: 登录标识；历史未初始化凭据的用户可能为空。
        name: 用户显示名称。
        created_at: 用户记录的创建时间。
        updated_at: 用户记录最后一次更新时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str | None
    name: str
    created_at: datetime
    updated_at: datetime

