# Mini RAG Handwrite

从空目录手写的企业知识库 RAG 与只读 Agent 后端练习项目。当前运行时代码使用
FastAPI、SQLModel、PostgreSQL、Alembic、LangChain/LangGraph、Chroma、本地 BGE 与
DeepSeek，通过 Swagger 演示上传、解析、检索、引用问答、Agent 会话和脱敏工具审计。
全部向量能力已迁移到 Chroma HTTP 客户端与单机服务配置。当前运行和开发基线是本地环境：
Chroma 的本机命名空间、范围过滤、精确删除，以及 Docker API、PostgreSQL、Chroma 与本地 BGE
健康连通均已验证。一次云主机 Chroma 独立实验仅保留为历史可行性证据；是否部署云端、采用何种
拓扑及资源规格均暂定，待 V1 全部功能完成后再决定，当前不能把项目描述为已完成云端部署。

下一阶段唯一业务方向是“智慧档案与企业文档智能”。需求、架构、数据库、API 与实施计划
基线已确认；AV1-P01 Parser 规则冻结、AV1-P02 虚构验收资料与 Ground Truth、AV1-P03
数据库/模型/公共授权基础、P04 前置模型分层调整、P04.1 项目 CRUD/模板复制 API、
P04.2 清单项 CRUD/派生状态 API，以及 P05 项目内上传/重复校验/容量控制已完成。归档表迁移
`0005`～`0008` 已在目标 PostgreSQL 空库实际前向迁移；P06 已完成首次解析、快照创建、受控失败记录、专用解析重试、四格式路由和重复解析快照保护，正式归档状态机、Chroma Final 索引和端到端验收尚未实现。当前不推进标书投标、标书解析生成或投标合规审查。原员工请假领域
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

- [需求说明](docs/requirements.md)
- [技术架构](docs/architecture.md)
- [数据库设计](docs/database-design.md)
- [API 设计](docs/api-design.md)
- [智慧档案实施计划](docs/archive-v1-implementation-plan.md)
- [Chroma 迁移决策](docs/chroma-migration-decision.md)
- [Parser 冻结规则](docs/archive-v1-parser-design.md)
- [既有 Agent 实施计划](docs/implementation-plan.md)
- [Agent 逻辑导览](docs/agent-guide.md)
- [Agent 演示步骤](docs/agent-demo.md)

以 `LEARNING_PLAN.md` 和对应实施计划为实时进度来源。`docs/review/`、`docs/stage/` 与
`pixie_qa/` 中的材料保留其产生时的评审、学习或评测上下文，不作为当前实现状态的来源。

## 主要能力

- TXT、Markdown、PDF、DOCX 上传与解析。
- BGE Embedding、向量入库、Top-K/Top-N 检索；Chroma cosine distance 的阈值尚待固定验收集标定。
- 无依据拒答，带文档名、页码、摘录和分数的结构化引用。
- Agent 会话所有权、LangGraph 多轮消息恢复、制度检索 Tool Calling。
- 工具参数/结果脱敏、耗时和稳定错误码审计。
- Alembic 管理 PostgreSQL Schema；当前本地 Compose 提供 PostgreSQL 与内部 Chroma 服务。云端部署策略和资源验收暂定，待 V1 完成后再评估。
- 智慧档案 V1 已具备 Parser 规则、虚构验收集、项目授权上下文、数据库/模型基础、项目 CRUD API、清单项 CRUD/派生状态 API、项目内上传/重复校验/容量控制，以及首次解析、受控失败记录、专用解析重试和四格式路由；后续归档业务 API 尚未实现。

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
- 没有 OCR、表格专用解析、混合检索、Rerank、多 Agent 或任务队列。
- 智慧档案的字段模型、分类与缺失规则、人工确认点、评测集和数据库设计基线已确认；
 Parser 冻结规则、虚构验收资料、迁移、模型和项目授权上下文已创建并验证；
  项目 CRUD/模板复制、清单项 API、项目内上传、首次解析、解析重试和四格式路由已实现；正式归档流程、Final Collection 和端到端质量验收尚未实现。
