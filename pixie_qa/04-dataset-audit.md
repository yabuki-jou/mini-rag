# FR-042 项目档案助手数据集审计

## 结论

`pixie_qa/datasets/archive-agent-mvp.json` 已通过 Step 4 的结构、现实性、覆盖和脱敏硬门，可进入真实模型评测。Pixie 已成功解析 17 条条目、1 个真实应用 Runnable 和 6 个最终评估器；本阶段尚未运行整批真实模型评测。

## 数据来源

| 来源标记 | 条目数 | 用途 |
| --- | ---: | --- |
| `AV1_P02_CAPTURED_20260908` | 13 | 复用既有正式检索评测捕获的 12 个单轮问题及其中两组候选组成的 1 个多轮问题 |
| `FR042_REFERENCE_TRACE` | 3 | 复用已完成真实 DeepSeek 参考 Trace 的虚构 PX-BETA 目录和多轮证据世界 |
| `FR042_CONTROLLED_FAILURE` | 1 | 验证外部输入形状异常时的稳定错误、失败审计和历史副作用 |

最终数据集没有使用 `tests/fixtures`、Mock Server 或开发示例作为 `eval_input`。从正式捕获迁移的候选已移除 `document_id`、`chunk_id`、检索分数和重排分数，并在单次请求范围内重新生成 `D1...D8` 引用。

## 覆盖与难度

| 类别 | 条目数 |
| --- | ---: |
| `GROUNDED` | 8 |
| `NO_EVIDENCE` | 2 |
| `ISOLATION` | 2 |
| `CATALOG` | 2 |
| `MULTI_TURN` | 2 |
| `CONTROLLED_FAILURE` | 1 |

难度分布为 `routine=5`、`moderate=3`、`challenging=9`；常规条目占 `5/17`，低于 60% 上限。覆盖 `catalog_navigation`、`evidence_qa`、`multi_turn`、`citations`、`safe_refusal`、`scope_audit` 六项能力。

17 条中有 16 组不同的外部世界输入，超过“半数条目必须具有不同世界数据”的门槛。每条条目均同时提供目录和证据两个输入 Wrap；单轮场景为允许的重复调用准备稳定后缀，多轮场景最多准备四次调用。

## 已执行的机械验证

- 数据集生成脚本实际生成 17 条条目。
- 数据集现实性与覆盖测试通过：`1 passed`。
- 评测材料和评估器相关测试通过：`13 passed`。
- Pixie `load_dataset` 成功解析 17 条条目，并解析出 6 个最终评估器。
- 最终 JSON 不含 `document_id`、`chunk_id`、`reranker_score`、名为 `score` 的字段、Access Token 或 Refresh Token。

## 证据边界

- 既有正式捕获证明问题和候选规模来自真实检索评测流程，但本数据集中的候选已经脱敏和重投影。
- PX-BETA 是虚构参考世界，用于验证 Agent 路由、会话、引用和审计，不证明真实 PostgreSQL 或 Chroma 的检索质量。
- 从候选文件名构造的目录页只用于检测模型是否错误选择目录工具，不能作为目录业务数据真实性证据。
- 只有 Step 5 的完整 `pixie test` 才能产生 17 条场景的真实模型分数；当前结论只确认数据集可执行且满足输入质量门槛。
