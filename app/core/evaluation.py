"""提供可选 Pixie 评测观测，生产环境未安装时保持无操作。"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from typing import Any, Literal, TypeVar


T = TypeVar("T")
_EVAL_NAME_COUNTS: ContextVar[dict[str, int] | None] = ContextVar(
    "archive_eval_name_counts",
    default=None,
)


@contextmanager
def evaluation_name_scope() -> Iterator[None]:
    """为一次多工具或多轮评测提供稳定且不冲突的 Wrap 名称。"""
    token = _EVAL_NAME_COUNTS.set({})
    try:
        yield
    finally:
        _EVAL_NAME_COUNTS.reset(token)


def _scoped_name(name: str) -> str:
    """只在显式评测作用域内给重复名称追加稳定序号。

    Args:
        name: 评测观测点的基础名称。

    Returns:
        当前作用域中可区分重复观测点的名称。
    """
    counts = _EVAL_NAME_COUNTS.get()
    if counts is None:
        return name
    count = counts.get(name, 0) + 1
    counts[name] = count
    return name if count == 1 else f"{name}__{count}"


def eval_wrap(
    data: T,
    *,
    purpose: Literal["input", "output", "state"],
    name: str,
    description: str,
) -> T:
    """调用 Pixie wrap；未安装评测依赖时原样返回数据。

    Args:
        data: 需要注入或观测的值、函数。
        purpose: 输入依赖、最终输出或内部状态。
        name: 全项目唯一的评测数据点名称。
        description: 数据点的业务含义。

    Returns:
        Pixie 处理后的同类型对象；普通运行时返回原对象。
    """
    try:
        import pixie
    except ImportError:
        return data
    return pixie.wrap(
        data,
        purpose=purpose,
        name=_scoped_name(name),
        description=description,
    )
