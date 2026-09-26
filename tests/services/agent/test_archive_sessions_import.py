"""验证档案助手会话服务可以在独立进程中冷导入。"""

from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_archive_sessions_can_be_imported_in_a_cold_process() -> None:
    """冷导入服务时不得因 dependencies 包初始化产生循环导入。"""
    script = """
import socket

def deny_connection(*args, **kwargs):
    raise RuntimeError("导入探针禁止建立网络连接")

socket.socket.connect = deny_connection
socket.socket.connect_ex = deny_connection

import app.services.agent.archive_sessions
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
