# 制度 Agent 入口与执行流程

## 如何运行

生产入口由 `python run.py` 启动 FastAPI。客户端先使用账号密码取得 Bearer Access Token，再创建绑定本人知识库的 Agent 会话，最后向该会话发送自然语言消息。

本评测不连接真实制度库，也不运行真实 BGE 或 Chroma 检索；它通过生产代码已有的 `policy_retrieval_result` 输入观测点注入脱敏制度 Chunk。Agent Graph、Prompt、工具调用、应用服务、HTTP 路由和 DeepSeek 调用保持真实。

## 应用入口

- **启动文件**：`run.py` → `app/main.py`
- **类型**：FastAPI HTTP 服务
- **核心入口**：`POST /agent-sessions/{session_id}/messages`
- **执行链路**：Bearer 身份校验 → 会话所有权校验 → `send_agent_message()` → `AdminAgentRuntime.invoke()` → LangGraph → `search_company_policy` → DeepSeek 最终回答 → 响应与脱敏工具日志

## 用户接口

- **`POST /auth/register`、`POST /auth/login`**
  - 输入：本地账号凭据。
  - 输出：Access Token 和 Refresh Token。
- **`POST /agent-sessions`**
  - 输入：当前用户拥有的 `kb_id`。
  - 输出：固定绑定用户、知识库和 LangGraph thread 的会话。
- **`POST /agent-sessions/{session_id}/messages`**
  - 输入：`{"message": "用户自然语言问题"}`。
  - 输出：完成状态、自然语言回答、结构化来源和请求 ID。
- **`GET /agent-sessions/{session_id}/tool-calls`**
  - 输出：不包含身份字段、Token 或制度正文的工具调用摘要。

## 评测 Runnable

Runnable 使用临时业务 SQLite 和临时 LangGraph Checkpoint 文件，通过 FastAPI 依赖覆盖建立隔离 HTTP 环境。每条样本创建独立用户、知识库和 Agent 会话，并使用真实 DeepSeek 模型执行消息接口。外部制度检索结果只允许由 Pixie 的 `policy_retrieval_result` 输入观测点注入，不能替换 Agent 模型、Graph、Prompt 或最终回答。

## 环境要求

| 配置 | 作用 | 要求 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 调用真实 DeepSeek | 必需，但不得写入评测资料或结果摘要 |
| `DEEPSEEK_BASE_URL` | OpenAI 兼容接口地址 | 可使用项目默认值 |
| `DEEPSEEK_MODEL` | 真实评测模型 | 可使用项目当前配置 |
| `AUTH_JWT_SECRET` | HTTP Bearer Token 签发与验证 | 必需；评测环境使用进程内临时值 |
| Pixie 0.8.x | 注入输入、捕获轨迹并运行评审器 | 必需 |

评测不需要真实 PostgreSQL、Chroma 或本地 Embedding 模型，也不得把这种隔离评测表述为真实检索基础设施验收。
