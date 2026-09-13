# P14-D1：Embedding 模型替换可行性设计

**角色**：模型替换设计与隔离实验记录；不切换正式配置
**状态**：v2（前置验证与 bge-base 隔离实验已完成，质量门槛未通过）
**最后更新**：2026-09-02

---

## 1. 当前基线确认（不可变条件）

| 项目 | 当前值 |
|---|---|
| Embedding 模型 | BAAI/bge-small-zh-v1.5 |
| 官方输出维度 | **512**（官方模型卡 C-MTEB 表格确认） |
| 代码配置维度 | **512**（`config.py:95` 默认值，**与官方一致**） |
| Reranker | BAAI/bge-reranker-base（不变） |
| Chroma 现有 Final Collection | `archive_final_chunks`（**512 维**，Chroma 在首次写入时锁定维度，后续维度不匹配抛 `InvalidDimensionException`） |
| 向量距离度量 | cosine |
| Final Chunk / 定位 / 引用 / 查询表达 | 全部不变 |
| AV1-P02 固定集 | 12 题不变 |
| C4-A / C4-B 结果 | 公开覆盖召回 7/8，严格诊断 5/8，无据拒答 0/2 |
| D1 bge-base 实验结果 | 公开覆盖召回 8/8，严格候选池命中 6/8，无据拒答 0/2 |
| C4-C 阈值标定 | 阻塞 |
| P12 DeepSeek QA | 阻塞 |
| Vue 完整 E2E | 延期 |

**注意事项**：
- Chroma 每个 Collection 在首次写入时锁定向量维度，后续写入不同维度的向量会抛 `InvalidDimensionException`。因此换模型必须新建 Collection（不能在现有 Collection 上混入不同维度向量）。
- `get_final_collection()` 通过 `settings.chroma_final_collection` 读取 Collection 名；本次以进程级环境变量切换，未修改正式 `.env`。
- `get_embeddings()` 和 `get_final_collection()` 均有进程内缓存；模型/Collection 切换后重启 API 才能确保实例和配置一致。
- 本机 Chroma 1.5.9 的 512→768 维写入校验实际抛出 `InvalidArgumentError`；结论仍是不同维度不能混入同一 Collection。

---

## 2. 候选模型评估

### 2.1 模型参数对比

| 维度 | 当前 | 候选 1 | 候选 2 | 候选 3 |
|---|---|---|---|---|
| 模型名 | **bge-small-zh-v1.5** | **bge-base-zh-v1.5** | **bge-large-zh-v1.5** | **bge-m3** |
| 输出维度 | 512 | **768** | **1024** | **1024** |
| 最大 tokens | 512 | 512 | 512 | **8192** |
| 参数量 | ~30M | ~102M | ~326M | ~568M |
| 模型文件大小 | ~96 MB | ~409 MB | ~1.3 GB | ~2.2 GB |
| 语言 | 中文 | 中文 | 中文 | 100+ 语言（含中文） |
| C-MTEB 平均分 | 57.82 | 63.13 | 64.53 | N/A（多语言基准，非 C-MTEB） |
| HuggingFaceEmbeddings 可加载 | ✅ | ✅ | ✅ | ✅（SentenceTransformer 路径可尝试；dense-only 场景无需 FlagEmbedding） |
| 是否需要 query instruction | 可选（v1.5 系列） | 可选 | 可选 | 不需要 |
| CPU 内存占用 | ~0.8 GB | **待实测** | **待实测** | **待实测** |
| 单条 Embedding 延迟 | ~3 ms | **待实测** | **待实测** | **待实测** |
| 模型加载耗时 | ~1-2 s | **待实测** | **待实测** | **待实测** |

> **C-MTEB 分数说明**：可作为候选筛选依据，但不能直接推出本项目固定 12 题一定会改善。效果必须以实验实测为准。

### 2.2 候选模型重点核对

**bge-base-zh-v1.5（768 维）**

- 与当前 small 同属 v1.5 中文系列，`HuggingFaceEmbeddings` 接入方式零差异
- 向量存储膨胀 1.5×（512→768）
- C-MTEB 63.13 比当前 small 高 5.31 分（+9.2%）
- **推荐优先尝试**——改动面最小，风险最低

**bge-large-zh-v1.5（1024 维）**

- 同样是 v1.5 中文系列，`HuggingFaceEmbeddings` 接入无差异
- 向量存储膨胀 2×（512→1024）
- C-MTEB 64.53 比当前 small 高 6.71 分（+11.6%）
- 资源压力高于 base，需确认本机 CPU 内存可用空间后再试

**bge-m3（1024 维）**

- 架构与 zh-v1.5 系列不同
- **dense-only 场景**：`SentenceTransformer("BAAI/bge-m3")` 或 `HuggingFaceEmbeddings` 路径**可以尝试加载**，需在当前依赖版本中单独验证兼容性
- **dense + sparse + ColBERT 场景**：才需要引入 `FlagEmbedding` 包和 `BGEM3FlagModel` API
- 最大 tokens 8192 对长文本可能有帮助，但当前 Final Chunk 被 Parser 规范限制在较短范围内
- 100+ 语言支持对当前纯中文场景是 overhead
- **不推荐作为第一选择**——先在 zh-v1.5 系列内完成变量隔离实验

### 2.3 候选模型 API 兼容性详解

当前代码用 `langchain_huggingface.HuggingFaceEmbeddings` 加载：

```python
# app/services/infrastructure/ai_models.py
return HuggingFaceEmbeddings(
    model_name=str(embedding_path),
    model_kwargs={"device": settings.embedding_device},
    encode_kwargs={"normalize_embeddings": True},
)
```

| 模型 | HuggingFaceEmbeddings 能否用 | 需要的代码改动 |
|---|---|---|
| bge-base-zh-v1.5 | ✅ 直接可用 | 改 `settings.embedding_model_path` + `settings.embedding_dimension = 768` |
| bge-large-zh-v1.5 | ✅ 直接可用 | 改 `settings.embedding_model_path` + `settings.embedding_dimension = 1024` |
| bge-m3（仅 dense） | ✅ 可尝试加载（需在当前依赖版本中验证） | 改 `settings.embedding_model_path` + `settings.embedding_dimension = 1024`；如需 sparse/ColBERT 能力再评估 FlagEmbedding |

---

## 3. 隔离实验方案

### 3.1 变量范围

| 变量 | 值 | 说明 |
|---|---|---|
| **唯一变更** | Embedding 模型 + 向量维度 + 新 Collection | 其他全部固定 |
| Reranker | 保持 `BAAI/bge-reranker-base` | 不变 |
| Final Chunk | 不变 | 同一份 Final Chunk（从 PostgreSQL 读取快照重建向量） |
| 查询表达 | 保持 C4-A 基线 | Embedding/Reranker 查询都不变 |
| Chroma 距离度量 | cosine | 不变 |
| AV1-P02 固定集 | 12 题 | 不变 |
| Reranker candidate_k | 30 | 不变 |
| ArchiveOperation 状态 | 同一份文档 | 不改业务数据 |
| 旧 `archive_final_chunks` Collection | **只读保留** | 不修改、不删除、不写入 |

### 3.2 代码变更点

| 文件 | 类型 | 说明 |
|---|---|---|
| `app/core/config.py` | 配置值 | 改 `embedding_model_path` + `embedding_dimension`；新增或临时切换 Collection 名的配置路径**待实测** |
| Collection 切换逻辑 | **需新增或验证** | `get_final_collection()` 如何显式切换到 `archive_final_chunks_v2`？通过 config flag？还是独立 config 项？**待实测** |
| 模型服务缓存 | **需验证** | 换模型后 `get_embeddings()` / `HuggingFaceEmbeddings` 实例是否需要重启或清缓存？**待实测** |
| 重建脚本 | **需新建** | 读取既有快照重建向量，不产生新的 ArchiveOperation 业务记录 |
| 写入安全 | **需验证** | 如何防止新模型误写入旧 Collection？Collection 名硬编码检查？写入前维度校验？**待实测** |
| 健康检查 / 维度校验 | 自动跟随 | `health.py` / `app/services/rag/embeddings.py` / `app/services/archive/final_chunks.py` 用 `settings.embedding_dimension`，配置变了自动跟随 |

> **注意**："0 行代码改动"不成立。即使选 base-zh-v1.5，除了配置值变更，还需要确认 Collection 切换路径、模型缓存行为、重建脚本的业务隔离设计。这些都需要先做 TDD 测试再实现。

### 3.3 Chroma Collection 隔离设计

| 项目 | 旧（当前） | 新（实验） |
|---|---|---|
| Collection 名称 | `archive_final_chunks` | `archive_final_chunks_v2` |
| 维度 | 512 | 768 或 1024（视候选模型而定） |
| 距离度量 | cosine | cosine |
| meta 字段 | 不变 | 不变 |
| 写入方式 | **只读保留** | upsert（用同一份 chunk_id） |

**隔离原则**：
- 新建 Collection 名后缀 `_v2`，旧 Collection **不动、不删、只读保留**
- 同一 Chroma 实例，不同 Collection
- 旧 Reranker Agent 用的 `mini_rag_knowledge_chunks_v1` Collection 不动
- 重建脚本在写入前**显式检查**目标 Collection 名 + 目标维度，防止误写

### 3.4 重建流程

```
前置准备：
  1. 验证 Collection 切换路径（配置项 / 环境变量？）
  2. 验证模型服务缓存行为（换模型是否需要重启？）
  3. 新建临时重建脚本（读取快照 → 构建 Final Chunk → 生成向量 → upsert 到 _v2）

重建步骤：
  1. 下载新模型到本地（或 HuggingFaceEmbeddings 自动拉取缓存）
  2. 改 config.py 的 embedding_model_path + embedding_dimension
  3. 切换 Collection 配置到 archive_final_chunks_v2
  4. 启动重建脚本：
     a. 从 PostgreSQL 读取所有已 CONFIRMED 且无 visibility_blocking 的 ArchiveDocument
     b. 读取每个文档的 ParsedSnapshot
     c. 用 build_final_chunks() 构建 Final Chunk（函数不变）
     d. 用 embed_final_chunks() + 新模型重新生成向量
     e. upsert 到 archive_final_chunks_v2 Collection
  5. 运行同一固定集 12 题，对比结果

清理步骤（无论实验是否通过都执行）：
  - 删除 archive_final_chunks_v2 Collection 的所有数据（或直接 drop）
  - 回退 config 到旧值
  - 重启服务，确认旧 Collection 恢复可用
```

### 3.5 验收指标（与 C4 基线对比）

| 指标 | C4 基线（bge-small-zh-v1.5） | 实验验收门槛 | 判定 |
|---|---|---|---|
| 候选池完整性 | 12/12 | 12/12 | ⚠️ 候选不足 30 则停止 |
| **公开覆盖召回** | 7/8 | **≥ 7/8** | 🔴 降了则失败 |
| 严格诊断召回 | 5/8 | 记录对比（归因用，**非正式门槛**） | 🟡 观察 |
| 无据拒答 | 0/2 | **2/2** | 🔴 未达则失败 |
| 项目隔离 | 2/2 | **2/2** | 🔴 失败则停，优先修隔离 |
| P95 延迟 | ~1820 ms（C4-A） | 记录对比 | 🟡 观察，不阻塞 |
| 模型加载时间 | ~1-2 s | 记录对比 | 🟡 观察 |
| CPU 内存 | ~0.8 GB | 记录对比 | 🟡 观察 |
| 向量存储 | 基准 | 记录膨胀倍数 | 🟡 观察 |

**正式决策门**（沿用 P14 既有门槛，非新增）：
- 公开覆盖召回 **≥ 7/8**
- 无据拒答 **= 2/2**
- 项目隔离 **= 2/2**

**三项同时通过才有资格讨论阈值冻结和 P12 解锁。**

严格诊断召回作为**增强诊断指标**单独记录，用于归因分析（例如定位"召回改善是来自哪几题"），不升级为阻断门槛。如需升级严格召回为阻断门槛，必须重新做产品决策。

---

## 4. 回滚与验收门槛

### 4.1 新旧 Collection 并存

```
┌─ Chroma tenant ───────────────────────────────────────────────┐
│                                                                  │
│  mini_rag_knowledge_chunks_v1    （旧制度检索，512 维）          │
│  archive_final_chunks           （旧正式档案，512 维）           │
│  archive_final_chunks_v2        （实验新模型，768/1024 维）      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

- 新旧 Collection 通过不同的配置项选择，不同时激活
- 实验期间旧 Collection 持续存在且**只读保留**
- "切回旧 Collection 只需改配置重启"**待实测验证**（需确认模型服务缓存、Collection 引用等行为）

### 4.2 失败时回滚

1. **代码回滚**：`config.py` 恢复旧的 `embedding_model_path`、`embedding_dimension` 和旧 Collection 配置
2. **索引回滚**：切回使用旧 `archive_final_chunks` Collection（它从未被修改或写入）
3. **清理实验数据**：删除 `archive_final_chunks_v2` Collection 的所有数据，或直接 drop Collection
4. **数据安全**：PostgreSQL 不涉及——Final Chunk 重建只影响 Chroma 向量，业务数据不变
5. **污染检查**：确认旧 Collection 的向量数量和内容在实验前后完全一致（需在实验开始前做快照）

### 4.3 清理核对（必须执行）

| 检查项 | 期望值 |
|---|---|
| `archive_final_chunks_v2` 向量数 | 0（已清空） |
| `archive_final_chunks` 向量数 | 实验前快照值（**未被修改**） |
| PostgreSQL CONFIRMED 文档数 | 不变 |
| 临时账号/会话/知识库/项目 | 为 0 |
| Chroma heartbeat | ok |
| `/health` | ok（embedding 维度回到 512） |

### 4.4 不通过的实验如何处理

- 公开覆盖召回 < 7/8：记录"新 Embedding 模型在当前 Final Chunk 上召回未达标"
- 无据拒答仍不足 2/2：记录"无据分离问题未随 Embedding 升级解决"
- 两项都未通过：记录"Embedding 模型替换不能单独解决问题"，需要重新讨论 Reranker 模型替换、Chunk 粒度调整或混合检索

### 4.5 AV1-P02 固定集保护

**实验不能改变 Ground Truth**：
- 不修改 `pixie_qa/` 固定集
- 不修改 `scripts/generate_archive_v1_eval_data.py` 生成逻辑
- 不修改任何题目的标准证据定义
- 实验结束后固定集保持原值

---

## 5. 代码变更影响汇总（如果选 base/large-zh）

### 5.1 前置验证（实验授权前必须完成）

| 验证项 | 方法 |
|---|---|
| Collection 切换配置路径 | 读 `get_final_collection()` 代码，确认当前 Collection 名是硬编码还是 config 驱动 |
| 模型服务缓存行为 | 重启服务前后各跑一次 `/health`，确认换模型后是否需要重启实例 |
| Chroma 维度校验 | 写一个小脚本验证：512 维 Collection 能否写入 768 维向量（预期应抛 InvalidDimensionException） |
| 重建脚本与业务操作隔离 | 确认重建只走 `app/services/rag/embeddings.py` + `app/services/archive/final_chunks.py`，不产生 `ArchiveOperation` 记录 |

### 5.2 需要修改的文件

| 文件 | 改动 | 类型 |
|---|---|---|
| `app/core/config.py` | 改 `embedding_model_path` + `embedding_dimension`；新增或切换 Collection 名配置项（**待前置验证确认路径**） | 配置值 + 可能新增配置项 |
| `app/services/infrastructure/chroma.py`（仅客户端）或 `app/services/archive/final_chunks.py`（正式归档 Collection/重建适配器） | 重建脚本或测试脚本（**需新建**），读取快照重建向量，写入 `_v2` Collection | 新建脚本 |
| `.env.example` | 对齐默认维度和 Collection 名 | 文档同步 |
| `requirements.txt`（如果选 m3 且需 FlagEmbedding） | 新增依赖 | 依赖变更 |

### 5.3 不需要修改的文件（自动跟随 config）

| 文件 | 原因 |
|---|---|
| `app/routers/health.py:61` | 用的是 `settings.embedding_dimension` |
| `app/services/rag/embeddings.py` | 用的是 `settings.embedding_dimension` |
| `app/services/archive/final_chunks.py` | 用的是 `settings.embedding_dimension` |

### 5.4 总改动量估算

| 候选 | 配置改动 | 新增脚本 | 可能的代码改动 | 依赖改动 |
|---|---|---|---|---|
| bge-base-zh-v1.5 | 3 个值（model path + dimension + Collection 名） | 重建脚本（需新建） | **取决于前置验证结果**（Collection 切换路径、缓存清理） | 无 |
| bge-large-zh-v1.5 | 同上 | 同上 | 同上 | 无 |
| bge-m3（dense-only） | 同上 | 同上 | 同上 + 可能的 API 适配 | 可能需 FlagEmbedding（如要 sparse/ColBERT） |

> **之前写的"0 行代码改动"不完整**。即使是最简单的 base-zh-v1.5，也需要前置验证 Collection 切换路径、模型缓存行为，以及新建重建脚本。这些不是配置值变更能覆盖的。

---

## 6. 决策建议

### 6.1 三种可能结论

| 结论 | 条件 | 后续 |
|---|---|---|
| **1. 值得申请独立实验授权** | 候选模型 API 兼容 OK、前置验证通过、风险可接受 | 下一步：写正式实验授权请求，列出具体变更清单和 TDD 步骤 |
| **2. 资源或接入风险过高，暂不更换** | 所有候选模型 CPU 内存超出现有机器、前置验证发现 Collection 切换路径需大幅代码改造 | 下一步：考虑受控 Chunk 粒度调整或降低质量门槛 |
| **3. 模型替换不能单独解决问题** | 候选模型在当前 Final Chunk 上理论上也无法解决无据分离问题 | 下一步：重新讨论 Reranker 模型替换 + Embedding 替换的组合，或混合检索 |

### 6.2 当前推荐

**bge-base-zh-v1.5 独立实验已完成，正式配置暂不切换**。结果显示召回改善但无据分离未改善，因此不能据此冻结阈值或解锁 P12：

1. bge-base 与 small 同属 zh-v1.5，`HuggingFaceEmbeddings` 兼容；768 维健康检查和 Chroma 维度拒绝均已实测。
2. 同一 12 题固定集、63 个上下文 Chunk、Top-30、Reranker 和查询表达保持不变时，公开覆盖由 `7/8` 提升到 `8/8`。
3. 无据拒答仍为 `0/2`，隔离保持 `2/2`，P95 为 `3859.89 ms`；三项正式门槛未同时通过。
4. 实验只写入 `archive_final_chunks_exp_bge_base_20260902`，实验结束后已删除；正式 `archive_final_chunks` 实验前后均为 `0` 条。

### 6.3 建议的完整执行顺序

```
Phase 0：前置验证（已完成）
  ├─ 验证 Collection 切换路径
  ├─ 验证模型服务缓存行为
  ├─ 验证 Chroma 维度校验行为
  └─ 验证重建脚本的业务隔离设计

Phase 1：bge-base-zh-v1.5 独立实验（已完成，质量未通过）
  │
  ├─ 三项正式门槛全过 → 进入 C4-C 阈值标定 → 冻结阈值 → 解锁 P12
  ├─ 召回过但无据没过 → 考虑换 Reranker 模型（需重新授权）
  └─ 公开覆盖召回仍 < 7/8 →
        Phase 2：bge-large-zh-v1.5 独立实验
          │
          ├─ 三项正式门槛全过 → 进入阈值标定
          └─ 仍未过 → Embedding 替换不足，需讨论 Chunk 粒度 / Reranker / 混合检索

Phase 3：Vue 完整 E2E
  （后端模型决策、后端回归和文档收口之后）
```

### 6.4 不推荐 bge-m3 作为第一步

- 当前 zh-v1.5 系列内的候选尚未尝试，先在同架构内完成变量隔离
- 最大 tokens 8192 的长序列优势在当前 Final Chunk 粒度下无法充分发挥
- 如果 base 和 large 都没解决召回缺失问题，再考虑 m3 的长序列能力

---

## 7. 附录：配置值变更示意

### 7.1 切到 bge-base-zh-v1.5

```python
# config.py:91-95
embedding_model_path: Path = Path(
    "../py-doc/py-doc-deepseek-server/models/bge-base-zh-v1.5"
)
embedding_dimension: int = Field(default=768, gt=0)
```

### 7.2 切到 bge-large-zh-v1.5

```python
# config.py:91-95
embedding_model_path: Path = Path(
    "../py-doc/py-doc-deepseek-server/models/bge-large-zh-v1.5"
)
embedding_dimension: int = Field(default=1024, gt=0)
```

### 7.3 切到 bge-m3（如果需要）

```python
# config.py:91-95
embedding_model_path: Path = Path(
    "../py-doc/py-doc-deepseek-server/models/bge-m3"
)
embedding_dimension: int = Field(default=1024, gt=0)
```

### 7.4 Chroma Collection 隔离（已验证路径）

```python
# 本次实验使用进程级覆盖，不修改正式 .env 或 config.py 默认值。
CHROMA_FINAL_COLLECTION = "archive_final_chunks_exp_bge_base_20260902"
```

---

## 8. Phase 0/1 实测记录（2026-09-02）

### 8.1 前置验证

- 模型目录 `bge-base-zh-v1.5` 可由 `HuggingFaceEmbeddings` 加载，健康检查输出维度 `768`。
- 512 维向量写入临时 Collection 成功；随后写入 768 维被 Chroma 拒绝（`InvalidArgumentError`），临时 Collection 已删除。
- 新增 `scripts/archive_v1_d1_embedding_rebuild.py`，通过显式 `collection` 参数写入实验 Collection；单元测试证明不调用 `Session.add/commit/flush`，不会创建 `ArchiveOperation`。
- 由于开发库在实验前没有确认文档，实际固定集采用既有 P14 验收器创建并确认 12 份虚构资料；该资料由验收器在结束时清理。

### 8.2 bge-base 固定集结果

| 指标 | C4 基线 | bge-base 实验 | 判定 |
|---|---:|---:|---|
| 候选池完整性 | 12/12 | 12/12 | 通过 |
| 公开覆盖召回 | 7/8 | **8/8** | 通过 |
| 严格候选池命中 | 5/8 | 6/8 | 诊断 |
| 无据拒答 | 0/2 | **0/2** | 未通过 |
| 项目隔离 | 2/2 | **2/2** | 通过 |
| 检索 P95 | 1819.88 ms（C4-A） | 3859.89 ms | 观察 |

索引上下文覆盖为 `63/63` 个 Chunk，零上下文文档为 `0`；严格语义不一致为 `2` 题。三项正式门槛要求公开覆盖至少 `7/8`、无据拒答 `2/2`、隔离 `2/2`，本实验因无据拒答失败而不通过。

### 8.3 回滚与清理

- 实验 API 仅监听 `127.0.0.1:8004`，通过进程级变量使用 768 维 Embedding、外部本地 Reranker 和实验 Collection；未修改 `.env`、`config.py` 默认值或正式 Collection。
- 实验 Collection 在结果核对后按精确名称删除；正式 Collection 条数保持 `0`。
- PostgreSQL 临时用户为 `0`，项目总数恢复为实验前的 `4`，业务文档和 CONFIRMED 文档均为 `0`；P14 临时文件无残留。

### 8.4 决策

本次实验支持“bge-base 能改善当前固定集公开召回”的局部结论，但不能支持“模型替换已解决 P14”或“可冻结阈值”。正式默认仍保持 bge-small 512 维；后续是否切换到 bge-base、是否更换 Reranker 或调整产品范围，需要新的明确决策。C4-C 阈值标定和 P12 真实 DeepSeek 质量验收继续阻塞。

## 9. 路径 A：Reranker 模型替换启动记录（2026-09-02）

用户已明确授权开始路径 A。首轮目标模型按当前建议选择 `BAAI/bge-reranker-large`；如果改用 `m3e-reranker-large`，需在实验记录中重新注明模型名和路径。

### 9.1 单变量边界

| 项目 | 路径 A 固定值 |
|---|---|
| Embedding | D1 使用的 `bge-base-zh-v1.5`，768 维 |
| Reranker | 仅由 `BAAI/bge-reranker-base` 替换为 `BAAI/bge-reranker-large` |
| 候选池 | Top-30，完整性要求 12/12 |
| 查询表达 | 保持 C4-A 的 `c4_a`，不复用 C4-B 模板 |
| Final Chunk 输入 | `evidence_values`，固定 63 个上下文 Chunk |
| 评测集 | AV1-P02 固定 12 题，不增删、不改 Ground Truth |
| 公开返回 | 最多 10 条；不冻结新阈值 |
| 配置与数据 | 仅进程级环境变量和独立实验 Collection；不改 `.env`、默认配置或正式 Collection |

D1 的实验 Collection 已删除，因此路径 A 不能复用旧 Collection；实验前需用同一固定资料重新构建新的 bge-base 隔离 Collection。

### 9.2 当前前置验证状态

- 已确认本机存在 `bge-reranker-base`、`bge-base-zh-v1.5`、`bge-small-zh-v1.5` 和用户下载的 `bge-reranker-large`。
- D2 已完成本地加载、有限输出校验和 CPU 延迟/内存观察：large 默认变体 P95 为 `12885.233 ms`，`max_length=256 + batch_size=8` 的最佳变体 P95 为 `12016.218 ms`，峰值工作集约 `2.30 GiB`。
- large 在当前 CPU 环境下超过 D2 的 `8000 ms` 风险线，因此没有启动完整路径 A 固定集实验；D2 详细记录见 [P14-D2-Reranker延迟前置验证方案.md](P14-D2-Reranker延迟前置验证方案.md)。

### 9.3 后续执行顺序

1. 将目标模型放入本地目录，并用 `local_files_only=True` 完成加载与有限推理前置验证。
2. 在独立 Collection 中重建同一批 bge-base 向量；核对 768 维、63 个 Chunk 和 12/12 候选池完整性。
3. 只替换 Reranker 路径，运行既有 P14 固定集验收器，记录公开覆盖、严格命中、无据拒答、隔离和 P95。
4. 与 D1 结果逐项对照；实验结束后按精确 Collection 名称、临时用户/项目 UUID 和文件范围清理并复核。

当前路径 A 状态为“已授权、已完成 large 延迟前置验证、完整质量实验暂缓”；未执行的完整固定集实验不写成通过。
