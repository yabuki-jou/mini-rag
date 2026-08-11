# Entry Point & Execution Flow

> **历史评测输入快照：** 本文中的请假接口、`decisions` 接口与 SQLite 业务库描述
> 已不代表当前项目。它们仅用于解释历史评测材料，不得作为当前 API 或部署依据。
> 当前范围见 `AGENTS.md` 与 `docs/api-design.md`。

## How to run

项目使用 Python 3.11 和 FastAPI。在项目根目录先执行 Alembic 迁移，再运行服务器：

```powershell
C:\D\venvs\mrh\Scripts\python.exe -m alembic upgrade head
C:\D\venvs\mrh\Scripts\python.exe run.py
```

`run.py` 通过 Uvicorn 加载 `app.main:app`，应用 lifespan 会在接受请求前再次执行缺失迁移。真实用户通过 `http://127.0.0.1:8000/docs` 的 Swagger 或普通 HTTP 客户端操作。

## Entry point

- **File**: `run.py` → `app/main.py`
- **Type**: 同步 FastAPI HTTP 服务
- **Framework**: FastAPI、SQLModel、LangGraph
- **Agent application boundary**: `app/routers/agent.py` → `app/services/agent_service.py` → `AdminAgentRuntime`
- **Agent execution entry**: `AdminAgentRuntime.invoke()` 与 `AdminAgentRuntime.resume()`，但评测应从 HTTP Agent API 进入，不能绕过身份、会话和响应转换。

## User-facing endpoints / interface

- **`POST /users`**
  - Input: `{"name":"演示员工"}`
  - Output: 用户 UUID。后续受保护请求通过 `X-User-ID` 使用该身份。
- **`POST /knowledge-bases`**
  - Input: `{"name":"员工行政制度"}`
  - Output: 当前用户拥有的知识库 UUID。
- **`POST /agent-sessions`**
  - Input: `{"kb_id":"<UUID>"}` 和 `X-User-ID`
  - Output: Agent 会话 UUID、知识库 UUID、Graph thread ID 和时间。
- **`POST /agent-sessions/{session_id}/messages`**
  - Input: `{"message":"<5–2000 字符自然语言>"}` 和 `X-User-ID`
  - Output: `COMPLETED` 最终回答和可选制度引用，或 `REQUIRES_CONFIRMATION` 与唯一待确认请假草稿。
- **`POST /agent-sessions/{session_id}/decisions`**
  - Input: `{"action_id":"<UUID>","decision":"APPROVE|REJECT"}`
  - Output: 恢复同一 Graph thread 后的最终回答；错误动作或无待确认动作返回 409。
- **`GET /agent-sessions/{session_id}/messages`**
  - Output: 只包含用户与助手自然语言内容的历史，不返回 ToolMessage 和内部 State。
- **`GET /agent-sessions/{session_id}/tool-calls`**
  - Output: 工具名、状态、累计耗时、安全参数/结果摘要和稳定错误码。

## Execution flow

```text
HTTP 请求
→ X-User-ID 对应用户存在性校验
→ AgentSession 所有权校验
→ 应用服务从会话注入 user_id、kb_id、thread_id
→ Runtime 从独立 Checkpoint SQLite 读取同一线程
→ LangGraph 调用真实 DeepSeek 选择工具或追问
→ ToolNode 调用 Milvus/业务 SQLite 领域服务
→ 写工具生成草稿并 interrupt，决定接口使用 Command(resume=...)
→ 应用服务转换最终回答、引用、历史和脱敏工具日志
→ FastAPI 返回统一响应与 X-Request-ID
```

## Environment requirements

| Variable | Purpose | Required? | Default / current check |
| --- | --- | --- | --- |
| `DATABASE_URL` | 用户、知识库、员工、请假和 Agent 审计业务库 | 否 | `sqlite:///./data/handwrite.db` |
| `AGENT_CHECKPOINT_FILE` | LangGraph 多轮状态和 interrupt | 否 | `./data/agent_checkpoints.db` |
| `DEEPSEEK_API_KEY` | 真实模型调用 | 是 | 已配置，仅检查存在性，未输出值 |
| `DEEPSEEK_BASE_URL` | DeepSeek OpenAI 兼容地址 | 否 | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | Agent 使用的聊天模型 | 否 | `deepseek-chat` |
| `MILVUS_URI` | 制度 Chunk 向量检索 | 是（真实 RAG） | 当前配置 `http://localhost:19530` |
| `MILVUS_TOKEN` | Milvus 认证 | 视服务配置 | 使用 SecretStr，不进入评测输出 |
| `EMBEDDING_MODEL_PATH` | 本地中文 BGE Embedding | 是（真实 RAG） | 当前检查为不存在，不能宣称真实检索已可运行 |

真实 DeepSeek 已具备配置条件。由于当前 Embedding 路径不存在，A-09 可以执行真实模型的工具选择与 Agent 编排评测，并通过评测注入控制外部业务/检索数据；不能把这种结果表述为真实 BGE + Milvus 端到端检索通过。
