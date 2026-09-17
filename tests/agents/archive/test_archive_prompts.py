"""验证 FR-042 项目档案助手工具选择提示契约。"""


def test_archive_agent_prompt_separates_catalog_and_evidence_intents() -> None:
    """事实问答不得因二次调用切换成目录响应。"""
    from app.agents.archive.prompts import ARCHIVE_AGENT_SYSTEM_PROMPT

    normalized = "".join(ARCHIVE_AGENT_SYSTEM_PROMPT.splitlines())

    assert "列出、筛选、分页或查看登记字段" in normalized
    assert "日期、单位、版本、结论或其他原文事实" in normalized
    assert "成功取得原文证据后不得改为调用目录工具" in normalized
    assert "目录结果不能作为原文事实答案" in normalized
