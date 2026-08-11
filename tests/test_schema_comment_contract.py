"""验证业务表字段说明覆盖 SQLModel 当前声明的每一列。"""

import importlib.util
from pathlib import Path

from sqlmodel import SQLModel

from app import models as _models  # noqa: F401  注册所有业务表。


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration_constants(filename: str):
    """按文件加载迁移中的固定注释映射，避免把迁移文件变成应用运行时依赖。"""
    path = PROJECT_ROOT / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TABLE_COMMENTS, module.COLUMN_COMMENTS


def test_every_business_table_and_column_has_a_comment_specification() -> None:
    """业务模型新增列时，必须同步补 PostgreSQL 注释迁移。"""
    archive_tables, archive_columns = _load_migration_constants(
        "0006_archive_jsonb_and_comments.py"
    )
    legacy_tables, legacy_columns = _load_migration_constants(
        "0008_legacy_business_comments.py"
    )
    auth_tables, auth_columns = _load_migration_constants(
        "0010_account_password_authentication.py"
    )
    table_comments = archive_tables | legacy_tables | auth_tables
    column_comments = archive_columns | legacy_columns
    for table_name, comments in auth_columns.items():
        column_comments[table_name] = column_comments.get(table_name, {}) | comments

    business_tables = {
        table_name: table
        for table_name, table in SQLModel.metadata.tables.items()
        if table_name != "alembic_version"
    }
    assert set(table_comments) == set(business_tables)
    assert set(column_comments) == set(business_tables)
    for table_name, table in business_tables.items():
        assert set(column_comments[table_name]) == {
            column.name for column in table.columns
        }
