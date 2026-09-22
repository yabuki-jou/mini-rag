# 芦山 P153548 Smoke 数据集审计

## 数据来源

`lushan-p153548-smoke.json` 使用世界银行公开项目 P153548（Lushan Earthquake Reconstruction and Risk Reduction Project）的真实 PDF。原文件及来源清单保存在本地 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/`，但这些材料不是项目自造的测试 fixture；其原始来源、文件哈希、页数和下载地址以该目录的 `metadata/manifest.json` 为准。

数据集统一使用来源标记 `WORLD_BANK_P153548_PUBLIC`。注入的 evidence excerpt 均逐字取自 manifest 对应 PDF 页，折叠换行和排版空白后仍能在页面提取文本中匹配。贷款协议因部分页面是扫描内容，不进入本轮问答案例。

## 覆盖范围

数据集包含 5 个案例，难度分布为 2 个 `routine` 和 3 个 `challenging`：

- 两个普通有据问答：贷款金额、延期后的关闭日期。
- 一个带相近干扰证据的独立复核评级问答。
- 一个资料无据的手机号拒答案例。
- 一个原完工报告与独立复核评级对照的两轮问答。

能力覆盖包括 evidence、citations、refusal、multiturn 和 distractor discrimination。每条案例均预置本轮可能发生的目录与证据 wrap 输入；目录结果使用空的合法页，避免错误路由时把不完整目录对象混入证据问答。

## 验证边界

本轮通过注入真实 PDF 页文本，测试 FR-042 Agent 的工具路由、证据忠实性、引用、无据拒答和多轮上下文行为。它不经过 PostgreSQL 或 Chroma，因此不验证数据库范围控制、正式索引或实际向量召回质量；也不代表 OCR 能力通过。部分扫描的贷款协议明确排除在问答范围外。

## 真实模型评测结果

已使用真实 DeepSeek 完成 Pixie 评测。`test_id` 为 `20260922-093130`，执行命令为 `pixie test pixie_qa\datasets\lushan-p153548-smoke.json -v --no-open`；数据集包含 5 个条目、合计 6 轮，共 21 个 evaluations，`pending=0` 且 21/21 个评分均为 `1.0`。Step 6 已完成，数据集级 `analysis`、`analysis-summary` 与顶层 `action-plan`、`action-plan-summary` 均已生成。

本次评测只验证真实公开材料经 Pixie 注入后的回答层行为，生产 `app/` 无需修改。评测结果目录位于被忽略的 `pixie_qa/results/20260922-093130`，不作为本次 Git 变更产物。
