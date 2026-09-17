# Mini RAG Handwrite

从空目录手写的企业知识库 RAG 与只读 Agent 后端练习项目。当前运行时代码使用
FastAPI、SQLModel、PostgreSQL、Alembic、LangChain/LangGraph、Chroma、本地 BGE 与
DeepSeek。Swagger 用于 API 契约诊断；AV1-P14 已将相邻 `mini-rag-milvus-vue` 作为本地工作台，完成上传、解析、检索、引用问答、物理删除和脱敏审计的分阶段真实代理链路验收。
全部向量能力已迁移到 Chroma HTTP 客户端与单机服务配置。当前运行和开发基线是本地环境：
Chroma 的本机命名空间、范围过滤、精确删除，以及 Docker API、PostgreSQL、Chroma 与本地 BGE
历史联合健康验证曾通过；当前最近 health 为 `200`，API、database、Chroma 与 Embedding 全部正常，Embedding 实际维度为 768。使用者已私下修正运行时 Reranker 路径并重启服务，随后完成 16 份资料、88 个 Final Chunk 和 20 题的真实 Top-8 检索与 DeepSeek 问答复测。项目 `.env` 未读取或修改。一次云主机 Chroma 独立实验仅保留为历史可行性证据；是否部署云端、采用何种
拓扑及资源规格均未确认；V1 本地功能验收完成不表示已经完成云端部署。

下一阶段唯一业务方向是“智慧档案与企业文档智能”。需求、架构、数据库、API 与实施计划
基线已确认；AV1-P01～P08、P09 确认—INDEX/取消确认、P10 清单关联/目录/审计、P11 正式检索、
P12 证据问答和 P13 物理删除的实现切片已完成。P09 已完成真实确认—INDEX 路由纵向链路、
真实 Chroma/BGE canary 与取消确认清理；P11 已完成真实单文档 canary（512 维 cosine、命中 1 条、
10 次请求 P95 约 125.97 ms）。P14 C3/C4 固定集结果属于历史阶段复验：当时三种纯稠密表示、阶段 C 本地 Reranker 及 Top-20 候选池均不能同时满足
“有据至少 7/8、无据拒答 2/2”。阶段 C.1 已确认一题标准证据未进入完整 Chroma Top-10，且两个无据题仍有高 Reranker
分数；C.3-A 已通过开发环境隐藏诊断接口观测完整 Top-20，确认 `GROUNDED-01/05/07` 未进入候选池；C.3-B 已在同一 Top-20 与本地 Reranker 下复验字段值表示，C.3-C 又复验固定查询表达 `档案证据检索问题：{query}`，两次结果均为 63/63 个 Chunk 获得上下文、有据 `7/8`、无据拒答 `0/2`、隔离 `2/2`，不存在可冻结的独立重排阈值。C.3-C 未更换模型、Chunk、候选池或评测集；新的查询模板、Chunk/窗口调整、换模型或修改评测集均需另行重新授权；该历史阶段之后的 DeepSeek 问答质量和 P13 真实跨存储故障恢复已完成，详见下文。
FR-034/035～FR-041 已完成前端接入；FR-039 的一般有据、无据、待确认排除、项目隔离和真实合同日期页面场景均已通过，FR-040 的真实物理删除代理链路与 FR-041 的真实脱敏审计代理链路也已通过。C4-A Top-30 候选池已获独立授权并完成真实复验：完整候选 `12/12`，公开覆盖式召回 `7/8`，严格诊断 `5/8`；`GROUNDED-01/05` 为匹配语义不一致，`GROUNDED-07` 在两种语义下均未命中。随后经独立授权完成 C4-B，仅替换 Reranker 查询表达并确认模式为 `c4_b`，Embedding 保持基线；候选池仍为 `12/12`，公开覆盖式召回 `7/8`，严格诊断 `5/8`，无据拒答 `0/2`、隔离 `2/2`，P95 `3765.54 ms`，两个无据最高分升至 `0.986686`、`0.952463`。当前不冻结阈值；C4-C、Chunk/窗口调整、换模型和评测集修改均需重新授权，当前不推进标书投标、标书解析生成或投标合规审查。原员工请假领域
已经删除，不再提供余额、申请、人工确认或决定接口。

P14-D5 的结构化一次调用与 D6-A 可信文档绑定已实现；D6-B 经独立授权只把 `archive-questions` 的内部候选从 Top-5 扩至 Top-8，外部响应形状保持不变。2026-09-08 在 D1 base/768 隔离环境运行正式 AV1-P02 12 题：候选池完整 `12/12`，Top-8 公开覆盖 `8/8`，有据回答并正确引用 `7/8`、无据拒答 `2/2`、隔离拒答 `2/2`，达到既定质量门。Q-02 与 Q-07 已正确回答并引用；Q-01 第一候选含准确合同日期但仍错误拒答。随后已将仓库默认配置与 Compose 正式基线切换为 `bge-base-zh-v1.5`、768 维和 `evidence_values`，并重建正式 `archive_final_chunks`。重建时数据库没有确认档案，因此汇总为 0 文档、0 Chunk；768 维 canary 写入、删除和联合健康检查均通过。

Pixie 正式 12 题记录中每题仍出现两个时间重叠、输入输出相同但 duration 略有差异的 `llm_span_trace`，重复原因尚未核实；静态代码和单测只证明一次应用层 `model.invoke`，不能据此宣称一次或两次网络请求。

## 架构

```mermaid
flowchart LR
    Client --> FastAPI
    FastAPI --> PostgreSQL[(PostgreSQL 业务库)]
    FastAPI --> Files[原文件]
    FastAPI --> Chroma[(Chroma 向量库)]
    FastAPI --> Graph[LangGraph Agent]
    Graph --> DeepSeek
    Graph --> Checkpoint[(Checkpoint SQLite)]
    Graph --> Chroma
```

- PostgreSQL：用户、知识库、文档、聊天、Agent 会话与审计。
- Chroma：存储可重建的 Chunk、向量和 `user_id + kb_id` 隔离字段。Compose 中的 API 使用内部
  `chroma:8000`；本机直接运行 Python 时可经回环地址 `127.0.0.1:8001` 访问，局域网和公网不可访问。
  当前命名空间为 `mini_rag_tenant / mini_rag_chroma`，既有制度检索 Collection 为
  `mini_rag_knowledge_chunks_v1`。该 Collection 已在确认空库后从历史 512 维原名重建为 768 维，
  与当前 BGE 配置一致；一次虚构制度文档的真实上传、处理、Agent 问答与引用链路已通过并完成
  PostgreSQL/Chroma 临时范围清理，该单题不替代完整制度质量评测。
- 文件系统：上传原文件。
- SQLite：仅保存 LangGraph Checkpoint，不再作为业务数据库。

详细设计见：

- [文档总览](docs/README.md)
- [需求说明](docs/design/需求说明.md)
- [技术架构](docs/design/技术架构.md)
- [数据库设计](docs/design/数据库设计.md)
- [API 设计](docs/design/接口设计.md)
- [智慧档案实施计划](docs/implementation/智慧档案V1实施计划.md)
- [项目档案助手 MVP 实施计划](docs/implementation/FR042-项目档案助手MVP实施计划.md)
- [最终验收报告](docs/review/验收报告.md)
- [P14 C.1 双排序诊断决策](docs/review/P14-rag检索质量改进/P14-C1-双排序诊断决策.md)
- [P14-D5 证据充分性与拒答判定层方案](docs/review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md)
- [P14-D6 文档绑定与 Top-8 证据保留方案](docs/review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md)
- [检索质量问题分析与改进策略](docs/review/P14-rag检索质量改进/检索质量问题分析与改进策略.md)
- [P14-C2 检索质量优化方案](docs/review/P14-rag检索质量改进/P14-C2-检索质量优化方案.md)
- [Chroma 迁移决策](docs/design/Chroma迁移决策.md)
- [Parser 冻结规则](docs/design/智慧档案V1解析器设计.md)
- [既有 Agent 实施计划](docs/implementation/既有检索与智能体实施计划.md)
- [Agent 逻辑导览](docs/implementation/智能体逻辑导览.md)
- [Agent 演示步骤](docs/implementation/智能体演示步骤.md)
- [V1 候选发布说明](docs/releases/V1发布说明.md)

以 `LEARNING_PLAN.md`、`docs/stage/handoff.md`、`docs/decisions.md` 和 V1 候选发布说明为当前状态来源；P14
检索质量的 C2～D6 方案保留为历史实验与决策证据，不再作为当前待执行任务。
`docs/review/` 保留当前评审、质量门和验收证据；仅供追溯的旧评审与阶段材料位于
`docs/archive/`，不作为当前实现状态的来源。当前制度 Agent 与智慧档案评测分别位于 `evals/policy_agent/` 和
`evals/archive/`；`pixie_qa/` 只保留本地 Pixie 状态及忽略的运行结果。

## 主要能力

- TXT、Markdown、PDF、DOCX 上传与解析。
- 档案问答在空候选时短路拒答；非空候选使用一次结构化 DeepSeek 决策，服务端校验 1-based 引用编号并映射为既有引用对象。
- BGE Embedding、向量入库、Top-K/Top-N 检索；C4-A/C4-B 历史阶段曾以固定 Chroma Top-30 做本地 Reranker 重排，公开响应仍最多返回 10 条；D6-B 当前正式固定集为有据 `7/8`、无据 `2/2`、隔离 `2/2`，达到已确认质量门。后续确认索引/RAG 真实 E2E 当前只受运行时 Embedding 路径覆盖错误阻塞。
- 无依据拒答，带文档名、页码、摘录和分数的结构化引用。
- Agent 会话所有权、LangGraph 多轮消息恢复、制度检索 Tool Calling。
- 工具参数/结果脱敏、耗时和稳定错误码审计。
- Alembic 管理 PostgreSQL Schema；当前本地 Compose 提供 PostgreSQL 与内部 Chroma 服务。云端部署策略和资源验收暂定，待 V1 完成后再评估。
- 智慧档案 V1 已具备 Parser 规则、虚构验收集、项目授权上下文、数据库/模型基础、项目 CRUD API、清单项 CRUD/派生状态 API、项目内上传/重复校验/容量控制、首次解析/受控失败记录/专用解析重试/四格式路由、P07 手工草稿和字段检查、P08 AI 建议/失败重试/安全 regenerate，以及 P09 确认/INDEX/取消确认、P10 清单关联/目录/审计、P11 正式检索、P12 证据问答和 P13 物理删除切片；真实确认—INDEX、Chroma/BGE canary、取消确认清理、单文档 P95 基线和 P13 故障恢复已通过。P14-D6-B 固定 12 题达到质量门，正式 Embedding 配置与 Collection 已切换并完成空库重建。相邻 Vue 工作台已接入认证、项目 CRUD 和 FR-031～FR-041 的 DTO、API、Store 与页面；FR-034/035～FR-041 的真实链路验收已经完成。
- 2026-09-12 已修复退出后重新登录时旧项目 URL 与新会话 Store 项目不一致的问题。登录、注册、
  有效深链接和退出登录四项 View 回归均通过；前端完整测试为 `122 passed`，类型检查和构建通过。

## 数据库表

PostgreSQL 当前包含十六张业务表：

`users`、`knowledge_bases`、`documents`、`chat_sessions`、
`chat_messages`、`agent_sessions`、`agent_tool_call_logs`、`projects`、
`archive_documents`、`parsed_snapshots`、`archive_field_values`、
`field_evidences`、`checklist_items`、`checklist_links`、`archive_operations`、
`archive_audit_logs`。

历史迁移 `0002_leave_domain` 曾创建三张请假表，前向迁移
`0004_remove_leave_domain` 会删除它们及 PostgreSQL Enum。新空库执行完整
迁移链后不会保留请假表。旧 SQLite 业务数据不会导入 PostgreSQL。

## 本地配置

要求 Python 3.11、Docker Compose，以及可用的本地 BGE 模型目录。

```powershell
Copy-Item .env.example .env
python -m pip install -r requirements-dev.txt
docker compose up -d postgres chroma
python -m alembic upgrade head
python run.py
```

如果本机 Docker 只运行 API 与 Chroma、业务数据库使用 `.env` 中已有的外部 PostgreSQL，
不要启动 Compose 的 `postgres` 服务。改用 `compose.external-postgres.yaml` 覆盖 API 的
`DATABASE_URL`，并以 `--no-deps` 防止 API 因依赖声明启动本地 PostgreSQL：

```powershell
docker compose -f compose.yaml -f compose.external-postgres.yaml up -d --build --no-deps api
```

API 启动会执行 Alembic 升级；连接外部 PostgreSQL 前应确认它属于本项目且允许升级。

首次启动本机 Chroma 后，先显式创建项目命名空间；此操作不会删除默认 Chroma namespace：

```powershell
C:\D\venvs\mrh\Scripts\python.exe scripts\provision_chroma_namespace.py
```

默认业务连接：

```text
postgresql+psycopg://mini_rag:mini_rag@localhost:5432/mini_rag
```

示例账号只用于本机开发。未来若决定部署，必须另行制定凭据、网络与资源方案；真实 `.env` 不得提交。
如果旧 `.env` 仍使用 `sqlite:///`，应用会拒绝启动；请根据 `.env.example`
手动更新，项目不会自动读取或覆盖你的真实配置文件。

完整容器启动：

```powershell
docker compose up --build
```

API 默认地址为 `http://127.0.0.1:8000`，Swagger 为 `/docs`。

## 核心接口

1. `POST /auth/register`、`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`
2. `POST/GET /knowledge-bases`
3. `POST/GET /knowledge-bases/{kb_id}/documents`
4. `POST /knowledge-bases/{kb_id}/documents/{document_id}/parse`
5. `POST /knowledge-bases/{kb_id}/retrieval-test`
6. `POST /chat-sessions` 与聊天消息/历史接口
7. `POST /agent-sessions`
8. `POST /agent-sessions/{session_id}/messages`
9. `GET /agent-sessions/{session_id}/messages`
10. `GET /agent-sessions/{session_id}/tool-calls`
11. `GET /projects/{project_id}/documents`、`GET /projects/{project_id}/archives`
12. `POST /projects/{project_id}/archive-retrieval`、`POST /projects/{project_id}/archive-questions`
13. `DELETE /projects/{project_id}/documents/{document_id}`、`GET /projects/{project_id}/audit-logs`

除注册、登录、刷新和健康检查外，受保护接口必须使用
`Authorization: Bearer <Access Token>`。`X-User-ID` 不再被接受。首次运行认证前，
请在本地 `.env` 手动配置 `AUTH_JWT_SECRET`，再运行 `python -m alembic upgrade head`；
项目不会读取、打印或覆盖你的真实 `.env`。项目没有 `/agent-sessions/{session_id}/decisions`。

### 账号密码登录流程

1. 在 Swagger 调用 `POST /auth/register` 创建新用户；`username` 是全局唯一的小写登录标识，
   `name` 仅作显示。
2. 调用 `POST /auth/login` 获得 Access Token（30 分钟）和 Refresh Token（7 天）。在 Swagger 的
   `Authorize` 中填写 Access Token 后，再调用知识库、项目、聊天或 Agent 等受保护接口。
3. Access Token 到期时，调用 `POST /auth/refresh` 并提交 Refresh Token；该接口只返回新的
   Access Token，不轮换 Refresh Token。
4. 调用 `POST /auth/logout` 撤销当前会话；之后该会话的 Access Token 与 Refresh Token 均不可继续使用。

旧 `users` 记录保留原有数据，但默认不能登录。仅在操作者已知目标用户 UUID、且具备本地数据库
访问权限时，才可运行下列本地命令初始化凭据；脚本交互读取两次密码，不提供 HTTP 后门，也不输出密码：

```powershell
C:\D\venvs\mrh\Scripts\python.exe scripts\initialize_user_password.py --user-id <用户UUID> --username <小写用户名>
```

## 测试

快速测试允许使用隔离内存 SQLite，但它不代表运行时业务数据库。真实
PostgreSQL 迁移测试必须显式提供空的专用测试库：

```powershell
pytest -q
$env:POSTGRES_TEST_URL='postgresql+psycopg://user:password@localhost:5432/empty_test_db'
pytest -q tests/test_migration_service_postgres.py
python -m compileall app tests migrations
docker compose config --quiet
```

`POSTGRES_TEST_URL` 测试会拒绝非空数据库，避免覆盖已有业务数据。

## 已知限制

- 已实现 Argon2 账号密码、JWT 与单会话注销；revision `0010_account_auth` 已在当前 PostgreSQL 开发库实际迁移并核对认证表结构，专用 `POSTGRES_TEST_URL` 自动化迁移测试仍未配置。
- Checkpoint SQLite 只适合单机运行。
- 同步解析不适合大文件和高并发。
- 没有 OCR、表格专用解析、混合检索、多 Agent 或任务队列。P14 当前本地 Reranker 对授权后的 Chroma Top-30 候选重排；D6-B 使用 base/768 与 Top-8 问答候选，正式 12 题为有据 `7/8`、无据 `2/2`、隔离 `2/2`。正式默认 Embedding 与 `archive_final_chunks` 已完成切换和空库重建。
- 智慧档案的字段模型、分类与缺失规则、人工确认点、评测集和数据库设计基线已确认；
 Parser 冻结规则、虚构验收资料、迁移、模型和项目授权上下文已创建并验证；
 项目 CRUD/模板复制、清单项 API、项目内上传、解析、P08 AI 建议、P09 确认/INDEX/取消确认、
 P10 清单关联/目录/审计、P11 正式检索、P12 证据问答和 P13 物理删除实现切片已完成。D6-B 已在
 D1 base/768 隔离环境通过固定问题集 DeepSeek 问答质量门，正式配置与 Collection 也已切换。P13 已完成一次真实跨 PostgreSQL、Chroma 与文件系统的故障删除—恢复验收：公开稳定错误、可见性阻断、文件保留、健康服务重试、三层归零、单条审计和临时范围清理均已核验。

截至 2026-09-08，D6-B 正式 12 题在隔离实验基线下达到质量门：有据回答并正确引用 `7/8`、无据拒答 `2/2`、隔离 `2/2`；候选池完整 `12/12`，Top-8 公开覆盖 `8/8`，检索 P95 `5801.62 ms`，问答调用 P95 `2046.44 ms`。后端完整回归为 `380 passed, 2 skipped, 105 warnings`，`compileall` 通过。Q-01 仍为有直接证据时的安全漏答；正式切换 bge-base/768 与重建正式 Collection 已在后续完成。

相邻前端目录为 `../mini-rag-milvus-vue`。它是 P14 的本地联调界面，不直接连接 PostgreSQL、Chroma、文件系统或 DeepSeek；所有业务请求仍经由 FastAPI 的 Bearer 认证与项目授权边界。

### FR-034/035 前端接入与真实本地 E2E（2026-09-10）

相邻 Vue 工作台已完成 FR-034/035：7 个前端文件共 55 个自动化测试通过，`npm run typecheck`、
`npm run test` 和 `npm run build` 均通过。真实链路为 `5173/api → 8000`：TXT 文档 AI 建议成功，
regenerate 成功并将版本从 `v2` 更新到 `v3`，人工保存后为 `v4` 且建议入口消失；此前失败的 PDF
重试成功并返回 `PDF_PAGE` 证据，刷新页面后草稿可以恢复。

本轮修复由真实模型输出中文字段键和格式不稳定触发；生产建议 Prompt 已明确七个英文字段键、枚举和值列契约，
并绑定 `response_format={"type":"json_object"}`。后端建议定向测试为 `11 passed`，完整回归为
`414 passed, 2 skipped`，`compileall` 与 `git diff --check` 通过。

该阶段记录的 Embedding 健康阻塞已在后续 FR-036 复验中解除。临时用户、项目、3 份文档、
原文件和快照已精确清理，数据库与文件均无残留。

### FR-036 确认、取消确认与重新确认（2026-09-10）

相邻 Vue 工作台已接入携带 `expected_version` 的确认、取消确认和重新确认。真实页面验证文档
`v9 → CONFIRMED v10 → PENDING_RECONFIRMATION v11 → CONFIRMED v12`；正式 Collection 的
对应 Final Chunk 数量依次为 `9 → 0 → 9`，刷新后 `v12` 状态仍保持。最终前端 7 个测试文件、
65 个测试通过，类型检查与正式构建通过；后端确认相关测试 10 个通过，全量回归为
`414 passed, 2 skipped, 121 warnings`，`compileall` 与差异检查通过。

当前 `/health` 为 `200`，API、database、Chroma 与 768 维 Embedding 全部正常。FR-036 验收当时
出现的 `RERANKER_UNAVAILABLE` 已通过运行时配置修正解除，随后企业扩展集真实复测达到有据
`12/12`、无据拒答 `4/4`、隔离拒答 `4/4` 和响应契约 `20/20`。临时文档、项目、账号、会话、
内部知识库与 Final Chunk 已全部核对为零。
FR-037 清单关联、FR-038 处理列表/正式档案目录、FR-039 带证据问答、FR-040 物理删除与 FR-041 脱敏审计均已完成前端接入；FR-039 真实合同日期页面场景、FR-040 真实代理删除链路和 FR-041 真实代理审计链路已通过。

### FR-040 文档物理删除（2026-09-11）

相邻 Vue 工作台已接入项目级文档删除、逐行删除状态、带文件名确认、失败重试和派生页面状态刷新。
前端全量 `110 passed`，类型检查与正式构建通过；后端全量 `415 passed, 2 skipped`，编译通过。
真实 `5173/api → 8000 → PostgreSQL/Chroma/DeepSeek` 验证了删除前正式检索与有据回答，以及删除后
文档、档案、关联、Final Chunk 和原文件归零、清单恢复缺失、检索为空、无据拒答和单条脱敏删除审计。
本轮浏览器控制 provider 不可用，因此不把代理链路写成新增 DOM 点击验收。

### FR-041 脱敏审计查询（2026-09-11）

相邻 Vue 工作台已接入项目审计 DTO、受控操作类型筛选、服务端分页、Pinia 项目/竞态隔离和独立审计面板。
后端审查同时修复了实际契约缺口：`AuditLogRead` 现在返回文档已声明的 `actor_id`，字段更新、解析重试成功和
建议重试成功会分别在业务事务中写入 `ARCHIVE_FIELD_UPDATED`、`PARSE_RETRIED`、`SUGGESTION_RETRIED`；
摘要不保存字段正文、解析正文或模型输入输出。

前端全量 `118 passed`，类型检查和正式构建通过；补齐 AC-FR-040-03 直接越权删除回归后，
后端全量 `417 passed, 2 skipped, 121 warnings`，
`compileall` 与差异检查通过。真实 `5173/api → 8000 → PostgreSQL` 验证了时间倒序分页、受控类型筛选、
操作人和时间、脱敏摘要及跨用户 `403`；临时账号、项目、内部知识库与认证会话最终均核对为 0。
浏览器控制 provider 仍连接失败，因此本节没有新增 DOM 点击证据，页面行为由 Vue 自动化测试证明。

本轮重新生成 16 份企业资料与 20 题，形成 88 个 Final Chunk；Top-30 候选池 `20/20` 完整，
有据目标证据进入 Top-8 为 `12/12`，检索 P95 为 `7993.01 ms`。第一次 DeepSeek 机械门
`19/20` 暴露 `GROUNDED-08` 的问题只问第一步而期望答案多要求第二步；Luna 按 TDD 仅修正该
企业扩展标注，保留证据原文且不修改 AV1-P02 或生产 RAG。复用同一捕获复测后机械门、响应契约
均为 `20/20`，人工逐题核对为有据 `12/12`、无据 `4/4`、隔离 `4/4`，问答 P95 为
`1328.36 ms`，两个 Pixie 运行的 Step 6 均通过。最终后端全量回归为
`415 passed, 2 skipped, 121 warnings`，`compileall` 与差异检查通过。
