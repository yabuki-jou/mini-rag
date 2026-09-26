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

- 状态：已废弃，由 DEC-022 替代
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

- 状态：已废弃，由 DEC-022 替代
- 首次纳入台账：2026-09-13
- 背景：`pixie_qa/` 根目录混合已删除请假领域、仍在使用的制度 Agent 和智慧档案 P14 评测，且若直接清理旧目录，活跃的制度 Agent 会失去质量入口。
- 决策：当前评测统一在 `evals/` 下按业务域组织；制度 Agent 使用 `evals/policy_agent/`，智慧档案使用 `evals/archive/`。`pixie_qa/` 只保留本地工具状态与忽略的结果，不再保存评测源码；已删除请假领域的旧运行器、数据集和追踪不保留。
- 约束：注入检索结果的模型评测只证明回答层，不得表述为真实 Chroma 检索通过；真实检索、跨轮状态和故障恢复必须使用独立评测并分别报告。评测不得读取真实业务数据或把 Ground Truth 注入生产请求。
- 替代/复查条件：评测工具的根目录规则发生变化，或新增独立业务域评测时复查。
- 依据文件：[`docs/archive/legacy-policy-agent/README.md`](archive/legacy-policy-agent/README.md)、[`evals/archive/README.md`](../evals/archive/README.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-012：旧制度 Collection 与当前 768 维 Embedding 对齐

- 状态：已废弃，由 DEC-022 替代；仅作为历史迁移记录保留
- 首次纳入台账：2026-09-13
- 背景：智慧档案切换到 bge-base/768 后，旧制度 Agent 仍访问冻结为 512 维的 `mini_rag_knowledge_chunks_v1`，真实查询被 Chroma 拒绝并映射为 HTTP 503。
- 决策：仅在制度 Collection 条目数和 PostgreSQL 待重建文档聚合计数均为 0 时，保留原名与 cosine 度量执行空库重建，以 768 维三字段 canary 锁定并验证新维度，随后精确清理 canary。任何非空状态都必须停止，不能自动删除或覆盖。
- 影响：制度 Collection 现与共享的 bge-base/768 配置一致；一次虚构制度文档真实链路已通过。该单题只证明链路恢复，不替代 `evals/policy_agent/` 的回答质量评测，也不证明多轮或故障恢复质量。
- 替代/复查条件：Embedding 再次更换、制度 Collection 出现待迁移数据，或制度与智慧档案改为独立 Embedding 配置时复查。
- 依据文件：[`docs/archive/legacy-policy-agent/制度Agent-Collection维度迁移实施计划.md`](archive/legacy-policy-agent/制度Agent-Collection维度迁移实施计划.md)、历史重建脚本（已随旧运行入口下线）、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-013：制度 Agent 应用服务按会话、消息、审计和执行分层

- 状态：已废弃，由 DEC-022 替代
- 首次纳入台账：2026-09-13
- 背景：原 `app/services/agent/sessions.py` 同时承担会话创建、Checkpoint 转换、工具审计和 Graph 执行，任一职责变化都会扩大同一模块的回归范围。
- 决策：`sessions.py` 只负责会话创建和知识库范围校验；`messages.py` 负责 Checkpoint 与用户可见消息转换；`audit.py` 负责工具调用脱敏和审计持久化；`execution.py` 负责 Graph 调用、错误映射、提交边界和响应构造。Router 直接依赖各职责模块，不保留聚合转发入口。
- 约束：执行成功与失败路径的审计、提交和错误转换顺序保持不变；不改变 HTTP API、Prompt、Graph、数据库 Schema、Checkpoint、工具参数或日志脱敏规则。跨模块只导入公开函数。
- 替代/复查条件：Agent 新增写工具、人工中断恢复或不同事务边界，并经用户确认新的应用服务划分时复查。
- 依据文件：[`docs/archive/legacy-policy-agent/智能体逻辑导览.md`](archive/legacy-policy-agent/智能体逻辑导览.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-014：文件日志按本地日期分层

- 状态：已确认
- 首次纳入台账：2026-09-14
- 背景：固定写入单个 `logs/app.log` 不便于按自然日期定位和管理运行日志。
- 决策：`LOG_FILE` 继续作为日志基准路径；应用按每条日志记录的本地时间写入其父目录下的 `YYYY/MM/YYYY-MM-DD<扩展名>`。运行中的服务跨过日、月或年边界时自动切换，不要求重启；无扩展名的基准路径默认使用 `.log`。
- 影响：控制台日志、request ID、格式和按大小轮转保持不变；`LOG_MAX_BYTES` 与 `LOG_BACKUP_COUNT` 分别作用于每个日期文件。不同日期的文件不由 `LOG_BACKUP_COUNT` 自动清理，原有 `logs/app.log*` 不迁移、不重命名、不删除。
- 替代/复查条件：需要按保留天数或总空间自动清理、集中采集，或日志时区不再使用主机本地时区时复查。
- 依据文件：[`docs/implementation/NFR-021日志按日期分层实施计划.md`](implementation/NFR-021日志按日期分层实施计划.md)、[`app/core/logging.py`](../app/core/logging.py)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-015：项目档案助手采用持久化会话 MVP

- 状态：已确认
- 首次纳入台账：2026-09-14
- 背景：现有 `archive-questions` 已完成单次“受控检索 → Grounded Prompt → 带引用回答”闭环；但用户还会提出“当前有哪些正式资料”“先找到验收资料再核对原文”这类需要在结构化目录与原文证据之间选择查询路径的问题。既有 `search_company_policy` 的语义仅适用于企业制度，不能直接复用到项目档案。
- 决策：在不替换 FR-039 直接问答的前提下，新增一个只读的项目档案助手，作为后续 FR-042。该助手使用持久化会话，且每个会话必须绑定已验证的 `user_id + project_id + kb_id + thread_id`。会话复用现有 Agent 的 SQLite Checkpoint 与 PostgreSQL 脱敏工具审计机制；旧制度会话与项目档案会话必须以受控会话类型区分，档案会话必须持久化项目绑定。MVP 仅提供两个只读工具：`list_formal_archives` 用于查询当前项目的正式档案目录，`search_confirmed_archive_evidence` 用于检索当前项目正式档案的可追溯原文证据。
- 安全与回答约束：模型只能提交查询词或受控目录筛选条件；`user_id`、`project_id`、`kb_id`、正式文档范围及最终引用对象均由服务端注入或映射。原文事实性回答必须引用 `CONFIRMED` 且未被可见性阻断的档案原文；无充分证据时必须拒答。目录工具仅用于正式档案导航，不得把人工无证据字段包装为原文依据。助手不提供写工具、自动确认、自动清单关联、跨项目检索或自动合规结论。
- 影响：LangGraph 仅负责单个项目档案助手的“范围校验 → 模型判断 → 工具调用 → 模型回答”循环，不引入运行时多 Agent 编排。`archive-questions` 继续作为简单事实问答的默认、确定性路径；档案助手通过独立项目 API 暴露，后续必须先完成 FR-042 的需求、架构、数据库/API 设计、实施计划和 TDD，再开始迁移与实现。
- 替代/复查条件：用户确认不再需要跨轮项目档案追问、工具审计或 Checkpoint 恢复时，可评估降级为无状态调用；需要写入档案、清单或外部业务系统时，必须另行决策，不得由本决策自动授权。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/技术架构.md`](design/技术架构.md)、[`docs/design/接口设计.md`](design/接口设计.md)、[`app/agents/archive/graph.py`](../app/agents/archive/graph.py)、[`app/services/archive/catalog.py`](../app/services/archive/catalog.py)、[`app/services/archive/retrieval.py`](../app/services/archive/retrieval.py)

## DEC-016：FR-042 需求评审问题裁决（部分已废弃）

- 状态：部分已废弃；硬 60 秒时限、工具批次和并发删除保证由 DEC-019 替代，其余范围与安全裁决继续有效
- 首次纳入台账：2026-09-14
- 背景：FR-042 首次需求评审发现持久化前提与旧架构基线冲突，并缺少助手质量门、D5/D6 拒答依据、重试幂等、客户端输入、删除阻断、项目删除联动和会话类型隔离口径。
- 决策：保留 DEC-015 确认的 SQLite Checkpoint，并在架构阶段显式修订“只保留给旧 Agent”的旧基线；档案业务事实仍不得进入 Checkpoint。证据回答沿用 P14-D5 结构化充分性判定与 D6 文档绑定/Top-8，不恢复统一 Reranker 拒答阈值。新增助手固定集，复用 12 个正式问答样本并增加目录与两轮追问，安全门保持 100%，其他新门槛由主 Agent 产生基线、用户确认后冻结。客户端请求体出现未声明范围字段、空白消息或超过 2000 个 Unicode 码点的消息统一返回 `422 VALIDATION_ERROR`。同一请求内的连接/超时重试不得重复消息和工具审计，客户端主动重发视为新轮次。删除一经受理即持续阻断可见性，失败不自动恢复；项目删除成功前必须清理其档案助手会话、Checkpoint 线程和工具记录。MVP 不提供会话列表、主动删除或重命名，但必须双向隔离制度会话与档案会话。
- 影响：FR-042 需求新增可执行 AC、固定集入口和删除生命周期；技术架构、数据库与 API 设计必须逐项落实上述边界。单轮最多两个模型发起的工具调用且总处理时限为 60 秒；工具业务空结果与基础设施失败必须返回不同语义，后者不得驱动模型继续回答。
- 替代/复查条件：改用 PostgreSQL 保存会话消息、增加会话管理动作、改变 D5/D6 证据判定路线、放宽工具数量或引入写工具时，必须另行确认并更新本决策。
- 依据文件：[`docs/review/FR-042-项目档案助手/需求评审.md`](review/FR-042-项目档案助手/需求评审.md)、[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md`](review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md)、[`docs/review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md`](review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md)

## DEC-017：FR-042 失败轮次、目录引用与输入规范（部分已废弃）

- 状态：部分已废弃；失败轮次持久化与硬工具批次口径由 DEC-019 替代，目录字段、输入规范与临时引用口径继续有效
- 首次纳入台账：2026-09-14
- 背景：FR-042 独立复审确认原 R1～R7 已闭环，同时发现失败轮次落盘、目录临时引用、目录字段白名单、无效工具调用计数和换行长度规范仍可能产生多种实现解释。
- 决策：模型或工具最终失败时保存一个用户消息和一个固定助手不可用消息，已发起工具按 `tool_call_id` 最多保存一条 `FAILED` 脱敏记录，会话时间只更新一次；Checkpoint 读写失败不产生完成标记、不展示中间消息且不更新会话时间。目录工具只返回标题、资料类型、文档日期、编制单位、项目阶段五个登记字段及来源标记；临时 `document_ref` 只绑定本轮目录结果与服务端来源映射，证据工具不接收它，后续追问根据已见文件名或登记信息重新查询并重新校验范围。消息先把 `CRLF` 和单独 `CR` 规范化为 `LF`，再去除首尾 Unicode 空白并按 Python 字符串 Unicode 码点计数。参数校验失败的模型工具调用计入单轮两个调用上限。
- 影响：项目档案助手的失败历史、审计条数、会话排序、目录 DTO、跨轮追问和边界测试均有唯一判定口径；DeepSeek 不可用不阻止创建会话，发送消息则按模型最终失败口径形成完整失败轮次且不调用工具。原文引用复用 FR-039 的响应结构。
- 替代/复查条件：若后续允许按目录标识定向检索、改变失败轮次可见性、引入请求幂等键或调整工具调用预算，必须另行确认并更新本决策。
- 依据文件：[`docs/review/FR-042-项目档案助手/需求评审.md`](review/FR-042-项目档案助手/需求评审.md)、[`docs/design/需求说明.md`](design/需求说明.md)

## DEC-018：FR-042 架构评审裁决（已废弃）

- 状态：已废弃，由 DEC-019 替代
- 首次纳入台账：2026-09-14
- 背景：FR-042 主 Agent 架构评审发现项目锁可能跨越模型调用、并行 Tool Call 口径冲突、证据 DTO 可能携带持久化标识、D5/D6 没有公共复用边界，以及 60 秒预算无法由现有客户端兑现；代码复核还发现正式检索仍保留可选阈值过滤，现有制度 Agent 工具策略最多尝试三次。
- 决策：消息轮次只锁定对应 `AgentSession`，创建会话与项目删除只在协调事务中锁 Project；项目删除按 Project→ARCHIVE 会话顺序锁定并先提交 `DELETING`。单次模型决策可以产生一个或两个 Tool Call：整批不超过剩余预算时全部执行，超过剩余预算时整批不执行并直接形成稳定失败轮次。证据工具只向模型和 Checkpoint 返回请求内编号、文件名、位置与摘录；持久化文档/Chunk 标识和分数保留在请求内服务端映射，工具审计只保存脱敏计数与状态。`questions.py` 必须抽出接收既有候选的公共 D5/D6 判定函数，FR-039 与 FR-042 共用，不能复制判定或重复检索。FR-042 新增不经过统一 Reranker 拒答阈值的 Top-8 候选入口，并使用总尝试次数为 2 的独立模型/工具重试策略。
- 超时与持久化：60 秒必须由可实际中止等待的模型、Chroma 和数据库调用适配器共同执行，不能只在调用返回后检查时钟；现有无参数缓存客户端行为不得静默冒充逐调用超时。新增不保存消息正文的 Agent 轮次元数据，使历史与模型上下文只接受 PostgreSQL 已完成轮次；Checkpoint 已写而 PostgreSQL 最终提交失败时，该轮隐藏并返回 503。模型/工具失败但固定失败轮次和业务记录均已保存时返回 `200 + FAILED + 固定不可用消息`；Checkpoint 或最终业务记录保存失败、无法形成完整轮次时返回稳定 503。
- 其他边界：模型上下文不回放历史 ToolMessage，只投影已完成的用户/助手消息和本轮工具结果；现有制度入口固定只接受制度会话，项目入口固定只接受档案会话。目录字段的 `has_source_evidence` 是根据字段标记和当前证据关系计算的对外派生值。当前项目解释器已静态确认 `SqliteSaver.delete_thread(thread_id)` 存在，且在内存 SQLite 上连续删除不存在的线程保持幂等；真实文件、真实线程和项目删除联动仍须通过集成测试。
- 影响：数据库设计必须承接 `agent_type/project_id/status`、Agent 轮次元数据及必要的历史引用映射；API 设计必须区分完整失败轮次与未完成基础设施失败。架构主审问题闭环不替代仍待补的需求独立二次复审，也不授权迁移或代码实现。
- 替代/复查条件：改为无锁租约、允许截断执行部分并行工具、把工具标识写入 Checkpoint、改变 60 秒硬上限、统一失败 HTTP 语义或恢复 Reranker 统一拒答阈值时，必须另行确认。
- 依据文件：[`docs/review/FR-042-项目档案助手/架构评审.md`](review/FR-042-项目档案助手/架构评审.md)、[`docs/design/技术架构.md`](design/技术架构.md)、[`docs/design/需求说明.md`](design/需求说明.md)、[`app/services/archive/retrieval.py`](../app/services/archive/retrieval.py)、[`app/services/archive/questions.py`](../app/services/archive/questions.py)、[`app/agents/archive/graph.py`](../app/agents/archive/graph.py)

## DEC-019：FR-042 收敛为最小可演示闭环

- 状态：已确认
- 首次纳入台账：2026-09-14
- 背景：FR-042 的第二轮架构方案为处理 PostgreSQL/SQLite 非原子提交、历史引用恢复、并发删除和硬 60 秒截止，引入了 AgentTurn、AgentTurnCitation、会话删除状态、动态预算适配器及多 Tool Call 批次规则。它们可以提高生产级一致性，但显著扩大迁移、事务、故障注入与维护范围，不符合当前“先完成最小能通过功能”的目标。
- 保留范围：持久化项目档案助手会话；服务端固定绑定 `user_id + project_id + kb_id + thread_id`；制度/档案会话类型隔离；目录与正式证据两个只读工具；CONFIRMED 与删除可见性过滤；D5/D6 公共判定、无统一阈值的 Top-8 候选；当前回答引用、无依据拒答、脱敏工具审计；输入校验；连接/超时最多额外尝试一次。
- 简化决策：模型每次决策最多调用一个工具，单轮最多顺序调用两个；多个 Tool Call 直接稳定失败。复用同一受保护 SQLite Checkpoint 文件，以全局唯一 `thread_id` 和服务端会话类型隔离。只在 `AgentSession` 增加 `agent_type` 与可空 `project_id`，不新增 AgentTurn、AgentTurnCitation 或 `DELETING` 状态。历史只返回完整用户/助手消息正文，不返回 ToolMessage，也不承诺恢复历史引用。模型、工具、Checkpoint 或 PostgreSQL 最终失败统一返回稳定 503，不保存“完整失败轮次”作为业务承诺。
- 已知限制：MVP 不解决 SQLite 与 PostgreSQL 的原子提交，不实现跨 DeepSeek/Chroma/数据库的动态 60 秒总预算，不保证同一会话并发消息或消息与项目删除并发时的强一致性。项目删除仍按“幂等清理 Checkpoint → 删除工具日志/会话/项目”执行，任一层失败不得返回成功；上述并发动作不进入本地验收场景。
- 影响：DEC-018 全部废弃；DEC-016 的硬 60 秒时限、工具批次和并发删除保证废弃；DEC-017 中失败轮次持久化与并行工具批次部分废弃，其余范围、安全、目录字段、输入规范和临时引用口径继续有效。需求与架构主审按本决策通过，下一步可以进入数据库设计、API 设计和 Implementation Plan，但在计划确认前不得开始代码实现。生产级轮次一致性、历史引用恢复、并发删除状态机和硬时限如以后确有需要，必须基于真实故障或容量证据重新立项。
- 依据文件：[`docs/review/FR-042-项目档案助手/需求评审.md`](review/FR-042-项目档案助手/需求评审.md)、[`docs/review/FR-042-项目档案助手/架构评审.md`](review/FR-042-项目档案助手/架构评审.md)、[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/技术架构.md`](design/技术架构.md)

## DEC-020：FR-042 MVP 引用、状态与错误语义

- 状态：已确认
- 首次纳入台账：2026-09-14
- 背景：DEC-019 生效后的复核发现，FR-039 的 `ArchiveRetrievalItemRead` 必含持久化文档/Chunk 标识和检索分数，不能同时满足 FR-042 的脱敏响应要求；同时还需固定模型 Tool Call 越界、公开回答状态、孤立 Checkpoint 消息及客户端超时的最小口径。
- 决策：FR-042 使用独立的脱敏引用 DTO，但复用 FR-039 的 `filename`、`location_type`、`location_start`、`location_end`、`excerpt` 字段名称与语义，不复用含 `document_id`、`chunk_id`、`score`、`reranker_score` 的完整 DTO。公开 `answer_status` 复用 `ArchiveAnswerStatus`：目录和有据回答为 `ANSWERED`，无依据为 `REFUSED_NO_EVIDENCE`；`CATALOG` 只用于 Graph 内部路由。
- 错误与证据：合法客户端请求触发的多个 Tool Call、未注册工具或累计第三次调用属于服务端依赖的模型输出违反契约，不归因于客户端，返回 `503 ARCHIVE_AGENT_MODEL_OUTPUT_INVALID`；真实模型/工具连接、超时、Checkpoint 或 PostgreSQL 最终失败返回 `503 ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`。模型没有调用本轮目录工具时不得陈述目录结果，没有调用本轮证据工具或证据不足时不得陈述档案原文事实，统一走无依据拒答。
- 历史与超时：历史接口和后续模型输入使用同一完整轮次投影，忽略中间 ToolMessage 和任何没有最终 AIMessage 的孤立轮次。MVP 不设置独立上下文轮数或码点上限，该限制只适合短会话演示。实施时必须为当前 `get_chat_model()` 增加固定、可配置的请求超时，但不引入跨 DeepSeek、Chroma 和数据库传播的动态总预算。
- 影响：需求与技术架构中的 C1～C7 已获得唯一口径；数据库/API 设计必须新增档案助手专用创建/响应 Schema，创建请求不复用要求客户端提交 `kb_id` 的 `AgentSessionCreate`，项目范围只来自路径和服务端 ProjectContext。该决策不授权代码实现，仍须先完成数据库设计、API 设计和 Implementation Plan。
- 依据文件：[`docs/review/FR-042-项目档案助手/需求评审.md`](review/FR-042-项目档案助手/需求评审.md)、[`docs/review/FR-042-项目档案助手/架构评审.md`](review/FR-042-项目档案助手/架构评审.md)、[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/技术架构.md`](design/技术架构.md)、[`app/schemas/archive_retrieval.py`](../app/schemas/archive_retrieval.py)、[`app/schemas/archive_question.py`](../app/schemas/archive_question.py)、[`app/schemas/archive_agent.py`](../app/schemas/archive_agent.py)

## DEC-021：证据候选携带已确认文档标题用于身份绑定

- 状态：已确认
- 首次纳入台账：2026-09-16
- 背景：FR-042 第三轮真实模型固定集严格通过 15/17，但 Q-02、Q-03 在候选已含正确字段值时仍错误拒答。两条候选只向模型暴露英文文件名，无法稳定关联用户问题中的中文文档名称；继续堆叠 Prompt 不能补足该身份信息。
- 决策：`search_confirmed_archive_evidence` 的模型可见安全候选新增可空 `document_title`，只复用当前 `CONFIRMED` 档案的已确认 `TITLE` 字段，不新增数据库列或第二套标题来源。该字段只用于定位目标文档；事实回答仍必须由同一 `document_ref` 下的原文摘录支持。标题缺失时返回 `null`，不得从文件名、问题或模型输出推断。人工确认但无原文证据的标题可以作为身份元数据，不能单独作为其他原文事实的回答依据。
- 安全边界：标题查询继续受当前服务端 `project_id`、正式状态和删除可见性阻断约束；模型不能提交或覆盖标题、`document_ref` 或范围参数。`document_title` 可以进入本轮 ToolMessage 和公共判定 Prompt；Checkpoint 仍只保存既有受信用户/助手投影，不新增 ToolMessage 持久化。标题不进入当前公开引用 DTO、历史引用、工具审计或持久化标识映射；文档 UUID、Chunk ID 和分数仍不得泄露。
- 评测边界：应回答场景必须证明目标标题与答案事实属于同一 `document_ref`。固定集标题只能来自既有去标识化正式资料元数据，禁止为通过门槛伪造；真实模型复验须在 TDD、本地回归和 Step 6 完成后另获用户授权。
- 替代/复查条件：公开引用需要展示标题、允许模型按持久化文档标识检索、或标题不再由人工确认的正式字段提供时，必须另行确认并更新契约。
- 依据文件：[`docs/review/FR-042-项目档案助手/真实模型质量评测/评测复盘.md`](review/FR-042-项目档案助手/真实模型质量评测/评测复盘.md)、[`docs/review/FR-042-项目档案助手/真实模型质量评测/问题排查与解决方案.md`](review/FR-042-项目档案助手/真实模型质量评测/问题排查与解决方案.md)、[`app/services/archive/catalog.py`](../app/services/archive/catalog.py)、[`app/agents/tools/archive_tools.py`](../app/agents/tools/archive_tools.py)

## DEC-022：当前产品收敛为智慧档案单主线

- 状态：已确认
- 首次纳入台账：2026-09-17
- 背景：通用知识库问答、普通 Chat 和企业制度 Agent 没有当前 Vue 展示入口，并与智慧档案的知识库、文档、会话和检索概念形成两套公开产品语义，增加学习、维护和演示成本。FR-030～FR-042 已形成完整的项目档案闭环。
- 决策：当前正式产品主线只保留智慧档案 V1、FR-039 档案问答和 FR-042 项目档案助手。删除通用知识库、普通文档、检索测试、普通 Chat 和制度 Agent 的公开 API 与运行代码，不保留 `410` 或占位兼容路由；相邻 Vue 同步删除相应 API 方法、类型和本地会话逻辑。
- 数据兼容：不删除或迁移现有 PostgreSQL 表与数据、SQLite Checkpoint、Chroma Collection、原文件或 Alembic 历史。`KnowledgeBase`、`Document` 继续作为项目档案数据底座；`AgentSession`、`AgentToolCallLog` 继续服务 FR-042。`AgentType.POLICY` 与数据库约束保留用于兼容历史行，档案入口必须继续拒绝此类历史会话，项目删除不得误清理无项目绑定的历史线程。
- 质量边界：保留 FR-039/FR-042 的 D5/D6、Top-8、引用、拒答、范围隔离和固定集基线。制度 Agent 评测与实现资料迁入历史归档，不再作为当前发布门；本决策不授权清理旧数据，也不把历史链路验证表述为当前能力。
- 替代关系：本决策替代 DEC-001、DEC-011、DEC-012 和 DEC-013 的当前产品口径；DEC-012 仍可作为旧 Collection 曾执行过的历史迁移记录阅读。DEC-015～DEC-021 中涉及 FR-042 项目档案助手的约束继续有效，其中“制度/档案双向入口隔离”收敛为“档案入口拒绝历史 POLICY 会话”。
- 依据文件：[`docs/implementation/智慧档案单主线拆除实施计划.md`](implementation/智慧档案单主线拆除实施计划.md)、[`docs/stage/handoff.md`](stage/handoff.md)

## DEC-023：FR-042 MVP 恢复当前项目最近会话

- 状态：已确认
- 首次纳入台账：2026-09-18
- 背景：FR-042 已把会话范围保存在 PostgreSQL、把消息保存在 SQLite Checkpoint、把脱敏工具记录保存在 PostgreSQL，但 Vue 只在内存中保存当前 `session_id`。浏览器刷新后旧会话仍存在，普通用户却无法重新发现，只能继续新建并累积会话。
- 决策：FR-042 MVP 增加当前项目最近会话恢复，不另立 FR-043。新增 `GET /projects/{project_id}/agent-sessions/latest`，在服务端已验证的 `user_id + project_id + kb_id + ARCHIVE` 范围内按 `updated_at DESC → created_at DESC → id DESC` 返回最多一个现有会话；无会话返回 `200 null`，不自动创建。Vue 进入档案助手页面后自动恢复该会话，并调用既有端点加载完整可见历史和脱敏工具记录。
- 边界：不持久化客户端 `session_id`，不提供通用会话列表、任意旧会话选择、重命名或用户删除；不恢复历史引用，不展示不完整轮次；不新增数据库字段、索引或迁移，不修改 Prompt、Graph、检索、D5/D6、模型或工具语义。
- 影响：`docs/implementation/FR042-项目档案助手MVP实施计划.md` 增加 P08；需求、数据库和接口设计同步增加 AC-FR-042-20 与最近会话契约。确定性测试证明发现、授权、排序和 Vue 恢复行为，不替代既有真实模型质量证据。
- 替代/复查条件：需要任意会话列表、会话选择、标题、分页、删除、历史引用恢复，或项目内会话量产生可测排序性能问题时，必须重新设计并确认。
- 依据文件：[`docs/design/需求说明.md`](design/需求说明.md)、[`docs/design/接口设计.md`](design/接口设计.md)、[`docs/design/数据库设计.md`](design/数据库设计.md)、[`docs/implementation/FR042-项目档案助手MVP实施计划.md`](implementation/FR042-项目档案助手MVP实施计划.md)

## DEC-024：冻结证据与扩展来源支持分开评估

- 状态：已确认
- 首次纳入台账：2026-09-25
- 背景：P153548 `LUSHAN-01` 对照中，冻结标注指定 appraisal 第 16 页；其他真实页面也可能支持同一批准贷款金额。只用指定页会忽略真实来源支持情况，只用宽泛来源支持又会破坏冻结结果的历史可比性。
- 决策：冻结评估集继续按其指定目标证据及现有质量门评分，数据与门槛保持不变。新增扩展评估时，另行评分人工核实的其他真实来源是否支持回答；替代来源分数不得并入或替代冻结目标证据分数。未经逐条人工核实的引用标为待复核，不计作支持通过。
- 影响：检索覆盖、答案事实正确、冻结目标命中、替代来源支持、引用有效和拒答表现分别报告。涉及金额时区分批准/承诺金额与实际提款金额，并核对来源机构归属。扩展样例及方案见 [`P153548 扩展来源双口径评测样例方案`](review/FR-042-项目档案助手/P153548扩展来源双口径评测样例方案.md)。
- 替代/复查条件：冻结评估集或质量门需变更、替代来源纳入正式验收门，或扩展来源审核流程改为自动化时，另行评审并记录替代决策。
- 依据文件：[`P153548 真实资料召回问题排查与解决方案`](review/FR-042-项目档案助手/P153548真实资料召回问题排查与解决方案.md)、[`P153548 扩展来源双口径评测样例方案`](review/FR-042-项目档案助手/P153548扩展来源双口径评测样例方案.md)

## DEC-025：贷款语境下补充世界银行术语召回

- 状态：已确认
- 首次纳入台账：2026-09-25
- 背景：P153548 的 `LUSHAN-01` 目标原文已在正式索引语料中，但原问题的 dense Top-30 未带入标准证据；真实资料对照显示补充 `IBRD IDA` 候选并以该表达重排可使冻结目标页的公开覆盖候选进入 Top-8，精确摘录命中仍单独诊断。
- 决策：FR-039 与 FR-042 共用的回答候选检索，仅在当前轮用户问题同时出现 World Bank 白名单指称和贷款/融资白名单意图时，保留原查询 Top-30，再补充固定 `IBRD IDA` 查询 Top-30；按 `chunk_id` 稳定去重，不截断并集，以补充表达重排一次并返回 Top-8。FR-042 门控取当前轮原始用户消息，模型生成的工具查询只用于检索；对外回答仍依据原始用户问题。
- 边界：维持服务端项目与正式文档过滤、现有公开 API、D5/D6、冻结评估集和引用契约；不扩展为通用机构等价、混合检索或融合排序。按 TDD 实施并分别报告确定性回归、真实检索、答案引用和扩展来源诊断；Pixie 注入候选结果不代替真实检索证据。
- 替代/复查条件：触发词表、候选预算、排序表达或算法需要改变，或真实资料验收未达到实施计划的门槛时，先重新评审并更新方案。
- 依据文件：[`FR-039/FR-042 世界银行贷款证据补充召回实施计划`](implementation/FR039-FR042世界银行贷款证据补充召回实施计划.md)、[`P153548 真实资料召回问题排查与解决方案`](review/FR-042-项目档案助手/P153548真实资料召回问题排查与解决方案.md)
