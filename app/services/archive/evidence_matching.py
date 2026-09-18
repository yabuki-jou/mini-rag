"""提供正式检索诊断与固定集验收共用的证据覆盖匹配规则。"""

import re
from collections.abc import Mapping


def _normalized_text(value: object) -> str:
    """以稳定的空白与大小写规则规范化证据文本。

    Args:
        value: 候选摘录或标准证据摘录。
    """
    return re.sub(r"\s+", "", str(value)).casefold()


def item_contains_expected_evidence(
    item: Mapping[str, object], expected_evidence: object
) -> bool:
    """判断候选是否覆盖标准证据的文件、定位范围和全部摘录。

    Args:
        item: 已通过正式检索范围校验的候选字段。
        expected_evidence: 固定评测集中的标准证据标注。

    返回:
        候选按公开验收语义完整覆盖标准证据时返回 True。
    """
    if not isinstance(expected_evidence, dict):
        return False
    relative_path = expected_evidence.get("relative_path")
    expected_filename = str(relative_path).replace("\\", "/").rsplit("/", 1)[-1]
    if not expected_filename or str(item.get("filename")) != expected_filename:
        return False

    expected_items = expected_evidence.get("items")
    if not isinstance(expected_items, list) or not expected_items:
        return False
    for expected in expected_items:
        if not isinstance(expected, dict):
            return False
        try:
            location_type = item["location_type"]
            normalized_location_type = getattr(location_type, "value", location_type)
            same_location_type = str(normalized_location_type) == str(
                expected["location_type"]
            )
            contains_location = (
                int(item["location_start"])
                <= int(expected["location_start"])
                <= int(expected["location_end"])
                <= int(item["location_end"])
            )
            contains_excerpt = _normalized_text(expected["excerpt"]) in _normalized_text(
                item["excerpt"]
            )
        except (KeyError, TypeError, ValueError):
            return False
        if not (same_location_type and contains_location and contains_excerpt):
            return False
    return True
