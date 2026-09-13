# 制度 Agent Collection 维度迁移实施计划

> 状态：待执行授权  
> 日期：2026-09-13  
> 范围：既有制度知识库 `mini_rag_knowledge_chunks_v1`

## 一、问题与只读证据

当前智慧档案与旧制度 RAG 共用 `settings.embedding_dimension=768` 和同一个本地 BGE
Embedding 实例，但旧制度 Collection 是历史 512 维 Collection。制度 Agent 查询因此在
生成 768 维查询向量后被 Chroma 拒绝，并由服务映射为 `VECTOR_UNAVAILABLE`/HTTP 503。

2026-09-13 已完成不读取正文和向量的只读预检：

- Collection：`mini_rag_knowledge_chunks_v1`
- 距离度量：cosine
- 条目数：0
- 512 维查询：接受
- 768 维查询：`InvalidArgumentError`，Collection 期望 512 维

因此当前可以采用“空 Collection 原名重建”，不需要搬运 Chroma 业务向量。执行前仍要
用聚合计数确认 PostgreSQL 不存在 `READY` 或 `chunk_count > 0` 的旧制度文档；若存在，
必须停止本方案，另行制定文档重处理方案，不能把空 Chroma 当作数据一致。

## 二、目标与非目标

### 目标

1. 保持 Collection 名 `mini_rag_knowledge_chunks_v1`、cosine 度量和 HTTP API 不变。
2. 将空 Collection 的冻结向量维度从 512 安全切换到当前配置的 768。
3. 通过 768 维 canary 写入、范围查询和精确删除证明新 Collection 可用，最终恢复为空。
4. 使用临时虚构制度文档完成一次真实上传、处理、Agent 检索问答和跨存储清理。

### 非目标

- 不修改 Embedding 模型、Prompt、Agent Graph、Router、Schema、迁移或业务错误码。
- 不读取、迁移或输出制度正文、用户信息、Token、向量内容或其他业务数据。
- 不处理智慧档案 `archive_final_chunks` Collection。
- 不把一次 canary 或单题问答表述为完整制度检索质量验收。

## 三、代码与目录改动

```text
scripts/
└── policy_collection_embedding_rebuild.py

tests/
├── scripts/
│   └── test_policy_collection_embedding_rebuild.py
└── evals/
    └── policy_agent/
        └── test_live_retrieval_canary.py

evals/
└── policy_agent/
    ├── live_retrieval_canary.py
    └── docs/
        └── live-retrieval-canary.md
```

同步更新：

- `docs/stage/handoff.md`
- `LEARNING_PLAN.md`
- `README.md`
- `docs/decisions.md`（仅在真实迁移完成后记录最终状态；不提前写成已完成）

不修改 `app/` 运行时代码。现有 `app/services/rag/vector_store.py`、
`app/services/rag/embeddings.py` 和 `app/services/infrastructure/chroma.py` 只作为公开能力调用方。

## 四、安全重建脚本

`scripts/policy_collection_embedding_rebuild.py` 提供两个显式阶段：

### 1. 默认只读预检

- 精确校验目标名必须等于 `settings.chroma_collection`，并拒绝档案正式/实验 Collection。
- 校验当前配置维度必须为 768，实际模型探针也必须返回 768。
- 只读取 Collection 名、配置和条目数，不读取 documents、metadatas 或 embeddings。
- 以聚合查询检查旧制度 `Document`：`READY` 数和 `chunk_count > 0` 数必须均为 0；
  只输出计数，不输出记录、文件名或 ID。
- 验证现状确为“512 接受、768 拒绝”；如果 768 已接受则报告幂等完成，不删除 Collection。
- 任一前置条件不满足即停止，禁止自动转为有数据迁移。

### 2. 显式 `--apply` 重建

- 要求 API/写入入口已停止，并再次执行全部预检，防止检查后状态变化。
- 删除的唯一目标必须是 `settings.chroma_collection`。
- 原名创建 cosine Collection，写入一条虚构 768 维 canary。
- 按 canary 的虚构 `user_id + kb_id + document_id` 查询，验证范围过滤和精确命中。
- 精确删除 canary，并确认 Collection 条目数恢复为 0。
- 清理 `get_chunk_collection()` 缓存，确保后续进程不复用旧 Collection 对象。

### 3. 失败恢复

旧 Collection 当前为空，因此恢复目标是“同名、cosine、空、仍为 512 维”：

- 新 Collection 创建、canary 写入、查询或删除任一步失败时，删除不完整的新 Collection。
- 重建同名 cosine Collection，写入并删除一条 512 维恢复 canary，使维度恢复为 512。
- 再确认条目数为 0；恢复失败时返回非零退出码并保留明确的人工处理提示。
- 所有输出仅包含阶段、Collection 名、维度、计数和稳定错误摘要。

## 五、TDD 顺序

### RED

先新增并实际运行以下失败测试：

1. 非精确目标名、档案 Collection 或非 768 配置必须拒绝。
2. Collection 非空必须停止且不得调用删除。
3. PostgreSQL 聚合计数显示存在待重建文档时必须停止。
4. 已是 768 维时必须幂等退出且不得删除。
5. 空 512 维 Collection 应进入重建路径。
6. 768 canary 查询必须使用服务端生成的三字段范围过滤。
7. 新建、写入、查询或清理失败时必须执行 512 维空库恢复。
8. 成功后 768 查询接受、512 查询拒绝、canary 为 0 条。

### GREEN

只实现使上述测试通过的最小脚本和 canary 入口，不改生产服务行为。

### REFACTOR

仅整理重复校验、中文文档字符串和稳定结果结构；相关测试持续通过后才进行。

## 六、真实执行与验收顺序

真实执行需要单独授权以下外部状态操作：停止/重启本地 API、删除并重建空 Chroma
Collection、写入和删除 canary，以及创建并清理临时 PostgreSQL/文件/Chroma 验收数据。

1. 提交当前尚未提交的制度评测迁移，保证本任务独立。
2. 完成脚本与测试的 RED/GREEN/REFACTOR，不连接真实存储执行 `--apply`。
3. 停止所有可能向旧制度 Collection 写入的 API/进程。
4. 运行只读预检；目标必须仍为 cosine、0 条、512 维，数据库相关聚合计数必须为 0。
5. 显式运行 `--apply`。
6. 复核：Collection 为 cosine、0 条、768 接受、512 拒绝、canary 不存在。
7. 重启 API，确认 `/health` 为 200 且 Embedding 实际为 768。
8. 使用全新虚构用户、知识库和单份制度 TXT 完成上传、处理、Agent 会话、真实 DeepSeek
   问答和引用检查；该资料不得来自项目测试 fixture 或真实业务。
9. 精确清理临时用户范围内的 PostgreSQL、原文件、Chroma Chunk 和 Checkpoint，并逐层复核为 0。
10. 运行制度评测相关测试、全量 `pytest`、`compileall` 和 `git diff --check`，更新当前状态。

## 七、验收标准

- 迁移前后 Collection 名和 cosine 度量不变，最终维度为 768、条目数为 0。
- 任何非空 Collection 或数据库待重建文档都会阻断原名重建。
- canary 只在虚构三字段范围内可见，删除后没有残留。
- 真实临时制度文档能够通过现有 HTTP 链路处理并由 Agent 返回对应引用。
- 临时 PostgreSQL、文件、Chroma 和 Checkpoint 数据全部清理。
- 不修改生产 API、Schema、Prompt、业务规则或智慧档案 Collection。
- 自动化回归与一次单题真实链路的证据边界分别记录。

## 八、停止条件

出现以下任一情况立即停止，不继续删除或重试：

- Collection 条目数不为 0。
- PostgreSQL 存在 `READY` 或 `chunk_count > 0` 的旧制度文档。
- API/写入进程无法确认已停止。
- 当前模型输出不是 768 维。
- 目标 Collection 名与配置不一致，或误指向智慧档案 Collection。
- 512 恢复路径失败。
