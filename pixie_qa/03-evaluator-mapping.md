# 项目档案助手 Evaluator Mapping

## Built-in evaluators used

本轮不使用内置 evaluator。FR-042 的公开输出是多个具名 Wrap 组成的结构，并且关键质量取决于“本轮最后一次工具结果”、工具顺序和多轮上下文；`Factuality`/`ClosedQA` 只比较通用输入、输出和期望文本，不能可靠判定这些 Trace 语义。开放式回答也不使用 `ExactMatch`。

## Agent evaluators

| Evaluator name | 覆盖标准 | 适用条目 | Source file |
|---|---|---|---|
| `pixie_qa/evaluators.py:archive_agent_tool_path_quality` | #1 工具路由与预算；#5 目录筛选和分页意图 | 全部成功条目，重点目录与两轮 | `pixie_qa/evaluators.py` |
| `pixie_qa/evaluators.py:archive_agent_evidence_faithfulness` | #2 当前轮证据支撑；#3 引用可信；#4 无据拒答 | 有据、无据、隔离和两轮条目 | `pixie_qa/evaluators.py` |
| `pixie_qa/evaluators.py:archive_agent_multiturn_freshness` | #6 第二轮上下文与新证据，不复用旧轮候选 | 两轮条目；单轮按不适用通过 | `pixie_qa/evaluators.py` |

## Manual custom evaluators

| Evaluator name | 覆盖标准 | 适用条目 | Source file |
|---|---|---|---|
| `pixie_qa/evaluators.py:archive_agent_structural_contract` | #1 最多两次且仅允许工具；#3 引用精确映射；#4 固定拒答；#5 目录响应形状；#6 历史与审计计数 | 全部成功条目 | `pixie_qa/evaluators.py` |
| `pixie_qa/evaluators.py:archive_agent_trace_privacy` | #7 Wrap 不含范围 ID、持久化 ID、分数、Token 或 UUID | 全部条目 | `pixie_qa/evaluators.py` |
| `pixie_qa/evaluators.py:archive_agent_failure_contract` | #8 受控 HTTP/业务错误、工具审计和失败后历史副作用 | 受控失败条目 | `pixie_qa/evaluators.py` |

## 八项标准完整映射

| 标准 | Evaluator |
|---|---|
| 1. 工具路径正确且预算不超过两次 | `archive_agent_tool_path_quality` + `archive_agent_structural_contract` |
| 2. 最终事实只由当前轮最后证据支持 | `archive_agent_evidence_faithfulness` |
| 3. 引用对应真实文件、位置和摘录 | `archive_agent_evidence_faithfulness` + `archive_agent_structural_contract` |
| 4. 零候选或证据不足时固定拒答 | `archive_agent_evidence_faithfulness` + `archive_agent_structural_contract` |
| 5. 目录分页、排序、筛选和五字段安全投影 | `archive_agent_tool_path_quality` + `archive_agent_structural_contract` |
| 6. 多轮保持会话且使用第二轮新证据 | `archive_agent_multiturn_freshness` + `archive_agent_structural_contract` |
| 7. 响应与 Wrap 脱敏 | `archive_agent_trace_privacy` |
| 8. 重试、失败、可见消息与审计幂等 | `archive_agent_failure_contract`；生产确定性 TDD 继续作为 100% 门槛 |

## Applicability summary

- 成功条目默认：`archive_agent_structural_contract`、`archive_agent_trace_privacy`。
- 受控失败条目默认：`archive_agent_failure_contract`、`archive_agent_trace_privacy`。
- 目录条目增加：`archive_agent_tool_path_quality`。
- 有据、无据和隔离条目增加：`archive_agent_tool_path_quality`、`archive_agent_evidence_faithfulness`。
- 两轮条目再增加：`archive_agent_multiturn_freshness`。
- Agent evaluator 在 `pixie test` 中保持待人工 Trace 评审，不用正则或通用 LLM 打分替代项目证据判断。
