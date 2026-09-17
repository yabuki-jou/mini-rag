# 项目档案助手入口与执行链

## 启动方式

本应用是 FastAPI 服务，正常本地入口为 `C:\D\venvs\mrh\Scripts\python.exe run.py`。启动流程会先执行
Alembic 升级，再启动 HTTP 服务，因此真实评测不能在未隔离数据库时随意启动。Pixie Runnable 应通过
FastAPI ASGI 入口发送真实 Bearer HTTP 请求，使用临时业务库和临时 Checkpoint；Archive Graph、工具路由、
DeepSeek 调用、应用服务和响应转换必须使用生产代码，不能替换模型。

## 入口

- **应用文件**：`app/main.py`
- **运行文件**：`run.py`
- **类型**：FastAPI 模块化单体 HTTP 服务
- **框架**：FastAPI、SQLModel、LangGraph

## 用户可见接口

1. `POST /auth/register` 与 `POST /auth/login`
   - 输入：用户名、显示名和密码；登录输入用户名与密码。
   - 输出：注册用户与 Bearer Token。
   - 评测用途：证明后续项目和会话入口走真实认证依赖，而不是直接调用内部函数。
2. `POST /projects`
   - 输入：项目名称、可选说明和模板开关。
   - 输出：项目公开信息；内部知识库由服务端创建和绑定。
3. `POST /projects/{project_id}/agent-sessions`
   - 输入：严格空 JSON 对象。
   - 输出：`id/project_id/created_at/updated_at`；创建阶段不调用模型或 Checkpoint。
4. `POST /projects/{project_id}/agent-sessions/{session_id}/messages`
   - 输入：`{"message": "1～2000 码点的用户消息"}`。
   - 输出：`session_id/answer_status/answer/citations/request_id`；引用仅含文件、位置与摘录。
5. `GET .../messages` 与 `GET .../tool-calls`
   - 输出：完整用户/助手轮次和脱敏工具审计，用于核对两轮会话及重试去重。

## 消息执行流

```text
Bearer Token
→ 当前用户与 ProjectContext 所有权校验
→ ARCHIVE 五要素会话查找
→ 构造绑定 user_id/project_id/kb_id 的 Archive Runtime
→ DeepSeek 决定调用目录工具、证据工具或不调用
→ 工具只在服务端范围内读取正式档案
→ 目录结果确定性投影，或 Top-8 候选进入 D5/D6 判定
→ 可信 AIMessage 写入共享 SQLite Checkpoint
→ 脱敏工具摘要写入 PostgreSQL
→ ArchiveAgentResponse 返回给客户端
```

## 环境要求

| 配置 | 用途 | 真实评测是否需要 | 默认/边界 |
|---|---|---|---|
| `DATABASE_URL` | PostgreSQL 业务实体和会话审计 | 真实外部链路需要；HTTP Runnable 可覆盖为临时隔离库 | 必须是 PostgreSQL URL |
| `AUTH_JWT_SECRET` | Bearer Token 签发与验证 | 需要；Runnable 使用仅存活于评测进程的临时值 | 空值时认证安全拒绝 |
| `DEEPSEEK_API_KEY` | 真实工具选择和回答/判定模型 | 必须 | 无安全默认值 |
| `DEEPSEEK_BASE_URL` | OpenAI 兼容接口地址 | 必须可达 | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | 生产聊天模型 | 必须 | `deepseek-chat` |
| `DEEPSEEK_REQUEST_TIMEOUT_SECONDS` | 单次模型调用超时 | 必须 | 30 秒 |
| `CHROMA_HOST/PORT/TENANT/DATABASE` | 正式档案向量检索 | 真实 Chroma 链路需要 | 本地服务默认端口 8001 |
| `CHROMA_FINAL_COLLECTION` | 已确认档案 Final Chunk 集合 | 真实 Chroma 链路需要 | `archive_final_chunks` |
| `EMBEDDING_MODEL_PATH` | 本地 BGE 查询向量 | 真实检索需要 | 项目配置路径 |
| `ARCHIVE_RERANKER_MODEL_PATH` | 本地候选重排 | 真实检索需要 | 项目配置路径 |
| Agent Checkpoint 路径 | POLICY/ARCHIVE 共享严格 SQLite 状态 | 必须；Runnable 使用临时文件 | 由应用配置解析 |

真实评测不得读取、记录或输出上述密钥、数据库 URL、Token 或业务数据。
