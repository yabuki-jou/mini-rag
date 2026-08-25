"""定义应用业务异常，并统一 FastAPI 的错误响应。"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


class AppError(Exception):
    """可安全返回给客户端的预期业务错误。

    Attributes:
        status_code: HTTP 响应状态码。
        code: 供客户端判断错误类型的稳定代码。
        message: 可展示给客户端的错误信息。
        details: 可选的、已脱敏的业务错误补充信息。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        """初始化业务错误。

        Args:
            status_code: HTTP 响应状态码。
            code: 供客户端判断错误类型的稳定代码。
            message: 可展示给客户端的错误信息。
            details: 可选的、已脱敏的业务错误补充信息。
        """
        # 同时保存 HTTP 语义和稳定业务代码，供统一异常处理器构造响应。
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """为 FastAPI 应用注册统一的异常处理器。

    Args:
        app: 需要安装异常处理器的 FastAPI 应用。
    """

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        """将预期业务异常转换为稳定且可安全展示的响应。"""
        # 业务异常的状态码、错误代码和消息都由抛出位置明确指定。
        logger.warning(
            "application_error status=%s code=%s",
            exc.status_code,
            exc.code,
        )
        error_payload: dict[str, Any] = {
            "code": exc.code,
            "message": exc.message,
        }
        if exc.details is not None:
            error_payload["details"] = jsonable_encoder(exc.details)
        return JSONResponse(status_code=exc.status_code, content={"error": error_payload})

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """将 FastAPI 参数校验异常转换为统一的 422 响应。"""
        # jsonable_encoder 将 Pydantic 错误中的特殊对象转换为 JSON 数据。
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数校验失败。",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def exception(_: Request, exc: Exception) -> JSONResponse:
        """记录未知异常的完整堆栈，并向客户端隐藏内部细节。"""
        # 完整细节只进入服务端日志，响应中不暴露数据库或密钥等信息。
        logger.exception("unhandled_application_error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器内部错误，请查看日志。",
                }
            },
        )

