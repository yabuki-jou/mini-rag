"""配置应用日志，并为每个 HTTP 请求注入稳定的请求 ID。"""

import logging
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from re import fullmatch
from time import perf_counter
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_PATTERN = r"[A-Za-z0-9._-]{1,128}"

logger = logging.getLogger(__name__)


def build_dated_log_path(
    log_file_path: Path,
    timestamp: float | datetime,
) -> Path:
    """根据本地时间为日志基准路径构造日期分层路径。

    Args:
        log_file_path: 配置中的日志基准路径，例如 ``logs/app.log``。
        timestamp: ``LogRecord.created`` 时间戳，或用于测试的日期时间。

    Returns:
        包含四位年、两位月和 ISO 日期文件名的日志路径。
    """
    if isinstance(timestamp, datetime):
        local_time = (
            timestamp.astimezone() if timestamp.tzinfo is not None else timestamp
        )
    else:
        local_time = datetime.fromtimestamp(timestamp)

    suffix = log_file_path.suffix or ".log"
    return (
        log_file_path.parent
        / local_time.strftime("%Y")
        / local_time.strftime("%m")
        / f"{local_time:%Y-%m-%d}{suffix}"
    )


class DatedRotatingFileHandler(RotatingFileHandler):
    """按日志记录本地日期切换文件，并在当天目录内按大小轮转。"""

    def __init__(
        self,
        filename: Path,
        maxBytes: int,
        backupCount: int,
    ) -> None:
        """创建延迟打开的日期分层轮转处理器。

        Args:
            filename: 日志基准路径；其父目录和扩展名用于构造日期路径。
            maxBytes: 单个当天日志文件触发轮转的最大字节数。
            backupCount: 当天目录中保留的轮转备份数量。
        """
        self._base_log_file_path = Path(filename)
        super().__init__(
            filename=filename,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding="utf-8",
            delay=True,
        )

    def emit(self, record: logging.LogRecord) -> None:
        """根据记录时间切换目标文件，再执行标准按大小轮转。

        Args:
            record: Python 日志系统正在写出的记录。
        """
        dated_path = build_dated_log_path(
            self._base_log_file_path,
            record.created,
        ).absolute()
        current_path = Path(self.baseFilename)
        if current_path != dated_path:
            # 日期变化时关闭旧文件；新文件在真正写入时才由父类打开。
            if self.stream is not None:
                self.stream.flush()
                self.stream.close()
                self.stream = None
            dated_path.parent.mkdir(parents=True, exist_ok=True)
            self.baseFilename = str(dated_path)

        super().emit(record)


def get_request_id() -> str:
    """返回当前请求 ID；非 HTTP 调用返回 ``-``。"""
    return _REQUEST_ID.get()


class RequestIdFilter(logging.Filter):
    """把当前请求 ID 添加到每一条应用日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """向日志记录写入格式化器需要的 ``request_id`` 字段。

        Args:
            record: Python 日志系统正在处理的记录。

        Returns:
            始终返回 ``True``，表示保留该日志记录。
        """
        record.request_id = get_request_id()
        return True


def configure_logging(
    log_file_path: Path,
    max_bytes: int,
    backup_count: int,
) -> None:
    """安装包含请求 ID 的控制台和轮转文件日志处理器。

    Args:
        log_file_path: 应用日志基准路径，其父目录和后缀用于生成日期路径。
        max_bytes: 单个日期日志文件触发轮转的最大字节数。
        backup_count: 每个日期日志文件保留的大小轮转备份数量。
    """
    root_logger = logging.getLogger()

    # 开发环境默认记录 INFO；重复导入应用时不重复添加处理器。
    if any(
        getattr(handler, "_mini_rag_handler", False)
        for handler in root_logger.handlers
    ):
        return

    # 两个处理器共用格式和过滤器，保证控制台与文件内容结构一致。
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s "
        "request_id=%(request_id)s %(message)s"
    )
    request_id_filter = RequestIdFilter()

    # 控制台日志便于开发时立即观察请求和异常。
    console_handler = logging.StreamHandler()
    console_handler._mini_rag_handler = True  # type: ignore[attr-defined]
    console_handler.addFilter(request_id_filter)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 目录和当天文件在第一条日志写出时按记录日期延迟创建。
    file_handler = DatedRotatingFileHandler(
        filename=log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    file_handler._mini_rag_handler = True  # type: ignore[attr-defined]
    file_handler.addFilter(request_id_filter)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    root_logger.setLevel(logging.INFO)
    logger.info(
        "logging_configured file=%s max_bytes=%s backup_count=%s",
        log_file_path,
        max_bytes,
        backup_count,
    )


def _resolve_request_id(request: Request) -> str:
    """读取安全的客户端请求 ID，缺失或无效时生成新 ID。

    Args:
        request: 当前 FastAPI HTTP 请求。

    Returns:
        可安全写入响应头和日志的请求 ID。
    """
    candidate = request.headers.get("X-Request-ID", "").strip()
    if fullmatch(_REQUEST_ID_PATTERN, candidate):
        return candidate
    return str(uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求记录请求 ID、状态码和总耗时。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """建立请求日志上下文，并调用下一层 ASGI 应用。

        Args:
            request: 当前 HTTP 请求。
            call_next: 执行后续中间件、路由和异常处理器的函数。

        Returns:
            带 ``X-Request-ID`` 响应头的 HTTP 响应。

        Raises:
            Exception: 记录未处理异常后继续交给外层处理。
        """
        request_id = _resolve_request_id(request)
        request.state.request_id = request_id
        context_token = _REQUEST_ID.set(request_id)
        started_at = perf_counter()

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "http_request method=%s path=%s status=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                (perf_counter() - started_at) * 1000,
            )
            return response
        except Exception:
            logger.exception(
                "http_request_failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                (perf_counter() - started_at) * 1000,
            )
            raise
        finally:
            # ContextVar 必须恢复，防止工作线程复用时串用上一个请求 ID。
            _REQUEST_ID.reset(context_token)
