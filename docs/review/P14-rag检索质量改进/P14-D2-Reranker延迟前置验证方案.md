# P14-D2：Reranker 延迟前置验证方案

**角色**：只读验证；不修改业务逻辑、不切换默认 Reranker
**状态**：前置验证已完成；`bge-reranker-large` 在当前 CPU 环境下不建议进入完整替换实验
**最后更新**：2026-09-03

---

## 1. 背景与目标

D1 bge-base 实验成功解决了 Embedding 层面的召回问题（公开覆盖 8/8），但无据拒答仍为 0/2。当前阻塞项已明确转移到 **Reranker 无法在当前候选集上分离真相关与伪相关**。

在授权 bge-reranker-large / m3e-reranker-large 替换实验之前，**必须先验证大模型 Reranker 在当前 CPU 环境下的延迟是否可接受**。经验表明（555438），CrossEncoder 在 CPU 上对 30 个候选推理的延迟可能达到 10s+，需要前置确认。

---

## 2. 当前 Reranker 实现快照

| 项目 | 当前值 |
|---|---|
| 模型 | BAAI/bge-reranker-base |
| 加载方式 | `sentence_transformers.CrossEncoder(path, device="cpu", local_files_only=True)` |
| 推理方式 | `predict([(query, content) for content in contents])`，默认参数 |
| max_length | **未显式设置**（当前模型配置的最大位置长度为 514；实际截断行为由 tokenizer/模型配置决定） |
| batch_size | **未显式设置**（当前 sentence-transformers 版本的 `predict` 默认 `batch_size=32`，30 个候选会落在一个 batch） |
| 候选数 | **固定 30**（`archive_reranker_candidate_k: int = Field(default=30, ge=30, le=30)`） |
| 设备 | **硬编码 CPU**（`archive_reranker_device = "cpu"`） |
| 当前 P95 | ~3860 ms（D1 base-zh 实验，包含 Embedding + Reranker + 完整检索链路） |

**关键发现**：
- 当前 Reranker 实现没有显式设置 `max_length`；不能把 512 直接归因于 CrossEncoder 的通用默认值，应以当前模型配置和 tokenizer 的实际行为为准。
- 当前实现没有显式设置 `batch_size`，实际使用 sentence-transformers 的默认 `32`；30 个 pairs 会在一个 batch 中推理。
- 当前候选数是**硬编码 30**（ge=30, le=30），不可调

---

## 3. 验证方案

### 3.1 验证范围

| 变量 | 值 | 说明 |
|---|---|---|
| Embedding | 纯延迟探针不访问 Embedding；完整链路阶段需**重新构建** D1 base-zh-v1.5 的隔离 Collection | D1 Collection 已清理，不能复用旧 `_v2` |
| Reranker 候选数 | 固定 30（同正式配置） | 保持与正式实验一致 |
| Final Chunk | 不变 | |
| 查询表达 | 保持 C4-A 基线 | |
| 固定集 | AV1-P02 12 题 | |
| 验证对象 | **只测 Reranker 推理延迟**，不测完整检索链路 | 使用固定的 12×30 个长度受控 pairs，跳过 Embedding + Chroma 查询 |

### 3.2 候选模型

| 模型 | 参数量 | 大小 | 说明 |
|---|---|---|---|
| **bge-reranker-base**（当前） | ~56M | ~224 MB | 基线 |
| **bge-reranker-large** | ~410M | 完整快照约 6.74 GB；单权重约 2.24 GB | 大模型，中文 CrossEncoder 常用选择 |
| **m3e-reranker-large** | 待验证 | 待验证 | 候选模型名和本机可用性需单独确认 |
| **bge-reranker-v2-m3** | ~568M | ~2.2 GB | v2 架构，支持多语言；dense-only 场景 |

### 3.3 验证步骤

**Step 1：环境探测**
```python
# 新建 scripts/p14_d2_reranker_latency_probe.py
# 目的：确认当前机器的 CPU 信息和内存

import psutil, os, torch

print(f"CPU: {psutil.cpu_count(logical=True)} 核")
print(f"物理内存: {psutil.virtual_memory().total / (1024**3):.1f} GB")
print(f"可用内存: {psutil.virtual_memory().available / (1024**3):.1f} GB")
print(f"torch available: {torch.cuda.is_available()}")
print(f"device count: {torch.cuda.device_count()}")
```

**Step 2：下载候选模型到本地**
- 手动下载或通过 `CrossEncoder(model_name)` 触发缓存
- 每个模型下载后记录文件大小

**Step 3：单模型延迟测试**（每个候选模型分别执行）

```python
# 对每个模型：
# 1. 加载模型，记录加载耗时
# 2. 准备每道题各自的 30 个固定 pairs（共 12×30），不让不同问题共享候选内容
# 3. 跑 warm-up（3 次，不计入统计）
# 4. 跑 12 次正式推理（对应 12 题），记录每次耗时
# 5. 输出 P50 / P95 / max / 平均耗时

# 变体 A：默认参数（max_length 未指定，batch_size=32）
# 变体 B：max_length=256（降序列长度）
# 变体 C：max_length=256 + batch_size=8（分批推理）
# 变体 D：max_length=256 + batch_size=16
```

**Step 4：交叉验证——完整链路（本轮未执行）**
- D1 的 `_v2 Collection` 已删除；如继续执行，必须先重新构建隔离 Collection。
- 该步骤会接触真实固定资料和 Chroma，不属于本轮纯延迟前置验证；只有在模型通过延迟门槛后才有必要安排。

**Step 5：内存监控**
- 每个模型加载后记录 RSS（Resident Set Size）
- 推理过程中监控内存峰值
- 是否超过本机可用内存的 80%

### 3.4 诊断模板

```
=== P14-D2 Reranker 延迟前置验证 ===

机器环境:
  CPU: X 核
  物理内存: X.X GB
  可用内存: X.X GB
  CUDA: available / not available

[模型名] [变体（默认/max_length=256/...）]:
  模型加载耗时: XXX ms
  加载后内存 RSS: XXX MB
  推理内存峰值: XXX MB
  单次推理候选数: 30
  推理次数: 12
  P50 latency: XXX ms
  P95 latency: XXX ms
  Max latency: XXX ms
  Mean latency: XXX ms

  P95 占 D1 完整链路比例: XX%

[完整链路验证]:
  Embedding/Chroma 耗时：本轮未测，不用估算值替代实测
  Reranker P95（实测）: XXX ms
  完整链路 P95：本轮未测
```

---

## 4. 决策门

### 4.1 延迟阈值（只读参考，不直接作为质量门槛）

| 结果 | 决策 |
|---|---|
| **Reranker 纯推理 P95 ≤ 3000 ms** | ✅ 完整链路预计 ≤ 4000 ms，可接受 → 直接授权 Reranker 替换实验 |
| **3000 ms < P95 ≤ 8000 ms** | 🟡 完整链路预计 ≤ 9000 ms，接近演示可接受上限 → 记录风险，与 Embedding 替换组合授权 |
| **P95 > 8000 ms** | 🔴 完整链路预计 > 10000 ms，可能破 15s 演示红线 → 尝试变体（max_length=128, batch_size 优化）后再评估 |
| **OOM / 加载失败** | 🔴 该模型在当前机器上无法运行 → 标记为不可行，换更轻量候选或重新评估方向 |

### 4.2 优先级

```
先测 bge-reranker-large（最可能成功）
  │
  ├─ P95 ≤ 3000ms → ✅ 授权 Reranker 替换实验
  ├─ P95 > 8000ms → 试 max_length=256 / batch_size 优化
  │     │
  │     ├─ 优化后 P95 ≤ 5000ms → 🟡 授权（带风险提示）
  │     └─ 优化后仍 > 8000ms → 跳过，测下一个
  └─ OOM → 跳过，测下一个

次测 m3e-reranker-large（备选）
最后测 bge-reranker-v2-m3（架构不同，先不花时间）
```

### 4.3 同时验证的潜在优化点

在延迟测试过程中，顺带验证以下优化点是否能显著降低延迟：

| 优化 | 方法 | 预期效果 |
|---|---|---|
| 降 max_length | 改成 256 或 128 | 序列长度减半，推理量约减 40-60% |
| 加 batch_size | 分批推理（8 或 16 pairs / batch） | 避免一次性 OOM，CPU 缓存更友好 |
| 降 candidate_k | 从 30 降到 20 或 15 | 直接减少推理 pairs 数量（但当前是 hardcoded，需改 config） |

---

## 5. 产出物

验证完成后输出一份简短报告：

| 产出 | 内容 |
|---|---|
| **延迟实测表** | 每个候选模型 × 每个变体的 P50/P95/Max/Mean + 内存占用 |
| **与当前基线对比** | base vs large vs m3e vs m3 的延迟增量倍数 |
| **决策建议** | 哪种模型 + 哪种变体在当前机器上的延迟和质量预期平衡最好 |
| **优化建议** | 哪些 max_length / batch_size / candidate_k 设置值得正式纳入 |

---

## 6. 实测结果（2026-09-03）

本轮使用 `scripts/p14_d2_reranker_latency_probe.py`，在本地 CPU 上对固定的 12 道问题、每题 30 个长度受控候选执行 3 次预热和 12 次正式推理。输入只用于延迟与内存测量，不代表检索质量结果；未访问 PostgreSQL、Chroma，也未启动 API。

### 8.1 环境

| 项目 | 实测值 |
|---|---:|
| CPU 逻辑核数 | 20 |
| 物理内存 | 15.8 GiB |
| CUDA | 不可用，`torch 2.13.0+cpu` |
| 采集方式 | Windows `GetProcessMemoryInfo`，记录 RSS 与峰值工作集 |

### 8.2 延迟与内存

| 模型 | 变体 | P50 | P95 | Max | Mean | 峰值工作集 |
|---|---|---:|---:|---:|---:|---:|
| bge-reranker-base | 默认（`batch=32`） | 3394.769 ms | 3520.995 ms | 3554.795 ms | 3328.769 ms | 1.41 GiB |
| bge-reranker-large | 默认（`max_length` 未指定，`batch=32`） | 12067.797 ms | 12885.233 ms | 12913.833 ms | 12209.743 ms | 2.30 GiB |
| bge-reranker-large | `max_length=256`，`batch=32` | 12229.759 ms | 12401.440 ms | 12414.556 ms | 12223.427 ms | 2.30 GiB |
| bge-reranker-large | `max_length=256`，`batch=8` | 11856.286 ms | 12016.218 ms | 12061.299 ms | 11862.774 ms | 2.30 GiB |
| bge-reranker-large | `max_length=256`，`batch=16` | 11951.335 ms | 12055.524 ms | 12066.461 ms | 11924.391 ms | 2.30 GiB |

目标模型加载耗时约 `1932.754 ms`，加载后 RSS 约 `743 MB`；base 加载后 RSS 约 `741 MB`。large 的最佳变体相对默认 P95 仅下降约 `6.7%`，仍明显高于 `8000 ms` 风险线。

### 8.3 决策

- `bge-reranker-large` 在当前 CPU 环境下不满足 D2 的延迟可接受条件；即使使用 `max_length=256 + batch_size=8`，纯 Reranker P95 仍约 `12.0 s`。
- 本轮不进入完整路径 A 固定集实验，不修改默认 Reranker，不重建 D1 Collection。
- 该结果只说明当前机器上的 CPU 延迟风险，不代表 large 的检索质量，也不证明 GPU/量化/其他运行时后端的性能。
- 如继续路径 A，应先选择并验证更轻量或不同运行时的 Reranker；`m3e-reranker-large` 的模型标识和本机可用性仍需单独确认。

---

## 7. 安全边界

- **业务数据只读**：不修改正式 Collection、不修改业务逻辑、不切换默认 Reranker
- **使用固定候选 pairs**：延迟测试只测 Reranker.predict()，跳过完整检索链路（避免污染 Chroma / 产生临时业务记录）
- **模型文件由用户预先下载**：本轮探针只读取本地模型目录，不自动下载或删除模型文件
- **不启动健康 8000 服务**：纯脚本测试，不影响现有运行中的服务

---

## 8. 与后续实验的关系

```
P14-D2（本次）: Reranker 延迟前置验证（业务数据只读）
  │
  ├─ 找到可接受延迟的 Reranker 模型
  │     │
  │     └─ → 申请 P14-D3 Reranker 替换实验授权（单变量：换 Reranker）
  │           │
  │           ├─ 无据拒答 2/2 ✅ → 冻结阈值 → 解锁 P12
  │           ├─ 无据拒答仍不足 → Embedding+Reranker 已都换过，需讨论 Chunk / 混合检索
  │           └─ 召回掉了 → 记录问题，回到 Embedding 方向
  │
  └─ 当前 bge-reranker-large 延迟不可接受（256 + batch=8 仍约 12 秒）
        │
        └─ → 讨论：降低 candidate_k / 降 max_length / 降低质量门槛 / 放弃 CPU 路径
```

---

*本方案的前置探针已于 2026-09-03 执行完成；业务数据、正式配置和正式 Collection 未改变。large CPU 延迟未通过，完整路径 A 实验暂不执行。*
