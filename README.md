# Mini RAG Handwrite

从空目录手写的企业知识库 RAG 与只读 Agent 后端练习项目。当前运行时代码使用
FastAPI、SQLModel、PostgreSQL、Alembic、LangChain/LangGraph、Chroma、本地 BGE 与
DeepSeek。Swagger 用于 API 契约诊断；AV1-P14 将相邻 `mini-rag-milvus-vue` 作为本地工作台，演示上传、解析、检索、引用问答和脱敏审计。
全部向量能力已迁移到 Chroma HTTP 客户端与单机服务配置。当前运行和开发基线是本地环境：
Chroma 的本机命名空间、范围过滤、精确删除，以及 Docker API、PostgreSQL、Chroma 与本地 BGE
健康连通均已验证。一次云主机 Chroma 独立实验仅保留为历史可行性证据；是否部署云端、采用何种
拓扑及资源规格均暂定，待 V1 全部功能完成后再决定，当前不能把项目描述为已完成云端部署。

下一阶段唯一业务方向是“智慧档案与企业文档智能”。需求、架构、数据库、API 与实施计划
基线已确认；AV1-P01～P08、P09 确认—INDEX/取消确认、P10 清单关联/目录/审计、P11 正式检索、
P12 证据问答和 P13 物理删除的实现切片已完成。P09 已完成真实确认—INDEX 路由纵向链路、
真实 Chroma/BGE canary 与取消确认清理；P11 已完成真实单文档 canary（512 维 cosine、命中 1 条、
10 次请求 P95 约 125.97 ms）。P14 固定集已复验：当前三种纯稠密表示、阶段 C 本地 Reranker 及已授权的 Top-20 候选池均不能同时满足
“有据至少 7/8、无据拒答 2/2”。阶段 C.1 已确认一题标准证据未进入完整 Chroma Top-10，且两个无据题仍有高 Reranker
分数；C.3-A 已通过开发环境隐藏诊断接口观测完整 Top-20，确认 `GROUNDED-01/05/07` 未进入候选池；C.3-B 已在同一 Top-20 与本地 Reranker 下复验字段值表示，C.3-C 又复验固定查询表达 `档案证据检索问题：{query}`，两次结果均为 63/63 个 Chunk 获得上下文、有据 `7/8`、无据拒答 `0/2`、隔离 `2/2`，不存在可冻结的独立重排阈值。C.3-C 未更换模型、Chunk、候选池或评测集；新的查询模板、Chunk/窗口调整、换模型或修改评测集均需另行重新授权；DeepSeek 问答质量、P13 真实跨存储故障恢复和完整 Vue 工作台
端到端验收仍待完成。当前不推进标书投标、标书解析生成或投标合规审查。原员工请假领域
已经删除，不再提供余额、申请、人工确认或决定接口。

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
  `mini_rag_knowledge_chunks_v1`。
- 文件系统：上传原文件。
- SQLite：仅保存 LangGraph Checkpoint，不再作为业务数据库。

详细设计见：

- [需求说明](docs/design/需求说明.md)
- [技术架构](docs/design/技术架构.md)
- [数据库设计](docs/design/数据库设计.md)
- [API 设计](docs/design/接口设计.md)
- [智慧档案实施计划](docs/implementation/智慧档案V1实施计划.md)
- [P14 C.1 双排序诊断决策](docs/review/P14-rag检索质量改进/P14-C1-双排序诊断决策.md)
- [检索质量问题分析与改进策略](docs/review/P14-rag检索质量改进/检索质量问题分析与改进策略.md)
- [P14-C2 检索质量优化方案](docs/review/P14-rag检索质量改进/P14-C2-检索质量优化方案.md)
- [Chroma 迁移决策](docs/design/Chroma迁移决策.md)
- [Parser 冻结规则](docs/design/智慧档案V1解析器设计.md)
- [既有 Agent 实施计划](docs/implementation/既有检索与智能体实施计划.md)
- [Agent 逻辑导览](docs/implementation/智能体逻辑导览.md)
- [Agent 演示步骤](docs/implementation/智能体演示步骤.md)

以 `LEARNING_PLAN.md` 和对应实施计划为实时进度来源；P14 检索质量的当前任务以
`docs/review/P14-rag检索质量改进/P14-C2-检索质量优化方案.md` 为唯一执行台账，证据与决策分别追溯到同目录的分析台账和 ADR。
其余 `docs/review/`、`docs/stage/` 与 `pixie_qa/` 材料保留其产生时的评审、学习或评测上下文，
不作为当前实现状态的来源。

## 主要能力

- TXT、Markdown、PDF、DOCX 上传与解析。
- BGE Embedding、向量入库、Top-K/Top-N 检索；当前以固定 Chroma Top-20 做本地 Reranker 重排，公开响应仍最多返回 10 条；真实固定集未找到满足门槛的重排阈值。
- 无依据拒答，带文档名、页码、摘录和分数的结构化引用。
- Agent 会话所有权、LangGraph 多轮消息恢复、制度检索 Tool Calling。
- 工具参数/结果脱敏、耗时和稳定错误码审计。
- Alembic 管理 PostgreSQL Schema；当前本地 Compose 提供 PostgreSQL 与内部 Chroma 服务。云端部署策略和资源验收暂定，待 V1 完成后再评估。
- 智慧档案 V1 已具备 Parser 规则、虚构验收集、项目授权上下文、数据库/模型基础、项目 CRUD API、清单项 CRUD/派生状态 API、项目内上传/重复校验/容量控制、首次解析/受控失败记录/专用解析重试/四格式路由、P07 手工草稿和字段检查、P08 AI 建议/失败重试/安全 regenerate，以及 P09 确认/INDEX/取消确认、P10 清单关联/目录/审计、P11 正式检索、P12 证据问答和 P13 物理删除切片；真实确认—INDEX、Chroma/BGE canary、取消确认清理和单文档 P95 基线已通过。P14 阶段 C.2 已将 Chroma 候选池扩大为 Top-20，C.3-A 又通过开发环境隐藏诊断接口观测了完整 Top-20，C.3-B 完成字段值表示复验，C.3-C 完成固定查询表达复验；固定集仍为有据 `7/8`、无据拒答 `0/2`、隔离 `2/2`，其中 `GROUNDED-01/05/07` 未进入完整 Top-20，两个无据题仍有高 Reranker 分数，阈值仍未冻结。相邻 Vue 工作台已接入认证、项目 CRUD、FR-031 清单 CRUD，以及 FR-032/033 项目级上传、处理列表、首次解析和专用解析重试的 DTO/API、Store 与页面，并通过正式 `5173/api → 8000` 代理 canary；FR-034～FR-041 前端接入、真实 DeepSeek、P13 真实故障恢复和完整端到端验收仍待 P14。

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
- 没有 OCR、表格专用解析、混合检索、多 Agent 或任务队列。P14 当前唯一例外是本地 Reranker 对授权后的 Chroma Top-20 候选重排；真实固定集未找到可通过门槛的重排阈值，仍未通过质量验收。
- 智慧档案的字段模型、分类与缺失规则、人工确认点、评测集和数据库设计基线已确认；
 Parser 冻结规则、虚构验收资料、迁移、模型和项目授权上下文已创建并验证；
 项目 CRUD/模板复制、清单项 API、项目内上传、解析、P08 AI 建议、P09 确认/INDEX/取消确认、
 P10 清单关联/目录/审计、P11 正式检索、P12 证据问答和 P13 物理删除实现切片已完成。当前仍未
 验收固定问题集阈值/召回质量、DeepSeek 问答质量、P13 真实跨存储故障恢复和完整 Vue 工作台端到端演示。

相邻前端目录为 `../mini-rag-milvus-vue`。它是 P14 的本地联调界面，不直接连接 PostgreSQL、Chroma、文件系统或 DeepSeek；所有业务请求仍经由 FastAPI 的 Bearer 认证与项目授权边界。
