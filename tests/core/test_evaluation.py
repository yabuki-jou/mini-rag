"""验证 Pixie 评测名称作用域和普通运行兼容性。"""

import sys
from types import SimpleNamespace


def test_evaluation_name_scope_suffixes_repeated_names_and_resets(
    monkeypatch,
) -> None:
    """同一评测作用域内重复名称应稳定编号，离开后恢复原名。"""
    from app.core.evaluation import eval_wrap, evaluation_name_scope

    observed: list[str] = []

    def fake_wrap(data, *, purpose, name, description):
        assert purpose == "state"
        assert description
        observed.append(name)
        return data

    monkeypatch.setitem(sys.modules, "pixie", SimpleNamespace(wrap=fake_wrap))

    eval_wrap({}, purpose="state", name="point", description="普通运行")
    eval_wrap({}, purpose="state", name="point", description="普通运行")
    with evaluation_name_scope():
        eval_wrap({}, purpose="state", name="point", description="评测运行")
        eval_wrap({}, purpose="state", name="other", description="评测运行")
        eval_wrap({}, purpose="state", name="point", description="评测运行")
    with evaluation_name_scope():
        eval_wrap({}, purpose="state", name="point", description="下一条评测")

    assert observed == ["point", "point", "point", "other", "point__2", "point"]
