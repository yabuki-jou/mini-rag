# Mini RAG 下一次 Codex 对话交接

> 更新时间：2026-09-08
> 工作区：`mini-rag-handwrite`
> 本文只记录下一次对话需要遵守的当前事实和边界，不是聊天摘要。

## 1. 读取顺序与工作区边界

下一次开始前依次读取：

1. `AGENTS.md`
2. `LEARNING_PLAN.md`
3. `docs/stage/handoff.md`
4. `docs/decisions.md`
5. `docs/review/P14-rag检索质量改进/P14-D6-文档绑定与Top8证据保留方案.md`
6. `docs/review/P14-rag检索质量改进/P14-D5-证据充分性与拒答判定层方案.md`
7. `docs/review/P14-rag检索质量改进/检索质量问题分析与改进策略.md`

当前分支为 `main`，HEAD 为 `2771e3d feat: 完成 P14 C3-C 查询表达复验`，相对
`origin/main` 为 `behind 0/ahead 12`。当前 `git status` 共 61 条：33 个已跟踪修改、
28 个未跟踪；本交接文件和 `docs/decisions.md` 自身未跟踪。工作区包含 C4、D1-D6、正式 Embedding 切换的未提交实现、测试和文档，
不能以 HEAD 代表现状。后续必须先检查状态并只显式选择获准文件，禁止 `git add -A`；
不得读取、输出或提交 `.env`、凭据、Token、数据库数据和运行日志。

提交前必须重新检查工作区；`docs/stage/handoff.md`、`docs/decisions.md`、D1-D6 方案等关键文件仍未跟踪，
需按批准范围显式选择，禁止逐项之外的批量暂存。

## 2. 已完成

- 智慧档案 V1 的 P01-P13 实现切片已完成。
- D5 结构化问答已实现：候选为空时短路拒答；非空时对正式范围候选执行一次应用层
  DeepSeek `invoke`，同时生成 `decision`、`answer` 和引用编号；引用由服务端按 1-based
  编号映射。D6-B 后 `app/services/archive_question_service.py` 固定 `top_k=8`。
- D6-A 的确定性代码阶段已完成：`_build_archive_prompt` 现在接收正式候选，按候选首次出现顺序
  为相同 `document_id` 生成相同请求内 `D1/D2` 临时引用，并加入服务端文件名、定位和摘录；
  Prompt 不包含原始文档、Chunk、用户、项目或知识库 ID。TDD RED 为新增 `2 failed`，GREEN 后
  问答服务 `18 passed`；完整回归 `377 passed, 2 skipped, 100 warnings`，`compileall` 和
  `git diff --check` 通过。Top-5、检索、模型、配置和公开响应均未改变。
- D6-A 真实安全门已使用 D5 捕获的同一 Top-5 固定 12 题与真实 DeepSeek 完成：有据回答并
  正确引用仍为 `5/8`，无据拒答 `2/2`、隔离拒答 `2/2`；Q-07 从历史 D5 的跨文档 V1.0
  错答变为固定拒答和空引用。证据忠实 `12/12`、拒答质量 `11/12`、响应契约 `12/12`，
  问答阶段 P95 `1193.20 ms`。全部 24 个 pending evaluator、dataset analysis、action plan
  和 Step 6 verifier 已完成，结果位于忽略路径 `pixie_qa/results/20260908-113715`。
- D5 正式固定集结果：候选池 `12/12`，Top-5 公开覆盖 `6/8`，有据正确引用 `5/8`，
  无据拒答 `2/2`，隔离 `2/2`，检索 P95 `5225.84 ms`，复用已捕获 Top-5 候选后的问答执行
  P95 `1658.41 ms`（不包含检索耗时）；
  质量门为 false，P12 阻塞。实验数据已清理。结果证据位于本地忽略路径：
  `pixie_qa/results/20260908-015502`、`pixie_qa/results/d5-formal-20260908/formal-summary.json`、
  `pixie_qa/results/p14-d4-base-snapshot.json`。
- D6-A 本轮已重新运行完整回归：`377 passed, 2 skipped, 100 warnings in 30.37s`；
  `compileall app tests scripts pixie_qa/archive_v1_p14` 通过；`git diff --check` 通过，
  仅有 LF/CRLF 提示。
- D6-B 已按独立授权由 GPT-5.6 Luna 子 Agent 实际实现：RED 证明旧问答仍请求 Top-5，GREEN
  后生产代码唯一变量为 `top_k=8`；独立 `d6b-capture` 支持真实 Top-8 捕获，历史 D5 捕获保持
  Top-5。主 Agent 复核相关测试 `61 passed`，完整回归 `380 passed, 2 skipped, 105 warnings`，
  `compileall` 与 `git diff --check` 通过。
- D6-B 真实固定集保持 D1 base/768、`evidence_values`、base Reranker、Top-30、`c4_a`、原 12 题
  和独立 Collection：候选池 `12/12` 完整，Top-8 公开覆盖 `8/8`；最终有据正确回答并引用
  `7/8`、无据拒答 `2/2`、隔离拒答 `2/2`，达到既定门槛。Q-02/Q-07 正确；Q-01 第一候选
  已含准确日期但仍错误拒答。证据忠实 `12/12`、拒答质量 `11/12`、响应契约 `12/12`，检索
  P95 `5801.62 ms`，问答 P95 `2046.44 ms`。Pixie Step 6 verifier 已通过，结果位于忽略路径
  `pixie_qa/results/20260908-121612`。
- D6-B 清理已复核：PostgreSQL `p14be-*` 用户、`p14-*` 项目和 P14 知识库均为 `0`；实验
  Collection `archive_final_chunks_exp_d6b_20260908` 已删除；临时 API `8004` 已停止。
- 正式 Embedding/Collection 切换已按用户授权完成：GPT-5.6 Luna 按 TDD 将仓库默认、
  `.env.example` 和 Compose 切换为 `bge-base-zh-v1.5`、768 维与 `evidence_values`，并新增只允许
  精确正式目标、拒绝非空 Collection、失败恢复为空 cosine Collection 的重建脚本。主审相关测试
  `34 passed`，全量回归 `391 passed, 2 skipped, 105 warnings`，`compileall` 与 `git diff --check`
  通过。
- 重建前只读预检确认正式 `archive_final_chunks` 为 0 条，PostgreSQL 文档与确认档案均为 0；
  正式重建结果为 0 文档、0 Chunk，768 维 canary 写入后已删除。临时 API `8005` 联合健康检查
  返回 API、数据库、Chroma、Embedding 全部 `ok`，Embedding 为 `dimension=768`，随后已停止。

## 3. 已确认的决策与当前配置

- V1 方向是智慧档案与企业文档智能；`Project` 是业务隔离边界，`KnowledgeBase` 是内部检索范围，
  对应 [`DEC-001`](../decisions.md) 与 [`DEC-002`](../decisions.md)。
  详见 `docs/design/需求说明.md`、`docs/design/技术架构.md`。
- 正式问答只能使用服务端校验的 `CONFIRMED` 且可见 Final Chunk；身份和范围由服务端注入
  `user_id + project_id + kb_id + document_id`，Ground Truth 绝不能进入运行时，详见
  `app/services/archive_retrieval_service.py`、`app/services/archive_question_service.py`，对应 [`DEC-002`](../decisions.md) 与 [`DEC-003`](../decisions.md)。
- 质量门固定为有据正确回答并引用 `>=7/8`、无据 `2/2`、隔离 `2/2`；未通过时不得降低门槛或
  修改评测集来掩盖失败，对应 [`DEC-004`](../decisions.md)。
- 用户已确认合同资料的 `DOCUMENT_DATE` 表示合同签订日期，其他资料表示文档自身日期，对应 [`DEC-006`](../decisions.md)。
  需求、数据库和接口设计已同步；Q-01 按既定 Ground Truth 定性为漏答/证据充分性失败，
  不修改固定问题或 Ground Truth。
- 同配置 D4 快照中，Q-02 正确证据为 Reranker 第 8 名，Q-07 为第 6 名；D5 只向模型
  提供前 5 条。Q-07 还存在 Prompt 缺少文件名、定位和可信文档绑定而导致的跨文档值误配。
- D4 证明 base 的统一阈值可行数量为 `0`；large 已决定跳过（纯 Reranker 约 12 秒、
  完整链路约 9.5 秒）。
- 当前不采用统一 Reranker 阈值路径，对应 [`DEC-005`](../decisions.md)。
- 正式仓库默认与 Compose 已采用 bge-base/768、`evidence_values` 和 `archive_final_chunks`；
  当前源码默认配置还包括 `candidate_k=30`、`query_mode=c4_a`。项目 `.env` 未读取或修改，
  若其中存在旧覆盖值，直接本地启动的实际进程可能偏离仓库默认；本次临时 API 使用显式非敏感
  进程配置完成了 768 维联合健康验证。
- 本地模型目录存在：`C:/Users/失吹丈/Desktop/Langchain学习/models/embedding_models/bge-base-zh-v1.5`
  和 `C:/Users/失吹丈/Desktop/Langchain学习/models/embedding_models/bge-reranker-base`。

## 4. 未完成与待确认

- D6 方案 G0～G5 均已满足；D1 base/768 基线的 P12 固定质量门已经通过，正式配置与
  Collection 切换也已完成。因为正式数据库当时没有确认档案，本次只证明空库重建、768 维
  canary 和依赖健康，不构成现有业务文档已重向量化的证据。
- Q-01 仍为有直接证据时的安全漏答；若继续优化证据充分性判断，必须作为新的单变量任务处理，
  不得回改固定问题、Ground Truth 或降低门槛。
- Vue 工作台已有早期部分联调；按用户决定，FR-034～FR-041 的完整真实 E2E 放在后端质量路径之后，对应 [`DEC-007`](../decisions.md)，
  当前尚未完成。
- 当前 Pixie `7118` 页面可达；D6-B 临时 FastAPI 已停止。Chroma 心跳与 PostgreSQL 迁移
  `0010_account_auth` 本轮已复验，但其后续存活状态仍需现场核验。

## 5. 已知风险

- Pixie 每题有两条重叠 `llm_span_trace`，原因未核实；不能据此宣称一次或两次网络请求。
- 旧 `docs/codebase/` 以及 C3/C4 历史文件可能过时，涉及当前状态必须回到实际代码和本交接列出的来源。
- 清理脚本曾因数据库权限误报；真实验收后必须精确复核 PostgreSQL、Chroma 和文件系统，不能只信退出码。
- 本地同步模型调用存在延迟风险；当前工作区规模较大且未提交。
- `app/services/archive_retrieval_service.py` 的 `_formal_document_ids()` 接收但未使用
  `user_id`；当前 HTTP 路由依靠 `app/dependencies/project_context.py` 的 `ProjectContextDep`
  先验证 owner，因此没有当前路由越权证据，但未来任何新调用方必须保留该授权依赖或补充服务层所有权校验。

## 6. 下一起点

1. D6-A、D6-B 与正式 Embedding/Collection 切换均已完成，不再在同一阶段修改 Prompt、模型、候选数或固定集。
2. 下一步建议继续 FR-034～FR-041 的相邻 Vue 工作台完整真实 E2E，并在完成后收口文档和最终回归。
3. Q-01 若要继续优化，另建独立单变量方案；不得回改固定问题、Ground Truth 或降低门槛。
