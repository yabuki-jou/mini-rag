# Chroma 向量存储迁移决策

## 1. 决策信息

| 项目 | 内容 |
|---|---|
| 决策日期 | 2026-08-07 |
| 最近更新 | 2026-09-11 |
| 状态 | C01 历史实验、C02 本地迁移、正式 bge-base/768 Collection 安全切换、P13 真实恢复、D6-B 固定质量门和企业扩展集已完成；云端完整栈与资源验证仍未进行 |
| 决策 | 全部向量能力由 Milvus 迁移至 Chroma 单机服务模式 |
| 影响范围 | 既有制度检索 Agent，以及后续智慧档案 V1 的正式检索 |
| 不变部分 | PostgreSQL 业务事实、文件系统原文件/快照、Checkpoint SQLite、本地 BGE 与 DeepSeek 的业务职责 |

## 2. 已确认边界

1. **全量切换**：既有制度检索和后续智慧档案均使用 Chroma；当前本地开发不再依赖运行时的
   Milvus、etcd 或 MinIO。只迁移智慧档案会保留旧 Agent 的 Milvus 依赖，不能完成向量后端
   统一。
2. **数据重建而非向量迁移**：不导出或转换历史 Milvus 向量。Chroma 实现和验证完成后，仅从
   仍保留的原文件与业务记录重新解析、切分和生成向量。删除 Milvus 数据必须是独立、显式、
   可恢复的运维动作，且只能在 Chroma 验证和重建完成后进行。
3. **本地网络边界**：当前本地开发中，Docker 内 API 经 Compose 内部网络访问 Chroma；为支持
   宿主机 Python 调试，Compose 将 Chroma 映射为 `127.0.0.1:8001`，但不得映射到局域网或公网。
   业务客户端不得直接连接 Chroma；所有检索和删除仍由 FastAPI 服务端注入 `user_id`、`kb_id`、
   `project_id` 和 `document_id` 范围。
4. **部署决策暂定**：是否部署到云端、服务器规格、是否使用外部 PostgreSQL、反向代理和网络
   拓扑，均在 V1 功能、向量重建和端到端验收完成后再评估。本文件中的 2 vCPU / 2 GB 记录只表示
   已做过的候选环境实验，不是当前部署约束或可运行结论。

## 3. 当前本地开发拓扑

```text
本机浏览器 / Swagger
  → FastAPI（`127.0.0.1:8000`）
  → PostgreSQL（本地 Compose 默认；也可用开发期外部连接）
  → Chroma（Compose 内部网络；宿主机调试仅 `127.0.0.1:8001`）
  → 本地文件卷（原文件与解析快照）

FastAPI → 本地 BGE（生成文档与查询向量）
FastAPI → DeepSeek（仅建议和带证据问答）
```

Chroma 使用服务端模式和持久化卷；既有制度检索使用一个逻辑 Collection，智慧档案仍使用独立
`archive_final_chunks` Collection。两者均用元数据过滤保持隔离，不依赖 Collection 名称代表
用户、项目或权限。

既有制度检索固定连接 `mini_rag_tenant / mini_rag_chroma`，并使用
`mini_rag_knowledge_chunks_v1` Collection。tenant/database 由
`scripts/provision_chroma_namespace.py` 显式、幂等创建；应用启动只连接既有命名空间，不执行
管理面创建。默认 `default_tenant/default_database` 和其中已有内容不属于本项目迁移的删除范围。

本机 C02 联调可以让 Docker 内的 FastAPI 通过 `compose.external-postgres.yaml` 使用开发期外部
PostgreSQL，同时在 Compose 内网访问本地 Chroma。这是已验证的本地开发组合，不构成任何云端
部署承诺；最终部署的数据库位置和网络边界待 V1 完成后决定。

### 3.1 本机项目命名空间验证（2026-08-07）

| 项目 | 实测结果 |
|---|---|
| Compose 项目与卷 | `mini-rag-handwrite` 的独立网络、`chroma_data` 卷和 Chroma 容器已启动；旧项目卷未删除 |
| 项目命名空间 | 显式脚本在新容器中创建 `mini_rag_tenant / mini_rag_chroma`，重复执行确认幂等 |
| 既有制度检索 Collection | `mini_rag_knowledge_chunks_v1` 已创建，初始不含业务 Chunk |
| 应用行为 | 以虚构 512 维 Chunk 验证写入、`user_id + kb_id` 范围过滤、`user_id + kb_id + document_id` 精确删除，并完成清理 |
| 自动化回归 | `103 passed, 1 skipped, 16 warnings`；跳过项与既有警告不代表 Chroma 行为失败 |
| Docker API 健康检查 | Docker API 通过外部 PostgreSQL 覆盖配置启动后，`/health` 返回 API、数据库、Chroma 与本地 BGE 均为 `ok`，Embedding 维度为 512 |

本节证明本机新命名空间、直接客户端行为，以及 Docker API、外部 PostgreSQL、Chroma 和本地
BGE 的健康连通；它不证明文档重建、旧制度检索或 DeepSeek 的业务链路完整回归，也不构成云端
部署或资源验收结论。

## 4. 必须保持的向量契约

- 每个 Chunk 继续保存稳定 `chunk_id`、正文、文件名、位置和摘录辅助定位，以及服务端生成的
  `user_id`、`kb_id`、`project_id`（适用时）和 `document_id` 元数据。
- 检索必须使用 Chroma `where` 组合范围过滤；正式档案检索还必须先由 PostgreSQL 得到
  `CONFIRMED` 且无可见性阻断的 `document_id` 集合。Chroma 中的孤立 Chunk 不能绕过
  PostgreSQL 的正式可见性闸门。
- 删除必须使用服务端构造的精确范围过滤，至少含 `user_id + kb_id + document_id`；档案删除
  额外包含 `project_id`。
- Chroma 的距离度量、返回分数方向和 `min_relevance_score` 不沿用 Milvus 的 COSINE 阈值。
  必须在 Chroma 实现后，使用 AV1-P02 固定验收集重新标定并冻结。

## 5. 历史可行性证据：AV1-C01 Chroma 独立实验

执行 C01 时 FastAPI 代码仍直接依赖 Milvus，因此该实验只能验证 Chroma 自身在候选主机的内部网络、
持久化、隔离、删除与基础资源占用；不能把它写成完整应用的内存验收。实验执行时使用了以下验证：

1. 使用目标云服务器或等价 2 vCPU / 2 GB 环境启动 Chroma 与持久化卷；Chroma 不发布宿主机端口。
2. 实测 Chroma 空闲内存、容器重启和健康状态。
3. 写入虚构 Chunk 后，验证 Chroma 重启后的持久化、精确删除、`user_id + kb_id` 隔离和
   服务端健康检查。

**后续部署评估**：若 V1 完成后决定部署，需要在选定环境实测 FastAPI、PostgreSQL、Chroma 与
Embedding 的空闲、模型加载、一次文档向量化和一次检索后的资源、OOM/重启与健康状态。届时再由
用户决定是否升级资源、迁出 PostgreSQL 或拆分 Embedding 服务；在此之前不把该验证作为开发阻塞项。

### 5.1 AV1-C01 实测结果（2026-08-07）

| 项目 | 实测结果 |
|---|---|
| 目标环境 | Ubuntu 5.15，2 vCPU，物理内存 1.6 GiB，磁盘可用约 32 GiB |
| 运行前准备 | 新建并持久化 2 GiB `/swapfile`；Docker 29.6.1、Compose v5.3.1 可用 |
| Chroma 镜像 | `chromadb/chroma:latest`，实测 digest `sha256:1e0b73a187a28757c572acba508c46f48c9e8b0acaf5c20e6d95cdedce1acdf6`；C02 必须改为固定版本或 digest |
| 网络边界 | `mini-rag-chroma-c01` 仅加入 `mini-rag-internal`，无宿主机端口映射；内网心跳成功 |
| 过滤与删除 | `chromadb` 1.5.9 临时客户端成功验证 `user_id + kb_id` 过滤和 `user_id + kb_id + document_id` 精确删除 |
| 持久化 | 容器重启后，保留的虚构 Chunk 可被读取 |
| Chroma 空闲资源 | `docker stats` 显示 21.58 MiB；当时主机可用内存约 1.0 GiB，Swap 未使用 |

**C01 结论**：Chroma 独立服务可作为该云主机的候选向量后端；这不证明完整项目、PostgreSQL
或本地 BGE 能在 2 GB 内存中稳定运行。

## 6. 当前开发顺序与延后事项

1. AV1-C01：历史候选云主机实验已记录；不删除历史 Milvus 数据。
2. AV1-C02：已替换配置、依赖、Compose、`vector_service`、检索/删除/健康检查及相关测试，并完成
   本机命名空间、范围过滤、精确删除和 Docker 健康验证。
3. AV1-P04.2、P05～P13 本地实现切片已完成；当前进入 P14 固定资料质量、端到端和文档验收。
4. 使用虚构资料完成 AV1-P11 固定问题集阈值/召回标定和 AV1-P12 真实 DeepSeek 引用质量评估；
   评估完成前不得把单文档 canary 当作正式质量结论。
5. V1 全部功能和端到端验收完成后，再决定是否部署云端；若决定部署，届时重新确定环境、网络、
   PostgreSQL 位置、资源门槛和完整栈验收方案。

AV1-P04.2 的项目清单 API 不调用向量库；P11/P12 现在已具备正式范围调用链，但质量阈值与真实模型
评估仍是 P14 工作。后续任何涉及向量写入或检索的任务依赖已完成的 C02 本地迁移契约，但不依赖
尚未决定的云端部署。
