# 入口与执行流程

## 如何运行

项目使用 Python 3.11、PostgreSQL、Chroma 与本地 BGE。完成本地 `.env` 配置后，在项目根目录执行：

```powershell
C:\D\venvs\mrh\Scripts\python.exe -m alembic upgrade head
C:\D\venvs\mrh\Scripts\python.exe run.py
```

`run.py` 使用 Uvicorn 加载 `app.main:app`；应用启动时也会执行受控 Alembic 升级。真实用户通过 Swagger 或 Vue 工作台向 `http://127.0.0.1:8000` 发出 Bearer 认证 HTTP 请求。

## 应用入口

- **文件**：`run.py` → `app/main.py`
- **类型**：FastAPI HTTP 服务
- **核心链路**：`app/routers/projects.py` → `retrieve_archive_chunks()` → PostgreSQL 正式文档闸门 → 本地 BGE → Chroma
- **本轮评估对象**：`POST /projects/{project_id}/archive-retrieval`，因为它同时覆盖身份授权、正式档案范围、向量候选、排序与可追溯引用。

## 面向用户的接口

- **`POST /auth/login`**
  - 输入：用户名和密码。
  - 输出：Access Token 与 Refresh Token；后续受保护接口使用 `Authorization: Bearer <Access Token>`。
- **`POST /projects`、`POST /projects/{project_id}/documents`、解析与确认接口**
  - 输入：项目资料、上传文件和受控确认操作。
  - 输出：项目、档案、解析/确认状态及脱敏审计；这些操作建立正式检索可见的档案集合。
- **`POST /projects/{project_id}/archive-retrieval`**
  - 输入：`{"query":"<1 至 2000 字中文问题>","top_k":1..10}`，以及 Bearer Access Token。
  - 输出：`items`、`requested_top_k`、`returned_count`。每个证据项包含原始 Chunk ID、文档 ID、文件名、定位范围、原文摘录和分数。
- **`POST /projects/{project_id}/archive-questions`**
  - 输入：问题和 Bearer Access Token。
  - 输出：基于正式检索证据的问答结果；当没有证据时拒答。P14 未通过前不得把其真实 LLM 质量写为通过。

## 执行流程

```text
HTTP 请求
→ Bearer Access Token 验证
→ 项目归属与内部 kb_id 上下文注入
→ PostgreSQL 查询 CONFIRMED 且未被删除阻断的 document_id
→ BGE 编码查询
→ Chroma 以 user_id + project_id + kb_id + document_id 过滤候选
→ 服务再次校验候选元数据、原始引用和范围
→ 返回按相关性排序的原始 Chunk 证据
```

阶段 C 接入后，最后一步之前会增加本地 Reranker：它只接收已通过范围校验的 Chroma Top-10 候选和查询正文，重排后仍返回同一份原始引用。

## 环境要求

| 变量 | 用途 | 是否需要 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 业务事实、授权和档案状态 | 是 |
| `CHROMA_HOST`、`CHROMA_PORT` | 本地 Chroma HTTP 服务 | 是 |
| `EMBEDDING_MODEL_PATH`、`EMBEDDING_DEVICE` | 本地 BGE 查询向量 | 是 |
| `AUTH_JWT_SECRET` | Bearer Access Token 验证 | 是 |
| `ARCHIVE_EMBEDDING_CONTEXT_MODE` | Final Chunk 的字段上下文表示 | 是，当前由本地 `.env` 决定 |
| 后续 Reranker 配置 | 本地模型路径、候选池和独立阈值 | 阶段 C 接入时新增；当前尚无运行时代码 |

真实固定集运行只能使用用户确认的虚构档案与受控本地服务；不得把单元测试的 Mock 数据或历史请假资料作为质量通过证据。
