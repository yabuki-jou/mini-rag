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
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/技术架构.md`](design/技术架构.md)、[`app/services/archive/retrieval.py`](../app/services/archive/retrieval.py)

## DEC-003：AI 草稿与人工确认门

- 状态：已确认
- 首次纳入台账：2026-09-08
- 背景：档案字段和证据需要人工负责后才能用于正式检索。
- 决策：AI 只能生成草稿；人工确认后状态为 `CONFIRMED`，且仅确认的 Final Chunk 进入正式检索。
- 影响：问答和检索不得绕过确认状态或使用草稿数据。
- 替代/复查条件：确认流程或正式索引边界重新设计并获用户确认。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`app/services/archive/final_chunks.py`](../app/services/archive/final_chunks.py)

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

## DEC-010：services 按业务域分包且拆分文档生命周期

- 状态：已确认
- 首次纳入台账：2026-09-12
- 决策：将 `app/services/` 按 archive、project、rag、agent、identity、infrastructure 组织；档案文档与旧知识库文档使用独立服务模块；Chroma 客户端与旧知识库 Collection 操作分离。仓库内导入、测试 MonkeyPatch 和当前源码链接同步迁移，不保留旧根级兼容模块。
- 约束：不改变 HTTP API、数据库 Schema、迁移、配置键、Collection 名称、事务边界、权限规则和业务行为；档案服务之间只通过 `archive.reads` 的公开读取函数共享投影。
- 依据文件：[`docs/design/技术架构.md`](design/技术架构.md)、[`docs/codebase/目录结构.md`](codebase/目录结构.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-011：当前评测按业务域组织并保留制度 Agent 质量入口

- 状态：已确认
- 首次纳入台账：2026-09-13
- 背景：`pixie_qa/` 根目录混合已删除请假领域、仍在使用的制度 Agent 和智慧档案 P14 评测，且若直接清理旧目录，活跃的制度 Agent 会失去质量入口。
- 决策：当前评测统一在 `evals/` 下按业务域组织；制度 Agent 使用 `evals/policy_agent/`，智慧档案使用 `evals/archive/`。`pixie_qa/` 只保留本地工具状态与忽略的结果，不再保存评测源码；已删除请假领域的旧运行器、数据集和追踪不保留。
- 约束：注入检索结果的模型评测只证明回答层，不得表述为真实 Chroma 检索通过；真实检索、跨轮状态和故障恢复必须使用独立评测并分别报告。评测不得读取真实业务数据或把 Ground Truth 注入生产请求。
- 替代/复查条件：评测工具的根目录规则发生变化，或新增独立业务域评测时复查。
- 依据文件：[`evals/policy_agent/docs/project-analysis.md`](../evals/policy_agent/docs/project-analysis.md)、[`evals/archive/README.md`](../evals/archive/README.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-012：旧制度 Collection 与当前 768 维 Embedding 对齐

- 状态：已确认
- 首次纳入台账：2026-09-13
- 背景：智慧档案切换到 bge-base/768 后，旧制度 Agent 仍访问冻结为 512 维的 `mini_rag_knowledge_chunks_v1`，真实查询被 Chroma 拒绝并映射为 HTTP 503。
- 决策：仅在制度 Collection 条目数和 PostgreSQL 待重建文档聚合计数均为 0 时，保留原名与 cosine 度量执行空库重建，以 768 维三字段 canary 锁定并验证新维度，随后精确清理 canary。任何非空状态都必须停止，不能自动删除或覆盖。
- 影响：制度 Collection 现与共享的 bge-base/768 配置一致；一次虚构制度文档真实链路已通过。该单题只证明链路恢复，不替代 `evals/policy_agent/` 的回答质量评测，也不证明多轮或故障恢复质量。
- 替代/复查条件：Embedding 再次更换、制度 Collection 出现待迁移数据，或制度与智慧档案改为独立 Embedding 配置时复查。
- 依据文件：[`docs/implementation/制度Agent-Collection维度迁移实施计划.md`](implementation/制度Agent-Collection维度迁移实施计划.md)、[`scripts/policy_collection_embedding_rebuild.py`](../scripts/policy_collection_embedding_rebuild.py)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-013：制度 Agent 应用服务按会话、消息、审计和执行分层

- 状态：已确认
- 首次纳入台账：2026-09-13
- 背景：原 `app/services/agent/sessions.py` 同时承担会话创建、Checkpoint 转换、工具审计和 Graph 执行，任一职责变化都会扩大同一模块的回归范围。
- 决策：`sessions.py` 只负责会话创建和知识库范围校验；`messages.py` 负责 Checkpoint 与用户可见消息转换；`audit.py` 负责工具调用脱敏和审计持久化；`execution.py` 负责 Graph 调用、错误映射、提交边界和响应构造。Router 直接依赖各职责模块，不保留聚合转发入口。
- 约束：执行成功与失败路径的审计、提交和错误转换顺序保持不变；不改变 HTTP API、Prompt、Graph、数据库 Schema、Checkpoint、工具参数或日志脱敏规则。跨模块只导入公开函数。
- 替代/复查条件：Agent 新增写工具、人工中断恢复或不同事务边界，并经用户确认新的应用服务划分时复查。
- 依据文件：[`app/services/agent/`](../app/services/agent/)、[`docs/implementation/智能体逻辑导览.md`](implementation/智能体逻辑导览.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-014：文件日志按本地日期分层

- 状态：已确认
- 首次纳入台账：2026-09-14
- 背景：固定写入单个 `logs/app.log` 不便于按自然日期定位和管理运行日志。
- 决策：`LOG_FILE` 继续作为日志基准路径；应用按每条日志记录的本地时间写入其父目录下的 `YYYY/MM/YYYY-MM-DD<扩展名>`。运行中的服务跨过日、月或年边界时自动切换，不要求重启；无扩展名的基准路径默认使用 `.log`。
- 影响：控制台日志、request ID、格式和按大小轮转保持不变；`LOG_MAX_BYTES` 与 `LOG_BACKUP_COUNT` 分别作用于每个日期文件。不同日期的文件不由 `LOG_BACKUP_COUNT` 自动清理，原有 `logs/app.log*` 不迁移、不重命名、不删除。
- 替代/复查条件：需要按保留天数或总空间自动清理、集中采集，或日志时区不再使用主机本地时区时复查。
- 依据文件：[`docs/implementation/NFR-021日志按日期分层实施计划.md`](implementation/NFR-021日志按日期分层实施计划.md)、[`app/core/logging.py`](../app/core/logging.py)、[`docs/stage/handoff.md`](stage/handoff.md)
