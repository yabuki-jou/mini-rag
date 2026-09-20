"""以项目配置运行 Alembic 数据库迁移。"""

from alembic import command
from alembic.config import Config

from app.core.config import PROJECT_ROOT
from app.db import database_url


def build_alembic_config(target_url: str | None = None) -> Config:
    """构造可供应用启动和隔离测试复用的 Alembic 配置。

    Args:
        target_url: 可选目标数据库 URL；省略时使用应用当前配置。

    Returns:
        已加载项目 ``alembic.ini`` 并写入目标数据库 URL 的配置对象。
    """
    config_path = PROJECT_ROOT / "alembic.ini"
    config = Config(str(config_path))
    config.attributes["database_url"] = target_url or database_url
    return config


def upgrade_database(target_url: str | None = None) -> None:
    """将指定数据库升级到最新迁移版本。

    Args:
        target_url: 可选目标数据库 URL；省略时使用应用当前配置。

    Raises:
        Exception: Alembic 连接、迁移脚本或数据库执行失败时向启动方传播。
    """
    # 启动和隔离测试共用同一入口，确保数据库结构始终按迁移版本推进。
    command.upgrade(build_alembic_config(target_url), "head")
