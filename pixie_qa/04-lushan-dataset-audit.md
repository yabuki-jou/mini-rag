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

## 隔离持久层验收结果

2026-09-23 在随机 PostgreSQL Schema、临时 Chroma Collection、临时文件目录和独立 SQLite Checkpoint 中完成一次端到端验收。4 份公开 PDF 均依次完成上传、解析、人工字段确认和正式索引；PostgreSQL、Chroma、文件系统和 Checkpoint 均观察到真实写入。业务清理完成后四类存储计数均归零，随后又删除了临时 Collection、Schema 和运行目录，未写入 `public` Schema 或正式 Collection。

本轮质量门槛未通过，不能用前述注入式 Pixie 结果替代真实检索结论：

- Top-8 有据检索覆盖 2/3；`LUSHAN-01` 的目标文档进入 Top-8，但期望证据片段未进入结果。
- FR-039 有据问题返回 `ANSWERED` 且答案包含目标事实，但没有命中要求的来源文件引用；无据问题正确返回 `REFUSED_NO_EVIDENCE` 且引用为空。
- FR-042 三轮中，第一轮返回 `ANSWERED` 但缺少目标事实和引用；第二轮错误返回 `REFUSED_NO_EVIDENCE`；第三轮无据拒答通过。历史恢复得到 6 条消息，记录到 2 次脱敏工具调用。

脱敏机器可读汇总保存在本地测试资料目录的 `acceptance/persistence-acceptance-20260923.json`。该目录按仓库规则被忽略，不作为 Git 产物；汇总只包含状态、计数和布尔判定，不包含文档正文、回答正文、凭据或隐藏推理。

## LUSHAN-01 召回诊断

同日使用既有开发诊断端点复现完整 Top-30 双排序。`LUSHAN-01` 的标准证据没有进入 Chroma Top-30，因此失败发生在 dense 召回阶段，不是 Reranker 把标准证据排出 Top-8；同一目标文档只有 1 个其他 Chunk 进入 Top-30，其 dense 与 Reranker 排名均为第 2，且未发现范围隔离违规。

只读解析检查确认 2016 PDF 生成了 66 个页级 Chunk：第 16 页存在 1 个 2370 字符 Chunk，完整包含 300 字符的标准证据摘录，因而排除解析遗漏。使用相同 4 份 PDF、当前页级解析和当前 Embedding 配置重建 190 个内存向量后，原问题的目标页 dense 排名为第 120；删除输出格式要求后为第 110，缩短为“世界银行贷款金额”后为第 109，英文 `World Bank` 问法为第 130。显式改用原文术语 `IBRD` 后，中文短问升至第 1，英文问法升至第 11。

当前证据支持的主因是：在该固定问题和 Embedding 配置下，查询里的“世界银行”与英文原文 `IBRD` 术语没有稳定对齐。统一 `800/300` 窗口切分已完成对照且使现有三个有据问题都退化，因此该参数组合不能作为修复；这仍不能证明所有细粒度切分均无效。任何查询规范化、Chunk 粒度或 Embedding 变更都属于正式检索行为变化，须遵守 P14 决策门并另行确认。脱敏初始诊断汇总保存在本地 `acceptance/dense-diagnosis-20260923.json`。

后续只读对照否定了两个直接方案：把别名追加到完整问题只能把目标页提升到第 46；`800` 字符窗口、`300` 字符重叠会把 190 个页级 Chunk 增至 857 个，并使三条有据题全部退化。只在检索表达中把“世界银行”规范为 `IBRD IDA` 时，`LUSHAN-01` 的目标页 dense 排名为第 6、Reranker 排名为第 3，进入 Top-8。该结果只支持继续评审“贷款/融资语境下的检索专用术语规范化”，不授权正式实现；机器可读汇总保存在本地 `acceptance/retrieval-strategy-comparison-20260923.json`。
