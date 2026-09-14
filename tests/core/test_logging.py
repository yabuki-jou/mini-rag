"""验证应用日志按本地日期分层和按大小轮转。"""

import logging
from datetime import datetime
from pathlib import Path

from app.core.logging import DatedRotatingFileHandler, build_dated_log_path


def _record(message: str, created: datetime) -> logging.LogRecord:
    """创建指定本地时间的日志记录。"""
    record = logging.LogRecord(
        name="tests.logging",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.created = created.timestamp()
    return record


def test_build_dated_log_path_uses_year_month_and_iso_date() -> None:
    """日志基准路径应映射到四位年、两位月和 ISO 日期文件。"""
    base_path = Path("logs") / "app.log"

    result = build_dated_log_path(base_path, datetime(2026, 9, 14, 8, 30))

    assert result == Path("logs") / "2026" / "09" / "2026-09-14.log"


def test_build_dated_log_path_preserves_custom_extension() -> None:
    """自定义扩展名应继续用于按日期生成的主日志文件。"""
    base_path = Path("var") / "service.jsonl"

    result = build_dated_log_path(base_path, datetime(2026, 1, 2, 0, 1))

    assert result == Path("var") / "2026" / "01" / "2026-01-02.jsonl"


def test_build_dated_log_path_adds_log_extension_when_base_has_none() -> None:
    """没有扩展名的日志配置应默认生成 ``.log`` 文件。"""
    base_path = Path("var") / "service"

    result = build_dated_log_path(base_path, datetime(2026, 12, 31, 23, 59))

    assert result == Path("var") / "2026" / "12" / "2026-12-31.log"


def test_handler_switches_log_file_across_day_month_and_year(
    tmp_path: Path,
) -> None:
    """处理器跨日期边界后应自动将后续记录写入新目录和文件。"""
    handler = DatedRotatingFileHandler(
        tmp_path / "app.log", maxBytes=1024, backupCount=2
    )

    for message, created in (
        ("day", datetime(2026, 1, 31, 23, 59)),
        ("month", datetime(2026, 2, 1, 0, 0)),
        ("year", datetime(2027, 1, 1, 0, 0)),
    ):
        handler.emit(_record(message, created))

    handler.close()

    assert (tmp_path / "2026" / "01" / "2026-01-31.log").read_text(
        encoding="utf-8"
    ) == "day\n"
    assert (tmp_path / "2026" / "02" / "2026-02-01.log").read_text(
        encoding="utf-8"
    ) == "month\n"
    assert (tmp_path / "2027" / "01" / "2027-01-01.log").read_text(
        encoding="utf-8"
    ) == "year\n"


def test_handler_rotates_by_size_inside_the_current_date_directory(
    tmp_path: Path,
) -> None:
    """按大小轮转的备份应留在当天目录，并保留配置的数量。"""
    handler = DatedRotatingFileHandler(
        tmp_path / "app.log", maxBytes=10, backupCount=2
    )

    for index in range(4):
        handler.emit(_record(f"message-{index}", datetime(2026, 9, 14, index)))

    handler.close()

    date_directory = tmp_path / "2026" / "09"
    assert (date_directory / "2026-09-14.log").exists()
    assert (date_directory / "2026-09-14.log.1").exists()
    assert (date_directory / "2026-09-14.log.2").exists()
    assert not (tmp_path / "app.log.1").exists()
