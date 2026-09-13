"""显式准备项目 Chroma tenant/database，不删除默认命名空间或已有 Collection。"""

from __future__ import annotations

from pathlib import Path
import sys

# 允许按 README 直接执行脚本；此时 Python 默认只把 scripts/ 加入模块搜索路径。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.core.errors import AppError
from app.services.infrastructure.chroma_namespace import ensure_chroma_namespace


def main() -> int:
    """创建或确认当前配置对应的 Chroma 项目命名空间。"""
    try:
        result = ensure_chroma_namespace()
    except AppError as exc:
        print(f"CHROMA_NAMESPACE_PROVISION_FAILED code={exc.code}")
        return 1

    print(
        "CHROMA_NAMESPACE_READY "
        f"tenant={settings.chroma_tenant} "
        f"database={settings.chroma_database} "
        f"tenant_created={result.tenant_created} "
        f"database_created={result.database_created}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
