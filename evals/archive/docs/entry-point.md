# Entry Point & Execution Flow

## How to run

在 Python 3.11 环境中执行 `python run.py` 启动本地 FastAPI 服务。该命令通过
Uvicorn 导入 `app.main:app`，绑定回环地址 `127.0.0.1:8000`；运行时配置来自项目
根目录的 `.env`，但评测过程不读取、输出或提交其中的值。

## Entry point

- **File**: `run.py` 和 `app/main.py`
- **Type**: FastAPI HTTP 服务
- **Framework**: FastAPI / Uvicorn

## User-facing endpoints / interface

- **Endpoint**: `POST /auth/register`、`POST /auth/login`
  - **Input format**: 注册或登录请求体；登录成功后客户端取得 Access Token。
  - **Output format**: 认证响应；后续受保护请求使用 `Authorization: Bearer <access-token>`。
- **Endpoint**: `POST /projects/{project_id}/archive-retrieval`
  - **Input format**: Bearer 身份、项目 ID，以及 `{"query": "...", "top_k": ...}` 请求体。
  - **Output format**: 当前用户、当前项目且已确认档案产生的候选证据和引用元数据。
- **Endpoint**: `POST /projects/{project_id}/archive-questions`
  - **Input format**: Bearer 身份、项目 ID，以及 `{"question": "..."}` 请求体（1–2,000 字符）。
  - **Output format**: `answer_status`、文本 `answer` 和正式证据 `citations`；无依据时返回
    `REFUSED_NO_EVIDENCE` 和空引用，存在证据时才调用 DeepSeek。
- **Endpoint**: `DELETE /projects/{project_id}/documents/{document_id}`
  - **Input format**: Bearer 身份、项目 ID 和文档 ID。
  - **Output format**: 成功时无正文；外部存储未完成清理时返回稳定失败。

## Execution flow for archive question evaluation

`POST /projects/{project_id}/archive-questions` → Bearer 身份和项目上下文依赖 →
`answer_archive_question()` → `retrieve_archive_chunks()` 使用服务端注入的 user/project/kb
范围查询 Chroma → 无命中直接拒答，或以编号证据构造提示 → `get_chat_model().invoke()`
调用 DeepSeek → 返回同一检索结果生成的 citations。

## Environment requirements

| Variable / configuration | Purpose | Required? | Note |
| --- | --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 业务库 | 是 | 必须是 `postgresql+psycopg://` URL。 |
| `CHROMA_HOST`、`CHROMA_PORT` | Chroma HTTP 服务 | 是 | 本机调试使用回环地址。 |
| `EMBEDDING_MODEL_PATH` | 本地 BGE 模型 | 是（检索） | 用于生成查询向量。 |
| `DEEPSEEK_API_KEY` | DeepSeek 调用凭据 | 是（真实 AI 评估） | 不记录凭据或请求内容。 |
| `AUTH_JWT_SECRET` | Access Token 验证 | 是（受保护接口） | 缺失时认证接口安全拒绝服务。 |
| 检索 Top-K/Top-N/距离阈值 | 检索标定 | 是（质量验收） | 阈值仅能由固定验收集标定后冻结。 |

## D5 Runnable 入口

- **File**: `evals/archive/runnable.py`
- **Class**: `ArchiveQuestionRunnable`
- **Typed input**: `ArchiveQuestionArgs`，仅包含 `question` 字段。
- **Production call**: `answer_archive_question(user_id, project_id, kb_id, question, session)`。

Runnable 使用三个固定的虚构 UUID 表示已验证上下文，不能由数据集覆盖；同步生产
服务放在 `asyncio.to_thread()` 中，并由 `asyncio.Semaphore(1)` 串行化。服务中的
`eval_wrap(name="archive_question_retrieval", purpose="input")` 会被 Pixie 替换为
数据集提供的 `ArchiveRetrievalResponse`，使 smoke 可以控制候选而无需重复构造
PostgreSQL/Chroma 数据。主 Agent 已执行真实 Pixie smoke；有候选时生产服务仍进入
DeepSeek 分支，最终结果和 Agent evaluator 评审保存于对应 Pixie 结果目录。
