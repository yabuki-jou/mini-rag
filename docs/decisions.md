# 决策台账

## 维护约定

本台账只记录用户已确认的长期产品、架构、数据、安全、质量或范围决策。首次纳入台账日期不等同于原始决策日期；不得把推测写成确认。原则上只追加，决策改变时将旧项标为“已废弃”，并链接替代决策 ID。

每项使用以下字段：

- ID
- 状态
- 首次纳入台账
- 背景
- 决策
- 影响
- 替代/复查条件
- 依据文件

可复制模板（状态仅使用“已确认”或“已废弃”；废弃项必须指向替代决策）：

```markdown
## DEC-NNN：决策标题

- 状态：已确认
- 首次纳入台账：YYYY-MM-DD
- 背景：
- 决策：
- 影响：
- 替代/复查条件：
- 依据文件：
```

## DEC-001：智慧档案与企业文档智能 V1 范围

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：需要明确个人学习项目的产品边界。
- 决策：V1 聚焦智慧档案与企业文档智能及只读企业制度 Agent；不做标书投标、OCR、表格专用解析、业务运行时多 Agent 编排、BM25/混合检索、Redis 任务队列或生产级分布式部署。
- 影响：本地开发和相邻 Vue 工作台联调服务于可演示的后端闭环；云部署另行评估。
- 替代/复查条件：用户重新确认产品边界。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/技术架构.md`](design/技术架构.md)

## DEC-002：Project 与 KnowledgeBase 的隔离职责

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：需要避免跨项目检索和客户端伪造身份范围。
- 决策：Project 是业务隔离边界，KnowledgeBase 是内部检索范围；身份、资源归属和检索范围由服务端控制并注入。
- 影响：受保护接口必须验证用户和资源归属，Chroma 查询使用服务端确定的范围。
- 替代/复查条件：架构或授权模型经用户重新确认。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/技术架构.md`](design/技术架构.md)、[`app/services/archive_retrieval_service.py`](../app/services/archive_retrieval_service.py)

## DEC-003：AI 草稿与人工确认门

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：档案字段和证据需要人工负责后才能用于正式检索。
- 决策：AI 只能生成草稿；人工确认后状态为 `CONFIRMED`，且仅确认的 Final Chunk 进入正式检索。
- 影响：问答和检索不得绕过确认状态或使用草稿数据。
- 替代/复查条件：确认流程或正式索引边界重新设计并获用户确认。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`app/services/archive_final_chunk_service.py`](../app/services/archive_final_chunk_service.py)

## DEC-004：P14 质量门与 Ground Truth 边界

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：需要用固定标准判断档案检索和问答是否可验收。
- 决策：质量门为有据正确回答并正确引用 `>=7/8`、无据拒答 `2/2`、项目隔离 `2/2`；Ground Truth 不得进入运行时；失败时不降低门槛或修改评测集掩盖失败。
- 影响：质量未达门槛时不得宣称 P12 或相关链路通过。
- 替代/复查条件：用户重新确认质量目标或固定评测资料。
- 依据文件：[`docs/review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md`](review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-005：当前不采用统一 Reranker 阈值路径

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：当前 P14 D4/D5 验证未形成可冻结的统一阈值；large D4 已决定跳过。
- 决策：当前不冻结统一 Reranker 阈值，large D4 不执行；这只是当前方案决策，不是对所有模型的普遍结论。
- 影响：任何阈值、模型或评测变量变化须按 handoff 重新取得授权并验证。
- 替代/复查条件：用户针对具体模型、数据和方案重新授权复查。
- 依据文件：[`docs/review/P14-rag检索质量改进/P14-D4-无据分离阈值可行性诊断方案.md`](review/P14-rag检索质量改进/P14-D4-无据分离阈值可行性诊断方案.md)、[`docs/review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md`](review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md)

## DEC-006：DOCUMENT_DATE 的语义

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：合同资料的日期字段存在业务语义歧义。
- 决策：合同资料的 `DOCUMENT_DATE` 表示合同签订日期；其他资料表示文档自身日期。不修改字段、API 或固定 Ground Truth。
- 影响：解析、展示、问答和验收按该语义解释日期。
- 替代/复查条件：用户重新确认日期业务语义。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/数据库设计.md`](design/数据库设计.md)、[`docs/design/接口设计.md`](design/接口设计.md)

## DEC-007：Vue 完整 E2E 的顺序

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：前端联调应建立在后端质量路径稳定之后。
- 决策：Vue 工作台完整真实 E2E 放在后端 P12 质量路径之后。
- 影响：后端质量门未通过前，不以 Vue 完整 E2E 作为当前验收终点。
- 替代/复查条件：用户重新调整验收顺序或产品范围。
- 依据文件：[`docs/stage/handoff.md`](stage/handoff.md)、[`docs/design/需求说明.md`](design/需求说明.md)

## DEC-008：档案问答保留 Top-8 候选

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：D6-A 已解决跨文档字段绑定，但 D5 Top-5 会截断固定集中位于第 6 和第 8 的正确证据。
- 决策：档案问答内部候选从 Top-5 扩至 Top-8，沿用可信文档绑定；公开检索仍保持 `top_k≤10`，问答响应只返回模型实际选择的引用。该决策不改变 Prompt 指令、模型、Embedding、Reranker、Top-30、Chunk、查询表达、固定集或质量门。
- 影响：D1 base/768 隔离实验基线的固定 12 题达到有据 `7/8`、无据 `2/2`、隔离 `2/2`；Top-8 是当前固定证据排名支持的最小边界，不作为其他数据集的通用最优值。
- 替代/复查条件：真实业务数据规模、延迟目标或固定质量评估显示 Top-8 不再满足需求，并经用户确认新的候选策略。
- 依据文件：[`docs/review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md`](review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-009：正式采用 bge-base/768 与 evidence_values

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：D6-B 在隔离实验基线达到固定质量门，正式配置仍停留在 bge-small/512，无法复现已验收链路。
- 决策：正式档案 Embedding 使用 `bge-base-zh-v1.5`、768 维和 `evidence_values`；正式 Collection 继续使用 `archive_final_chunks`，切换时按安全重建脚本删除空旧 Collection、建立 cosine Collection，并用 768 维 canary 验证后清理。
- 影响：仓库默认配置、`.env.example` 与 Compose 使用新基线。本次正式数据库没有确认档案，实际重建为 0 文档、0 Chunk；联合健康检查确认实际模型输出 768 维。项目 `.env` 不读取或修改，其中的私有覆盖值不属于已验证范围。
- 替代/复查条件：新固定集、真实业务数据或资源约束显示当前模型不再满足需求，并经用户确认替代模型和重建方案。
- 依据文件：[`docs/review/P14-rag检索质量改进/P14-D1-Embedding模型替换可行性设计.md`](review/P14-rag检索质量改进/P14-D1-Embedding模型替换可行性设计.md)、[`docs/review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md`](review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md)、[`scripts/archive_v1_formal_embedding_rebuild.py`](../scripts/archive_v1_formal_embedding_rebuild.py)

## 非决策/当前待授权事项

D6-A、D6-B 和正式 Embedding/Collection 切换均已授权并完成。是否继续优化 Q-01，以及何时开始完整 Vue E2E，仍按 [`docs/stage/handoff.md`](stage/handoff.md) 分别处理；本台账不自动授权这些后续动作。
