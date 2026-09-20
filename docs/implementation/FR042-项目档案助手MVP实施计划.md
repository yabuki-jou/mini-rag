# FR-042 项目档案助手 MVP 实施计划

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 对应需求 | `FR-042`、`BR-035～BR-039`、`NFR-022～NFR-024` |
| 决策基线 | `DEC-019`、`DEC-020` |
| 设计基线 | `docs/design/需求说明.md`、`docs/design/技术架构.md`、`docs/design/数据库设计.md`、`docs/design/接口设计.md` |
| 后端分支 | `codex/fr-042-archive-agent-mvp`（已存在） |
| 相邻 Vue 仓库 | `../mini-rag-milvus-vue`；分支 `codex/fr-042-archive-agent-mvp` 已完成最小接入 |
| 文档状态 | 已由用户确认；FR042-P00～P08 已完成；P08 真实刷新恢复联动已通过 |
| 更新日期 | 2026-09-18 |

> DEC-022 已将制度 Agent 运行入口下线。本计划中记录的“双向隔离”、制度 Graph 与旧 Router
> 只描述 FR-042 当时的实施基线；当前契约收敛为项目档案助手单向拒绝历史 `POLICY` 会话。

## 2. 目标、边界与完成定义

本计划只实现一个可在本地 Vue 工作台与 Swagger 演示的项目档案助手闭环：

```text
创建项目档案助手会话
→ 在同一会话连续提问
→ DeepSeek 在正式档案目录、正式原文证据两个只读工具间选择
→ 服务端固定注入 user_id + project_id + kb_id
→ 目录确定性投影，或 D5/D6 有据回答，或固定拒答
→ SQLite Checkpoint 保存完整可见轮次
→ PostgreSQL 保存会话和脱敏工具审计
→ 当前响应返回脱敏引用
```

本期必须保持：

- 只读取当前用户、当前项目、当前知识库中可见的 `CONFIRMED` 档案；
- 项目档案助手按 ARCHIVE 五要素校验，并拒绝历史 `POLICY` 会话；
- FR-039 与 FR-042 共用同一个“无统一阈值 Top-8 候选 → D5/D6 判定 → 服务端引用映射”入口；
- 原始 `document_id/chunk_id/score/reranker_score` 只存在于当前请求内存；
- 当前 HTTP `answer` 与 Checkpoint 最终 AIMessage 正文完全相同；
- 普通自动测试不调用真实 DeepSeek，模型工具选择和语义质量由独立固定集验证。

本期明确不做：

- `AgentTurn`、`AgentTurnCitation`、历史引用恢复；
- 通用会话列表、任意旧会话详情或选择、重命名、用户删除；P08 只恢复最近一个会话；
- `DELETING` 会话状态、消息并发保证、跨存储原子事务；
- 流式响应、WebSocket、批量消息、跨项目会话迁移；
- 写工具、自动确认、自动清单关联、运行时多 Agent 编排；
- 硬 60 秒总预算、Chroma/数据库新增超时适配器；
- 把 FR-039 直接问答替换成 Agent。

只有以下证据全部形成后，FR-042 才可标记实现完成：

1. 每个可观察行为都有实际执行过的 RED，再有最小 GREEN；
2. 后端相关测试、后端全量测试和 `compileall` 通过；
3. PostgreSQL 真实迁移与约束门通过，不能以 SQLite 替代；
4. 共享 SQLite Checkpoint 的真实读写、类型隔离与删除通过；
5. Vue 单元测试、类型检查和构建通过，并完成真实 `/api` 代理联调；
6. 真实 DeepSeek 固定集达到 §10 的既定安全门和质量门；
7. Swagger、本地 Vue、README、验收报告和 handoff 与实际代码一致。

## 3. 实际代码基线与实施约束

计划编写时已按当前代码和 Git 状态核验以下事实：

- `AgentSession` 目前只有 `user_id/kb_id/thread_id` 等制度会话字段，尚无 `agent_type/project_id`；
- 当前迁移 head 为 `0010_account_auth`，`0005` 已有 `uq_projects_id_kb_id`；
- `agent_tool_call_logs.agent_session_id` 的旧外键在 `0003_agent_api.py` 中匿名创建；
- `get_chat_model()` 是无参缓存单例，当前未显式配置请求超时；
- FR-039 的候选获取和 D5/D6 判定仍位于 `app/services/archive/retrieval.py`、`questions.py`；
- 当时存在的制度 Agent Graph、工具、消息投影和重试规则没有被改名复用，现已按 DEC-022 下线；
- 现有 SQLite Checkpoint 已使用关闭 pickle fallback 的 `JsonPlusSerializer`；
- 相邻 Vue 工作台已有 FR-034～FR-041，但当前仓库处于 `feature/ui-feedback-and-layout` 且有未提交改动。

因此实施时必须遵守：

- 后端继续在 `codex/fr-042-archive-agent-mvp` 工作；开始每个切片前重新确认分支和工作区；
- Vue 在任何修改前先处理现有脏工作区归属，再从用户确认的基线建立对应 `codex/` 分支；不得自动
  stash、reset、丢弃或夹带现有改动；
- 不覆盖当前后端工作区内与 FR-042 无关的已有改动；提交时只能显式暂存获准文件，禁止
  `git add -A`；
- 本计划确认前不创建迁移、Schema、Router、Graph、测试或 Vue 代码；
- 未实际运行的测试、PostgreSQL、Chroma、Checkpoint、DeepSeek 或浏览器流程不得写成通过。

## 4. 最小实现结构

实施时优先采用以下模块边界；若实际依赖版本迫使文件名小幅调整，职责和公开契约不得改变：

```text
app/
├── agents/
│   ├── archive/
│   │   ├── state.py
│   │   ├── prompts.py
│   │   ├── graph.py
│   │   └── runtime.py
│   ├── checkpoint.py
│   └── tools/archive_tools.py
├── dependencies/archive_agent.py
├── routers/archive_agent.py
├── schemas/archive_agent.py
├── services/agent/
│   ├── archive_sessions.py
│   ├── archive_execution.py
│   ├── archive_messages.py
│   └── archive_audit.py
└── services/archive/
    ├── catalog.py
    ├── retrieval.py
    └── questions.py
```

关键实现约束如下：

1. `ArchiveAgentState` 只保存 LangChain 可序列化消息、服务端范围 UUID 的字符串形式、本轮工具调用数
   和内部回答类型；不得保存 Session、Engine、模型、向量客户端或原始候选对象。
2. 每次 HTTP 消息执行创建请求级原始候选注册表，以 `tool_call_id` 保存证据工具返回的原始 Top-8；
   工具写入 Graph/ToolMessage 的只能是安全投影。最终判定读取最后一次成功证据调用对应的原始候选，
   并在 `finally` 中清空注册表，禁止跨请求复用。
3. 目录临时 `document_ref` 只存在当前目录工具安全结果中；不能进入 HTTP 响应，也不能作为下一轮
   或证据工具参数。
4. Graph 的模型节点只保留控制工具调用所需消息。模型自由文本不直接成为最终回答；最终 AIMessage
   只能由目录模板、公共证据判定或固定拒答生成。
5. Checkpoint 的创建、读取和幂等 `delete_thread` 抽到共享组件；Admin 与 Archive Runtime 复用同一
   文件和严格序列化配置，但各自保留独立 Graph 和消息投影。
6. 项目删除不能为了删除线程而构造 DeepSeek 或完整 Archive Runtime；删除协调只调用独立的
   Checkpoint 存储组件。
7. `get_chat_model()` 增加配置项 `deepseek_request_timeout_seconds`，默认值先固定为 30 秒；保留既有
   无参调用兼容入口，并允许 Archive Runtime 明确取得 `max_retries=0` 的模型实例。FR-042 只在应用层
   对已识别的连接失败或超时额外尝试一次，不能与客户端隐藏重试叠加。若当前依赖版本的超时参数
   名称不同，只适配构造参数，不改变既有调用方公开行为或 FR-042 的总尝试次数。
8. 公共 `judge_archive_answer(question, candidates, model)` 返回领域结果或抛中性异常，不包含 HTTP
   状态码。FR-039 保持映射 `ARCHIVE_ANSWER_UNAVAILABLE`；FR-042 映射
   `ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`。

## 5. Multi-Agent Development Workflow

### 5.1 主 Agent（GPT-5.6 Sol）

主 Agent 负责：

- 在每个切片开始前核对本计划、实际代码、Git diff 与前置测试；
- 明确本切片的单一可观察行为、允许修改文件和停止条件；
- 审查实现 Agent 提交的 RED 输出，确认失败原因确实来自缺失行为；
- 审查 GREEN 后的 Git diff、需求映射、越权边界、错误码、数据泄露和测试充分性；
- 运行或复核阶段回归、PostgreSQL/Checkpoint/Chroma/DeepSeek 真实门；
- 发现范围或架构必须变化时停止实现，先修订设计和本计划并交用户确认。

### 5.2 实现 Agent（GPT-5.6 Luna）

边界明确后，优先把单个切片交给实现 Agent。实现 Agent只能：

- 先编写一个会因目标行为缺失而失败的测试，并实际运行得到 RED；
- 写使该测试通过的最小实现，再实际运行得到 GREEN；
- 在相关测试持续通过时做必要重构；
- 修改切片明确列出的代码、测试和必要文档。

实现 Agent不得自行改变 FR-042 对外 API、数据库核心结构、固定错误码、两工具边界、Top-8、
Checkpoint 策略或延期范围。发现必须改变时立即交回主 Agent。

### 5.3 主审闭环

每个学习切片结束前固定执行：

```text
主 Agent 核对 RED 证据
→ 审查最小 GREEN diff
→ 运行切片相关测试
→ 运行后端全量测试与 compileall
→ 记录已证明与未证明边界
→ 用户确认学习检查点后进入下一切片
```

若当前运行环境没有可用的实现子 Agent，主 Agent也必须按相同 TDD 和 diff 审查纪律执行，不能跳过
RED 或把预期失败当成已验证证据。

## 6. TDD 实施切片

### FR042-P00 分支、脏工作区与基线门（已完成，2026-09-15）

目标：建立不污染既有改动的实施起点，不产生功能代码。

- 后端确认分支为 `codex/fr-042-archive-agent-mvp`，保存 `git status --short` 和目标 diff 范围；
- 运行当前后端全量测试和 `compileall`，把基线失败与本期新增失败分开记录；
- 检查默认迁移 head、当前 SQLModel 表和 OpenAPI 基线；不读取业务数据、`.env` 或上传文件；
- Vue 开始前检查当前脏工作区。若现有改动仍未归档，停止 Vue 修改并请用户决定基线；确认后才创建
  Vue 的 `codex/fr-042-archive-agent-mvp` 分支并运行 `npm test`、`npm run typecheck`、`npm run build`；
- 输出：基线记录、允许修改文件清单、不可覆盖的已有改动清单。

停止条件：分支不符、目标文件存在无法区分的用户改动、全量测试有未解释失败，或 Vue 基线不明确。

### FR042-P01 会话类型与数据库迁移（已完成，2026-09-15）

目标：只完成 `AgentSession` 的最小结构扩展和数据库约束，不增加轮次表。

RED：

1. 在模型测试中证明当前模型不能表达 `POLICY/ARCHIVE` 与可空 `project_id`；
2. 修改迁移链测试，要求 head 为 `0011_archive_agent_scope`，当前先失败；
3. 修改注释契约测试，把 `0011` 注释并入映射，当前先失败；
4. 增加启用 `PRAGMA foreign_keys=ON` 的 SQLite 行为测试，覆盖类型/项目组合、复合外键与级联；
5. 扩展 PostgreSQL 集成测试，先要求真实具名约束、`MATCH SIMPLE` 与 cascade。

最小 GREEN：

- 新增受控 `AgentType`，`AgentSession.agent_type` 默认 `POLICY`，`project_id` 可空；
- 创建 `0011_archive_agent_scope`，`down_revision = "0010_account_auth"`；
- 先加列并回填 `POLICY`，再增加：
  - `ck_agent_sessions_agent_type`；
  - `ck_agent_sessions_type_project`；
  - `fk_agent_sessions_project_kb`，`MATCH SIMPLE ON DELETE CASCADE`；
  - `ix_agent_sessions_project_id`；
- 通过 Inspector 按受限列和引用表精确查找旧匿名工具日志外键，再以
  `fk_agent_tool_logs_session_cascade ON DELETE CASCADE` 重建；
- 为 `agent_type/project_id` 写列注释；`tests/models/test_schema_comment_contract.py` 显式合并
  `0011` 注释；
- downgrade 在存在 ARCHIVE 会话时明确拒绝，避免无声破坏范围信息。

REFACTOR 与验证：

- 保持既有 POLICY 行默认值和制度会话创建行为不变；
- 相关模型/迁移测试通过后运行全量测试和 `compileall`；
- 在隔离 PostgreSQL 测试库验证 0010→0011、约束拒绝、cascade、旧 POLICY 行保留和 migration head；
- SQLite 只证明应用测试行为，不冒充 PostgreSQL DDL 证据。

### FR042-P02 共享模型、候选与证据判定基础（已完成，2026-09-15）

目标：先建立 FR-039/FR-042 可安全共用的最小底座，确保 FR-039 对外契约不回归。

RED：

1. 配置和模型工厂测试要求 DeepSeek 有固定可配置请求超时，Archive Runtime 使用隐藏重试为 0 的
   模型实例，既有无参调用仍兼容；
2. 检索服务测试要求回答候选固定 Top-8、保留既有顺序、显式不使用公开检索阈值；
3. 问答服务测试要求 `judge_archive_answer` 接收调用方候选，0 候选不调用模型，解析失败抛中性异常；
4. FR-039 回归测试要求状态、回答、引用字段和 `ARCHIVE_ANSWER_UNAVAILABLE` 映射保持不变；
5. 测试证明 FR-039 与模拟 FR-042 调用同一公共函数时，使用相同候选得到相同状态和引用选择。

最小 GREEN：

- 在配置和 `get_chat_model()` 中接入固定请求超时，不在日志输出密钥或地址；
- `retrieval.py` 增加无统一阈值的回答候选入口，FR-039 和后续 FR-042 共用；公开检索 API 保留原行为；
- `questions.py` 抽出候选判定结果类型、中性异常和 `judge_archive_answer`；
- `answer_archive_question` 保留空候选固定拒答，并在非空时调用公共函数、映射既有错误码；
- 不复制 D5/D6 Prompt、JSON 解析、引用编号验证或固定拒答文案。

REFACTOR 与验证：相关服务测试、FR-039 Router 测试、全量测试、`compileall`。

### FR042-P03 HTTP Schema、会话创建与双向隔离（已完成，2026-09-15）

目标：先打通不依赖模型的会话创建和五要素授权，为后续 Graph 提供可信范围。

RED：

1. Schema 测试覆盖空对象创建、`extra="forbid"`、消息换行规范化、Unicode 空白与 1～2000 码点；
2. 会话服务测试覆盖服务端生成 `user_id/project_id/kb_id/thread_id/ARCHIVE`，创建不构造模型或
   Checkpoint，`updated_at == created_at`；
3. 依赖与 Router 测试覆盖项目不存在/越权、会话五要素任一不匹配；ARCHIVE 五要素查找服务和依赖
   接收 POLICY ID 时必须拒绝，实际 ARCHIVE 路由对 POLICY ID 的拒绝留到 P06 再验；
4. 反向回归测试要求既有 `/agent-sessions` 入口拒绝 ARCHIVE ID，且拒绝前不读取其 Checkpoint/日志；
5. OpenAPI 测试固定本切片唯一路由 `POST /projects/{project_id}/agent-sessions` 的请求/响应
   Schema 和 `422 VALIDATION_ERROR`，并断言 `.../messages`（POST/GET）与 `.../tool-calls`
   三个端点尚未出现在 OpenAPI 中。

最小 GREEN：

- 新增 `app/schemas/archive_agent.py` 中已冻结的六个公开 DTO；
- 新增 ARCHIVE 会话创建/查找服务和依赖，以 `session_id + user_id + project_id + kb_id + agent_type`
  整体匹配；
- 新增 `POST /projects/{project_id}/agent-sessions`，只写 PostgreSQL；
- 收紧制度会话读取、消息和日志入口，只接受 `POLICY + project_id IS NULL`；
- 在 `app/main.py` 注册 `app/routers/archive_agent.py`；本切片该 Router 只包含
  `POST /projects/{project_id}/agent-sessions`，消息、历史和工具日志三个路由留到 P06 注册，
  不得注册任何占位、返回错误或未实现空壳端点。

REFACTOR 与验证：Schema、依赖、会话、Router 相关测试，全量测试和 `compileall`。

### FR042-P04 两个只读工具与请求内安全映射（已完成，2026-09-15）

目标：完成可独立测试的目录与证据工具，不引入 Graph 控制流。

RED：

1. 目录服务测试覆盖当前项目、`CONFIRMED`、删除可见性阻断、五类筛选、稳定排序、分页和空目录；
2. 目录投影测试覆盖五字段 `value/source/has_source_evidence`，并证明输出无 UUID、版本、关键字或正文；
3. 目录正文测试逐字固定分页首行和五字段模板，覆盖 `未登记`、页内重编号及禁止字段；
4. 证据工具测试覆盖服务端注入范围、固定 Top-8、查询规范化、禁止 `top_k/document_ref/范围字段`；
5. 安全测试证明 ToolMessage 和可序列化 Graph 输入中不存在内部 ID/分数，原始候选仅在
   `tool_call_id` 请求级注册表中，并在成功与异常后清理；
6. 工具参数测试覆盖目录 `page_size<=20`、筛选白名单、无效调用占一次调用额度所需的受控错误。

最小 GREEN：

- 在 `catalog.py` 增加 Agent 专用查询投影，与 FR-038 共用正式范围/筛选谓词但不复用含 ID 的 DTO；
- 实现固定目录正文格式：`第 {page} 页，本页 {n} 份，共 {total} 份：`；
- 在 `retrieval.py` 的公共回答候选入口上建立证据安全投影；
- 新增两个工具适配器，只接受模型白名单参数并注入服务端范围；
- 新增请求级证据注册表和安全序列化边界；目录 `A1..A20` 与证据 `S1..S8` 每次调用重新编号。

REFACTOR 与验证：目录/检索/工具测试、FR-038/FR-039 回归、全量测试和 `compileall`。

### FR042-P05 Archive Graph、Checkpoint 与可信最终投影（已完成，2026-09-16）

目标：实现一轮最多两个顺序工具调用、完整轮次持久化和稳定失败，不接 HTTP Router。

RED：

1. Graph 测试覆盖零调用固定拒答、一次目录、一次证据、目录→证据、证据→目录；
2. 多 Tool Call、未注册工具和第三次调用必须在执行前失败为
   `ARCHIVE_AGENT_MODEL_OUTPUT_INVALID`，不得部分执行或写伪造工具日志；
3. 工具参数无效占额度，模型只可用剩余一次修正；参数、权限、拒答和结构错误不重试；
4. 模型或工具连接/超时最多额外尝试一次，重试不增加工具调用数；最终依赖失败不根据失败文本作答；
5. 最后一次成功调用唯一决定最终类型；较早结果不得进入最终正文、引用或目录来源；
6. 证据分支把规范化用户原问题和最后一次原始 Top-8 交给公共判定层，不二次检索、不合并候选；
7. 0 候选不调用判定模型，直接固定拒答；目录为空返回固定空目录文案；
8. Checkpoint 测试证明最终 AIMessage 等于 HTTP 候选结果，工具自由文本不持久化；不完整轮次被统一投影忽略；
9. 共享文件中的 POLICY/ARCHIVE `thread_id` 互不串读，严格 serializer 保持关闭 pickle fallback；
10. `delete_thread` 幂等，且调用它不构造 DeepSeek。

最小 GREEN：

- 新增 `ArchiveAgentState`、Prompt、Graph 和 Runtime；
- 模型每次决策只允许零或一个 Tool Call，单轮累计最多两个；
- Archive Graph 使用独立重试分类和尝试计数，不复用制度工具的三次尝试规则；
- 目录、证据和拒答都由服务端可信最终化节点生成唯一最终 AIMessage；
- 抽取共享 Checkpoint 存储组件，Admin Runtime 保持原行为，Archive Runtime 使用同一文件；
- Graph 异常只转成中性执行失败，HTTP 稳定错误码留给应用服务映射。

REFACTOR 与验证：Archive/Admin Graph 与 Runtime 测试、Checkpoint 临时文件集成测试、全量测试、
`compileall`。

### FR042-P06 消息执行、历史、审计与四端点闭环（已完成，2026-09-16）

目标：把可信 Graph 接入应用服务和已冻结的四个 HTTP 端点。

RED：

1. 消息 Router 测试覆盖连续两轮同一 `thread_id`、第二轮读取第一轮完整用户可见上下文；
2. 成功响应固定为 `session_id/answer_status/answer/citations/request_id`，禁止范围和内部检索字段；
3. 当前证据引用只含五个脱敏字段，目录/拒答引用为空；正文 `[Sn]` 不由服务端注入或解析；
4. 历史测试覆盖新会话空数组、完整轮次、隐藏 ToolMessage、忽略孤立 HumanMessage/ToolMessage，历史引用恒空；
5. 审计测试覆盖两工具安全摘要、`created_at ASC`、允许错误码和禁止敏感内容；
6. 同一 `(session, tool_call_id)` 的自动重试只落一条日志并累计耗时；客户端主动重发产生新轮次；
7. PostgreSQL 最终写入、Checkpoint、工具或模型失败映射
   `ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`，FR-042 不泄出 FR-039 错误码；
8. 输入 422 在读取会话、Graph、Checkpoint 和审计前结束；各种 404/403 不泄露资源存在性。
9. OpenAPI 测试固定消息、历史和工具日志三个新增端点的请求/响应 Schema 和消息端点
   `422 VALIDATION_ERROR`，并把 P03 的“其余三个端点不存在”负向断言替换为四个端点齐备；
   三个实际 ARCHIVE 路由接收 POLICY 会话 ID 时均返回 `404 ARCHIVE_AGENT_SESSION_NOT_FOUND`。

最小 GREEN：

- 实现档案会话消息执行、最终响应转换和成功后 `updated_at` 更新；
- 实现档案专用完整轮次投影，历史接口和下一轮模型输入调用同一函数；
- 实现档案专用审计摘要、upsert/唯一约束收敛和读取；
- 完成消息、历史、工具日志三个 Router；
- 对已知业务错误使用 `AppError`，数据库失败先 rollback，未知异常只记服务端安全日志。

REFACTOR 与验证：四端点 API 测试、服务测试、历史 POLICY 会话拒绝、全量测试、`compileall`。

### FR042-P07 项目删除联动（已完成，2026-09-16）

目标：空项目返回 `204` 前幂等清理其 ARCHIVE Checkpoint、工具日志和会话，保留知识库与 POLICY。

RED：

1. 有未删除文档时仍返回 `409 PROJECT_HAS_DOCUMENTS`，不开始会话/Checkpoint 清理；
2. 空项目删除时锁定 Project，枚举且只枚举该项目 ARCHIVE 会话；
3. 每个线程先幂等删除 Checkpoint，再在 PostgreSQL 事务中删除日志、会话和 Project；
4. Checkpoint 失败时 rollback 且不返回成功；Checkpoint 已删而 PostgreSQL 提交失败时重试安全；
5. 删除成功后旧 `project_id/session_id` 均不可访问；KnowledgeBase、其他项目和 POLICY 会话保留；
6. SQLite cascade 与显式清理结果一致，PostgreSQL 真实 cascade 另行验证。

最小 GREEN：

- 扩展既有 `delete_empty_project` 协调流程，不新增公开路由或删除状态；
- Project 行锁保持到 PostgreSQL 提交，期间同项目会话创建/上传阻塞作为 MVP 接受代价；
- Checkpoint/PostgreSQL 联动失败统一映射 `ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`；
- 不承诺消息执行与项目删除并发，测试和演示均顺序执行。

REFACTOR 与验证：项目删除/Agent 隔离测试、真实临时 Checkpoint 文件、PostgreSQL 集成门、全量测试、
`compileall`。

完成证据：先以缺少 `checkpoint_path` 参数形成 4 个有效 RED，再最小扩展既有
`delete_empty_project`。文档门禁发生在任何 Checkpoint 清理前；服务只枚举目标项目 ARCHIVE 会话，
按 Checkpoint → 工具日志 → 会话 → Project 的顺序清理，并保持 Project 行锁至最终提交。真实临时
SQLite Checkpoint 文件证明目标线程删除、其他项目与 POLICY 线程保留，以及 Checkpoint 失败 rollback、
数据库提交失败后的幂等重试。P07 相关回归为 `59 passed`，后端全量为
`572 passed, 2 skipped, 148 warnings`，`compileall app tests` 与 `git diff --check` 通过。
用户授权后又在随机隔离 PostgreSQL schema 中执行 0001→0011 全链迁移、约束、项目删除服务与真实
临时 Checkpoint 联动，结果为 `1 passed`；测试 schema 经所有权核验删除，随后独立查询确认不存在。

## 7. 后端集成门与 Swagger 验收

P01～P07 全部通过后，主 Agent执行一次不修改业务设计的集成收口：

1. 对照 AC-01～AC-19 检查每项至少一个确定性测试落点；
2. 在隔离 PostgreSQL 执行迁移并检查 revision、列、索引、CHECK、复合外键、`MATCH SIMPLE`、cascade；
3. 使用临时共享 Checkpoint 文件验证两类 Graph 线程隔离、连续两轮、历史投影和删除；
4. 使用受控 Chroma/正式档案资料验证当前项目、待确认、其他项目和删除阻断过滤；
5. 用 Swagger 验证四个端点的 201/200/422/403/404/503 代表性契约；
6. 运行：

```powershell
C:\D\venvs\mrh\Scripts\python.exe -m pytest -q
C:\D\venvs\mrh\Scripts\python.exe -m compileall -q app tests evals scripts
```

这里的 Mock 模型只证明控制流、错误映射和数据边界，不证明 DeepSeek 的工具选择或回答质量。

## 8. 相邻 Vue 工作台最小接入

### 8.1 分支门

相邻 Vue 仓库当前存在未提交改动。开始任何 Vue 文件修改前必须：

1. 重新核对其 `AGENTS.md`（若存在）、Git 分支和 diff；
2. 由用户确认现有改动应作为基线、先提交或另行处理；
3. 创建 `codex/fr-042-archive-agent-mvp` 分支；
4. 保存并运行现有测试、类型检查、构建基线。

不得为创建干净分支自行 stash、reset 或删除用户改动。

### 8.2 Vue TDD 切片

最小 UI 不替换现有 FR-039 `ArchiveQuestionPanel`，新增独立“档案助手”入口和面板：

- 新建会话按钮；不增加会话列表、重命名或删除；
- 当前会话消息列表、输入框和顺序发送；
- 当前回答的 `answer_status`、正文和五字段脱敏引用；
- 历史刷新按钮和脱敏工具调用记录；
- 422、404、503 使用既有统一错误展示，不显示内部异常；
- 项目切换时清空当前助手会话，避免跨项目复用 `session_id`；
- 客户端不提交或展示 `user_id/project_id/kb_id/thread_id/agent_type`，不解析正文 `[Sn]`。

RED 顺序：API DTO/路径 → Store 创建与发送 → 连续两轮/项目切换 → 面板交互/引用 →
历史与工具日志 → 错误状态。每个 RED 后只写最小 GREEN。

完成门：

```powershell
npm test
npm run typecheck
npm run build
```

随后用 Vite `/api` 代理顺序验证：登录 → 选择已有项目 → 新建会话 → 目录题 → 原文题 →
无据题 → 历史 → 工具日志。直接后端 URL 与 Vite 代理分别验证，不能只凭单元测试宣称联调通过。

## 9. AC-01～AC-19 确定性测试映射

| 验收项 | 主要切片 | 必须证明的确定性行为 |
|---|---|---|
| AC-01、AC-18 | P03、P05、P06 | 创建不调模型/Checkpoint；同一线程连续两轮；模型失败仅影响发送 |
| AC-02、AC-10、AC-15、AC-17 | P04、P05 | 正式范围、删除阻断、五字段模板、分页、临时引用不跨轮 |
| AC-03、AC-05、AC-06、AC-19 | P02、P04、P05、P06 | Top-8、公共判定、空候选短路、固定拒答、脱敏当前引用、原始 ID 只在请求内 |
| AC-04、AC-08、AC-12、AC-14 | P03、P04、P05、P06 | extra forbid、输入规范化、五要素查找、双向类型隔离、参数白名单和调用预算 |
| AC-07、AC-09、AC-16 | P05、P06 | 完整轮次、HTTP/AIMessage 一致、重试去重、安全审计和两类稳定 503 |
| AC-11 | P01、P07 | 线程、日志、会话、项目清理；KB/POLICY 保留 |
| AC-13 | P05 | 多调用、未知工具、第三次调用执行前稳定失败且不部分执行 |

所有安全与隔离断言必须 100% 通过；任何一项失败都不能用模型平均分抵消。

## 10. 真实 DeepSeek 固定集与 Eval-Driven Development

该切片只在确定性实现和真实本地链路稳定后执行。开始时先确认项目解释器、Pixie 依赖和仓库既有
`evals/archive/` 约定，复用现有评测包，不新建互相冲突的第二套 QA 根目录。

### 10.1 评测资产

新增或扩展：

```text
evals/archive/
├── runnable.py
├── evaluators.py
├── datasets/archive-agent-mvp.json
└── docs/
    ├── archive-agent-project-analysis.md
    ├── archive-agent-entry-point.md
    ├── archive-agent-eval-criteria.md
    └── archive-agent-evaluator-mapping.md
```

固定集组成：

- 复用 §12.3 的 12 个问题：8 个有据、2 个无据、2 个隔离；
- 新增至少 2 个目录导航问题；
- 新增 2 组两轮追问，每组两轮都标注会话、必需/允许/禁止工具和预期结果；
- 每条记录标注轮次、工具路径、回答状态、预期目录项或引用、隔离范围；Ground Truth 不进入生产请求。

### 10.2 运行与评审顺序

按 Eval-Driven Development 连续完成：

1. 核对生产入口并写项目分析、入口说明和评测标准；
2. 为实际 Archive Agent 请求入口补足追踪，使工具选择、工具结果安全投影、最终状态和引用可评审；
3. Runnable 必须调用生产 Graph/应用服务路径；外部 PostgreSQL/Chroma 资料可受控准备或捕获，但
   不得 Mock、Stub 或拦截 DeepSeek；
4. 先生成单条真实参考 Trace，核对没有内部 ID、分数、完整原文或 Ground Truth 泄漏；
5. 编写确定性 evaluator 与必要的 Agent evaluator，并用单元测试验证 evaluator 本身；
6. 创建固定数据集，先跑单条真实 `pixie test`，再跑全量；
7. 对每个未通过项、每个 agent-evaluator 评论和所有 pending 评分逐条分析；
8. 写结果汇总、根因、改进行动和 Step 6 完整性校验，不回写或美化原始运行结果。

### 10.3 冻结门槛

- 8 个有据问题：至少 `7/8` 达到既有回答质量门；
- 2 个无据问题：`2/2` 正确拒答；
- 2 个隔离问题：`2/2` 不跨项目回答；
- 所有返回引用：文件、位置与摘录正确率 100%；
- 2 个目录问题：`2/2` 选择目录工具并返回正确正式集合；
- 2 组两轮追问：`2/2` 保持同一项目/线程且每轮工具和结果符合标注；
- 范围不可覆盖、类型隔离、删除阻断、工具上限、重试去重：确定性安全门 100%；
- FR-039 与 FR-042 在同候选上的状态、候选选择和五个引用字段一致；
- 新增但尚未冻结的主观质量或延迟指标只记录基线，必须由用户确认后才能成为门槛。

真实模型固定集未完成前，只能说“确定性功能通过”，不得说“FR-042 质量通过”。

## 11. 文档、验收与交付

实现完成后再按实际证据更新：

- `README.md`：本地启动、四端点、Vue 演示路径、已知限制；
- `docs/archive/legacy-policy-agent/智能体演示步骤.md`：历史制度 Agent 演示资料；当前演示以项目档案助手为准；
- `docs/review/验收报告.md`：区分 Mock/SQLite/PostgreSQL/Checkpoint/Chroma/DeepSeek/Vue 证据；
- `docs/review/验证冻结清单.md`：只登记用户确认后的新门槛与真实结果；
- `docs/design/需求说明.md`、`技术架构.md`、`数据库设计.md`、`接口设计.md`：只同步实际实施状态，
  不借实现修改已冻结契约；
- `docs/stage/handoff.md`：记录完成切片、测试数字、真实环境限制和下一步；
- `docs/decisions.md`：只有用户确认了新的产品/架构/数据/安全/质量决策才追加。

提交和推送不属于本计划的自动动作。用户明确要求提交时，主 Agent先重新核对两个仓库各自的 diff，
只显式暂存本期文件，并分别报告分支、提交和推送结果。

## 12. 停止与回退条件

出现以下任一情况必须停止当前实现切片并交回主 Agent，不得靠扩大范围绕过：

- 实际代码与冻结需求/API/数据库设计冲突，且没有唯一兼容实现；
- 必须新增公开字段、路由、状态、错误码、数据表或改变 Top-8/D5/D6 语义；
- 原始文档/Chunk ID 或分数无法在当前依赖下阻止进入 Checkpoint、日志或 HTTP；
- LangGraph/SQLite 版本不能以现有严格 serializer 安全保存或删除线程；
- 为满足重试必须改变“最多两个模型工具调用”或出现重复可见消息/审计；
- PostgreSQL 真实约束与 Alembic 设计不一致；
- Vue 脏工作区无法确认归属；
- 真实 DeepSeek 评测暴露安全越界或固定门槛未通过。

回退只撤销本切片新增内容，不使用 `git reset --hard`、`git checkout --` 或删除用户已有改动。

### FR042-P08 最近档案助手会话恢复（已完成确定性实现）

目标：补齐 PostgreSQL 已保存会话在 Vue 刷新后无法重新发现的最小用户闭环，不改变 Graph、
Checkpoint 消息格式、工具审计、D5/D6 判定或历史引用边界。

冻结契约：

1. 新增 `GET /projects/{project_id}/agent-sessions/latest`，使用现有 `ProjectContext` 后只查询
   当前 `user_id + project_id + kb_id + agent_type=ARCHIVE` 范围。
2. 排序固定为 `updated_at DESC → created_at DESC → id DESC`，最多返回一个
   `ArchiveAgentSessionRead`；无会话返回 `200 null`，不自动创建。
3. 复用现有 `ix_agent_sessions_project_id` 收窄单项目范围，不新增字段、索引或迁移。
4. Vue 进入已授权项目的档案助手页面时自动恢复最近会话；找到后自动并行读取完整可见历史和
   脱敏工具记录。项目切换、新建会话和延迟响应继续通过项目 ID、会话 ID 与请求序号隔离。
5. 不把 `session_id` 写入 localStorage；不增加通用列表、选择、重命名或删除；历史
   `citations=[]` 和不完整轮次隐藏规则保持不变。

TDD 顺序：

1. 后端 Service RED：无会话、稳定排序、五要素隔离、数据库异常脱敏；随后最小 GREEN。
2. 后端 Router RED：Bearer/项目授权、返回最近会话、`200 null`、旧 POLICY 与跨项目不可见；
   随后最小 GREEN。
3. Vue API RED：固定 URL、GET 方法和可空响应；随后最小 GREEN。
4. Vue Store RED：自动恢复后加载历史与工具记录、无会话空状态、项目切换/新建会话使延迟响应
   失效；随后最小 GREEN。
5. Vue View/Panel RED：进入档案助手路由自动触发恢复，并在恢复期间禁用新建；随后最小 GREEN。
6. 主 Agent 审查公开 API、无迁移边界、错误语义、测试覆盖和两个仓库 Diff；运行后端相关测试、
   后端全量、`compileall`、Vue 全量、`typecheck`、`build` 和 `git diff --check`。
7. 自动化完成后另行执行真实 PostgreSQL + Checkpoint + Vite 刷新恢复验收；该验收不调用
   DeepSeek，不改变现有真实模型固定集结论。

完成证据：后端 RED 为 `7 failed, 21 passed`，GREEN 为相关 `28 passed`；全量回归为
`492 passed, 2 skipped`。Vue RED 为 `6 failed, 91 passed`，GREEN 为相关 `97 passed`；主审补充
登录后认证状态变化竞态测试，RED 为 `1 failed, 16 passed`，GREEN 为 `17 passed`，最终全量为
`136 passed`。后端 `compileall`、Vue `typecheck`/`build` 和两仓库差异检查通过。上述证据是
SQLite/HTTP 契约与前端自动化证据。

真实刷新恢复验收使用固定命名的隔离 PostgreSQL Schema、独立 SQLite Checkpoint 和真实
`5173/api → 8000` 代理。经代理创建临时账号、项目和档案助手会话后，使用真实 Archive Graph 与
固定本地模型写入一轮完整 Checkpoint，并写入一条符合白名单的脱敏工具记录；该步骤不调用 DeepSeek，
也不改变语义质量结论。浏览器进入档案助手页面后自动显示该轮历史和工具记录，整页刷新后仍自动恢复
同一内容。刷新前存储核对为会话、工具记录、Checkpoint 各 1；经正式项目删除接口清理后均为 0。
随后隔离 Schema 和临时 SQLite 文件均已删除，FastAPI 与 Vite 临时进程已停止。

## 13. 当前状态与后续边界

本计划已由用户确认，`FR042-P00`～`FR042-P08` 已完成。P08 最近会话端点与 Vue 自动恢复已通过
隔离 PostgreSQL + SQLite Checkpoint + Vite + 浏览器整页刷新的真实联动验收。该验收使用固定本地
模型写入受控历史，没有调用 DeepSeek，因此只证明会话发现与恢复链路，不新增模型质量证据。DEC-021 标题身份修正完成后，第四轮真实
DeepSeek 固定集严格通过 17/17：有据单轮 8/8、无据 2/2、隔离 2/2、目录 2/2、多轮 2/2、隐私
17/17、受控失败 1/1；32 条人工评审无 pending，官方 Step 6 校验通过。FR-042 固定集真实模型质量门
本轮通过，但该证据使用注入的虚构、去标识化外部世界，不代表真实 PostgreSQL/Chroma/文件端到端。
Swagger/HTTP 真实后端闭环已通过：随机隔离 PostgreSQL Schema 迁移至 `0011`，并完成真实
Chroma/BGE/Reranker/文件/Checkpoint/DeepSeek 的建档、目录问答、有据问答、无据拒答、历史、脱敏审计
与跨存储清理。相邻 Vue 已完成独立入口、五端点客户端、Store、
会话面板、最近会话自动恢复和项目切换隔离，前端回归 `136 passed`，类型检查与构建通过。此前已用随机隔离 PostgreSQL
Schema、临时 Chroma Collection、文件与 Checkpoint，通过 Vite `5173/api` 完成真实注册登录、建档、
确认索引、目录题、有据题、无据题、三轮历史和脱敏工具日志，最终状态与引用均符合冻结契约；清理后
隔离持久化资源为零。P08 随后已新增浏览器 DOM 刷新恢复证据并完成隔离清理；第五轮固定
集真实模型调用须重新授权。提交前审查发现的后端 Runtime 构造错误码泄漏与 Vue 旧工具名已按 TDD
修复；P08 当前后端全量 `492 passed, 2 skipped`，Vue 全量 `136 passed`，类型检查、构建与编译检查通过。
后端 P08 功能已由 GitHub PR #6 合入 `main`，注释完善提交也已合并；Vue P08 功能分支已快进合入
`main`。两个仓库的 `main` 均已推送，并在合并后通过后端 `492 passed, 2 skipped`、Vue `136 passed`、
后端编译检查及 Vue 类型检查/生产构建。历史引用恢复、任意旧会话选择与跨存储强一致性仍不属于
本 MVP；若要扩展，先按 DEC-023 重新确认需求、方案与验收门。
