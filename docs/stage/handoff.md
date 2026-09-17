# Mini RAG 下一次 Codex 对话交接

> 更新时间：2026-09-14
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

当前分支为 `main`；`v1.0.0` 指向候选基线 `1f2d092`，`v1.0.1` 指向修复提交 `de868aa`。
`origin/main` 已包含 `v1.0.1` 修复；本交接状态提交后同步推送。
services、评测目录和 Agent 服务拆分均已纳入该基线；相邻 Vue 项目不在本轮范围。
发布基线主审发现的四个陈旧 service 来源路径已以 TDD 修复并提交为 `de868aa`。
`main`、`v1.0.0` 和 `v1.0.1` 均已以非强制方式发布到 `origin`；本轮不移动已发布标签。
NFR-021 日志按日期分层提交 `52ff0c8` 和后续评测来源行号修复 `1416e46` 已推送；当前
Router 契约修复已完成，验证记录见第 30 节；提交和远端同步状态每次以实际 Git 为准。
后续只显式选择获准文件；不得读取、输出或提交 `.env`、凭据、Token、数据库数据和运行日志。

提交前必须重新检查工作区；`pixie_qa/results/` 仍是忽略的本地评测证据，不能误当源码提交。
需按批准范围显式选择，禁止 `git add -A` 或逐项之外的批量暂存。

## 2. 已完成

- 智慧档案 V1 的 P01-P13 实现切片已完成。
- D5 结构化问答已实现：候选为空时短路拒答；非空时对正式范围候选执行一次应用层
  DeepSeek `invoke`，同时生成 `decision`、`answer` 和引用编号；引用由服务端按 1-based
  编号映射。D6-B 后 `app/services/archive/questions.py` 固定 `top_k=8`。
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
  `compileall app tests scripts evals/archive` 通过；`git diff --check` 通过，
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
  `app/services/archive/retrieval.py`、`app/services/archive/questions.py`，对应 [`DEC-002`](../decisions.md) 与 [`DEC-003`](../decisions.md)。
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
- Vue 工作台的 FR-034/035～FR-041 已实现并完成真实代理链路验收，对应 [`DEC-007`](../decisions.md)。
- FR-039 的一般有据、无据、待确认排除、项目隔离和真实合同签订日期页面场景均已通过。
  第一份对照素材把业务隔离说明机械标为 `CONTRACT`，原文仍写通用“文档日期”，模型拒答；换成明确包含
  “资料类型：CONTRACT”和“合同签订日期”的合同式原文后正确回答并引用，故前一结果属于素材语义失真。
- 2026-09-13 验收时 Pixie `7118`、FastAPI `8000` 与 Vue `5173` 已启动，`/health` 为 `200`，
  API、database、Chroma 和 768 维 Embedding 全部正常。本轮发布文档收口未重新探测这些进程，
  不把历史运行状态表述为当前仍在线。项目 `.env` 未读取或修改；PostgreSQL 迁移
  `0010_account_auth` 当时已复验。
- FR-040 与 FR-041 的真实 `5173/api` 代理链路已通过，但本轮 Codex browser provider 持续返回连接错误，
  因此没有新增真实 DOM 点击证据；页面接线、筛选、分页和交互由 Vue 自动化测试覆盖，不能写成浏览器点击已验收。

## 5. 已知风险

- Pixie 每题有两条重叠 `llm_span_trace`，原因未核实；不能据此宣称一次或两次网络请求。
- 旧 `docs/codebase/` 以及 C3/C4 历史文件可能过时，涉及当前状态必须回到实际代码和本交接列出的来源。
- 清理脚本曾因数据库权限误报；真实验收后必须精确复核 PostgreSQL、Chroma 和文件系统，不能只信退出码。
- 本地同步模型调用存在延迟风险；功能代码基线已稳定，发布文档收口后仍需用户决定 push/tag。
- 前端 URL 与 Store 项目 ID 一致性问题已由自动化测试覆盖并修复，但修复后没有新增真实浏览器
  DOM 点击证据；不能把自动化结果改写为浏览器人工验收。
- `app/services/archive/retrieval.py` 的 `_formal_document_ids()` 接收但未使用
  `user_id`；当前 HTTP 路由依靠 `app/dependencies/project_context.py` 的 `ProjectContextDep`
  先验证 owner，因此没有当前路由越权证据，但未来任何新调用方必须保留该授权依赖或补充服务层所有权校验。

## 6. 下一起点

1. D6-A、D6-B 与正式 Embedding/Collection 切换均已完成，不再在同一阶段修改 Prompt、模型、候选数或固定集。
2. FR-034/035～FR-041 Vue 接入、真实代理链路验收、前端 URL 修复和 services/评测/Agent 结构收口均已完成。
3. V1 发布文档基线已整理；下一步由用户 review 6 个未推送本地提交及本次文档提交，决定 push/tag 或直接进入作品集材料。
4. Q-01 若要继续优化，另建独立单变量方案；不得回改固定问题、Ground Truth 或降低门槛。
5. 云端部署、容量、拓扑和资源规格仍保持独立决策，不因本次发布文档收口自动启动。

## 7. 项目文档有据问答复测（2026-09-09）

- 已按 Multi-Agent/TDD 新增 `archive-question-project-grounded`：4 条全部为有据问题，候选摘录逐条
  回查 `docs/decisions.md`、`README.md`、`docs/stage/handoff.md` 与干扰项 `AGENTS.md`；候选通过
  Pixie 输入边界注入，因此本轮只评测回答层，不证明 Chroma 召回、Reranker 排序或项目隔离。
- RED 为目标 JSON 不存在导致 `5 failed`；GREEN 后 P14 Pixie 材料测试 `23 passed`。完整回归
  `397 passed, 2 skipped, 121 warnings`，`compileall` 与 `git diff --check` 通过。
- 真实 DeepSeek 结果位于忽略目录 `pixie_qa/results/20260909-002713`。4/4 回答状态与引用契约通过，
  4/4 没有错误拒答；严格整句包含门仅 2/4。第 1、4 题是 Markdown/措辞差异造成的评测器
  假阴性；Q-01 诊断题引用正确但没有直接回答“已非召回失败”，并把数值概括为“指标均达标”，
  证据忠实性人工评分为 0.75。
- 8 个 pending Agent evaluator 已全部人工评分；dataset analysis、action plan 和 Step 6 verifier
  均已完成。首次运行目录 `20260909-002635` 因 Windows GBK 无法输出 Pixie 图标而机械失败，
  无 `meta.json`；UTF-8 重跑后完成上述正式结果。

## 8. 企业规模资料真实 RAG 链路复测（2026-09-09）

- 已按用户确认的自然规模生成 2 个隔离项目、16 份虚构企业资料，正文约 26 万字符，来源覆盖
  当前仓库 15 个文件；没有重复段落凑体量。其中 4 份为可真实解析的 PDF，共 30 页。PPT 暂不纳入。
- 生成器与真实链路扩展按 Multi-Agent/TDD 由 GPT-5.6 Luna 实现。RED 先后覆盖模块缺失、来源
  密度不足、证据标注契约缺失、PDF 缺失和七项字段标注缺失；GREEN 后定向回归 `74 passed`。
- 16 份资料均经过真实 HTTP 上传、解析、七项字段人工确认和正式索引，共形成 88 个带字段上下文的
  Final Chunk。20 题 Top-30 候选池 `20/20` 完整，12 道有据题标准证据进入 Top-8 为 `12/12`，
  检索 P95 为 `7375.92 ms`；该延迟是观测值，本扩展集没有新增性能门槛。
- DeepSeek 真实回答层结果：结构契约 `20/20`；有据题语义正确且引用有支持 `12/12`；4 道无据题
  全部固定拒答且引用为空。严格连续子串/引用门为 `13/20`，其中 GROUNDED-04、06、11、12 共
  4 题是 Markdown、标点或自然措辞造成的确定性评测器假阴性。
- 隔离检索 `4/4` 未出现另一项目的隐藏文档，没有跨项目证据泄漏。隔离题固定拒答仅 `1/4`：
  ISOLATION-01～03 的目标事实不是项目专属事实，当前项目资料也含同义通用规则，模型据当前项目
  证据作答并错误绑定到问题里的另一项目。该结果同时暴露隔离题设计不足和回答实体绑定不足，
  不能表述为跨项目检索泄漏，也不能把隔离拒答记为通过。
- 40 个 pending Agent evaluator 已全部人工完成：证据忠实性均值 `0.925`，拒答质量均值 `0.85`；
  dataset analysis、action plan 和 Step 6 verifier 均已完成。结果位于忽略目录
  `pixie_qa/results/20260909-021610`，检索摘要与捕获数据位于
  `pixie_qa/results/enterprise-rag-20260909`。
- 最终完整回归 `407 passed, 2 skipped, 121 warnings`；`compileall`、`git diff --check`
  和 Step 6 verifier 均通过。
- 第一次真实运行因资料缺少必填字段收到 HTTP 422；修正测试和生成器后，第二次因临时 API 使用
  production 环境导致诊断接口 404；development 配置重跑后完成。每次均执行精确清理，最终复核
  临时用户、项目、文档和正式 Collection 对应 Chunk 全部为 `0`，临时 API 已停止。
- 下一步优先重写 4 道隔离题为两个项目互不重复的专属事实，再修正严格评测器的 4 个假阴性。
  是否加强生产回答层的实体绑定必须另立单变量方案并单独授权；之后用同一捕获数据连续运行 3 次
  观察真实模型随机性。

## 9. 企业扩展集 P0/P1/P3 修正与稳定性复测（2026-09-09）

- P0 已按 TDD 完成：4 道隔离题改为只存在于另一项目的专属编号、日期、责任单位和审批结论；
  测试同时证明全部必要事实片段存在于隐藏证据，并且提问项目所有资料均不包含这些片段。
- P1 已按 TDD 完成：离线标注支持可选 `expected_answer_fragments`，提供时要求全部必要事实
  片段匹配；未提供时继续使用旧的 `expected_answer` 整句包含规则。该标注只进入 Pixie
  `eval_metadata`，不进入问题、候选或生产请求，AV1-P02 固定集保持兼容。
- 真实链路重新处理 16 份资料、88 个 Final Chunk 和 20 题：Top-30 候选池 `20/20` 完整，
  有据标准证据进入 Top-8 为 `12/12`，检索 P95 `7871.52 ms`。四道隔离题的隐藏文件和
  全部专属事实片段均未进入提问项目候选。
- P3 使用同一份真实 Top-8 捕获连续运行 3 次 DeepSeek。三次均为有据语义正确并引用
  `12/12`、无据拒答 `4/4`、隔离拒答 `4/4`、响应契约 `20/20`；问答 P95 分别为
  `2593.94 ms`、`1331.69 ms`、`1013.77 ms`。
- 三次原始机械门均为 `19/20`，唯一失败都是 GROUNDED-10：回答事实与 B-03 引用正确，
  但括号解释或引用标记破坏了连续整句匹配。该原始记录保留不回写；随后增加“原文件、
  PostgreSQL、Chroma Chunk、document_id、关联”五个必要片段，三种实际表达的确定性测试均通过。
- 三次共 120 个 pending Agent evaluator 已人工完成，三个 dataset analysis、action plan 和
  Step 6 verifier 均通过。跨运行汇总位于
  `pixie_qa/results/enterprise-rag-p0p1-20260909/stability-report.md`。
- 最终完整回归 `412 passed, 2 skipped, 121 warnings`；`compileall` 与
  `git diff --check` 通过。PostgreSQL 临时用户、项目、普通文档和归档文档均为 `0`；
  Chroma 没有残留 Collection；临时 API 已停止。
- 当前结果没有提供必须修改生产实体绑定规则的失败证据，P2 保持未实施。FR-034/035、FR-036 与
  FR-037 已完成真实本地 E2E；运行时 Reranker 路径后续已修正并完成新一轮真实 RAG 复测，
  下一步进入 FR-039～FR-041。

## 10. FR-034/035 前端接入与真实本地 E2E（2026-09-10）

- 相邻 Vue 工作台已完成 FR-034/035：7 个前端文件共 55 个自动化测试通过，`npm run typecheck`、
  `npm run test` 和 `npm run build` 均通过。
- 真实链路为 `5173/api → 8000`：TXT 文档 AI 建议成功；regenerate 成功且版本从 `v2` 更新到 `v3`；
  人工保存后为 `v4` 且建议入口消失；此前失败的 PDF 重试成功并返回 `PDF_PAGE` 证据；刷新页面后草稿可恢复。
- 修复原因是真实模型输出中文字段键和格式不稳定。生产建议 Prompt 已明确七个英文字段键、枚举和值列契约，
  并绑定 `response_format={"type":"json_object"}`。后端建议定向测试为 `11 passed`，完整回归为
  `414 passed, 2 skipped`，`compileall` 与 `git diff --check` 通过。
- 该阶段记录的健康阻塞已在 2026-09-10 后续复验中解除：当前 Embedding 为 768 维且健康；
  临时用户、项目、3 份文档、原文件和快照已精确清理，数据库与文件零残留。
  本阶段结果只代表 FR-034/035；FR-036 的后续结果见下一节。

## 11. FR-036 前端接入与真实本地 E2E（2026-09-10）

- 已按 `docs/implementation/FR036-Vue确认与取消确认实施计划.md` 和 Multi-Agent/TDD 完成前端确认、
  取消确认与重新确认。Luna 的首轮 RED 为 7 个断言失败，主审补充的版本透传与已保存检查状态 RED
  为 4 个失败；眉题遗漏和范围说明过时分别另以 1 个失败断言锁定后修复。最终前端为 7 个测试文件、65 个测试通过，
  `npm run typecheck` 与 `npm run build` 通过。后端全量回归为 `414 passed, 2 skipped, 121 warnings`，
  `compileall` 与 `git diff --check` 通过；差异检查只有既有 LF/CRLF 提示。
- 真实页面经 `5173/api → 8000 → PostgreSQL/Embedding/Chroma` 验证：七字段已检查的文档从
  `v9` 确认为 `CONFIRMED v10`，正式 Collection 为 9 个 Final Chunk；取消确认后变为
  `PENDING_RECONFIRMATION v11` 且 Final Chunk 为 0；重新确认后为 `CONFIRMED v12` 且重建为
  9 个 Final Chunk。刷新页面后仍为 `v12`，证明状态持久化。
- FR-036 验收当时正式检索请求返回 `503 / RERANKER_UNAVAILABLE`。这与 `/health` 的 API、database、
  Chroma、Embedding 全绿不矛盾，因为健康检查没有加载 Reranker；该阻塞已在后续配置修正和第 12 节复测中解除。
- 临时文档、项目、账号、认证会话、内部知识库和 Final Chunk 最终均精确核对为 0。业务 API 清理
  先完成文档、项目与向量删除；沙箱外精确清理账号范围后完成 PostgreSQL 与 Chroma 零残留复核。

## 12. Reranker 恢复后的企业规模真实 RAG 复测（2026-09-10）

- 使用者私下完成 Reranker 路径配置并重启 8000；`/health` 为 `200`，API、database、Chroma 与
  768 维 Embedding 全绿。随后重新生成 2 个隔离项目、16 份虚构企业资料、20 题，其中包含
  4 份共 30 页 PDF，不复用旧捕获冒充本轮结果。
- 真实业务接口完成上传、解析、字段确认、正式索引、Top-30 候选检索、Top-8 捕获和清理；形成
  88 个 Final Chunk，候选池完整 `20/20`，12 道有据题目标证据进入 Top-8 为 `12/12`，检索
  P95 为 `7993.01 ms`。运行结束后 `p14be-*` 账号、`p14-*` 项目、会话、内部知识库和正式
  Collection 均复核为 0。
- 第一次真实 DeepSeek 运行的响应契约为 `20/20`，机械门为 `19/20`。逐题检查确认唯一失败
  `GROUNDED-08` 的问题只问解析第一步，回答“先识别文件格式”且引用正确，而 expected_answer
  多要求了未被提问的第二步，属于测试用例范围不一致；40 个 Agent evaluator 完成人工评分且
  Step 6 校验通过。
- Luna 按 TDD 将 `GROUNDED-08` 的 expected_answer 最小收窄到第一步，保留完整证据原文且未修改
  AV1-P02 或生产 RAG。RED 为目标断言 1 个失败，GREEN 后生成器测试 `10 passed`。
- 复用同一真实 Top-8 捕获再次运行 DeepSeek：有据语义正确并引用 `12/12`、无据固定拒答 `4/4`、
  隔离固定拒答 `4/4`、响应契约与机械门均为 `20/20`，问答 P95 为 `1328.36 ms`；40 个 Agent
  evaluator 全部完成且 Step 6 校验通过。当前没有证据要求修改生产检索、Prompt、模型或回答服务。
- 最终后端全量回归为 `415 passed, 2 skipped, 121 warnings`；`compileall`、两个结果目录的 Step 6
  校验和 `git diff --check` 均通过，差异检查只有 LF/CRLF 提示。

## 13. FR-037 前端接入与真实本地 E2E（2026-09-10）

- 已按 `docs/implementation/FR037-Vue清单关联实施计划.md` 和 Multi-Agent/TDD 完成前端类型、四个 API
  方法、Pinia 状态、独立关联面板和页面接线。Luna 首轮 RED 为 API `1 failed`、Store
  `3 failed`、View `1 failed`，面板因组件不存在而收集失败；主审指出文档级 loading key 后又以
  `1 failed` 锁定并修正。最终前端 8 个测试文件、74 个测试通过，`typecheck` 与 `build` 通过。
- 真实页面经 `5173/api → 8000 → PostgreSQL/Embedding/Chroma` 验证：已确认档案在没有人工关联时，
  匹配的必需清单项保持 `MISSING`；页面明确标记类型/阶段建议，人工确认后变为 `SATISFIED`，刷新页面
  后保持。删除关联后立即恢复 `MISSING`；重新建立关联后，取消档案确认使文档变为
  `PENDING_RECONFIRMATION v11` 且清单恢复 `MISSING`，关联记录仍保留；重新确认为 `CONFIRMED v12`
  后自动恢复 `SATISFIED`。
- 页面还以一项未被系统建议的同项目必需项验证主动关联：该项显示“可主动关联”，用户确认后同样由
  后端派生为 `SATISFIED`。跨项目拒绝继续由后端相关集成测试覆盖，前端不会列出其他项目清单项。
- 最终后端全量回归为 `415 passed, 2 skipped, 121 warnings`，FR-037 定向接口测试 `6 passed`，
  `compileall` 和 `git diff --check` 通过。临时用户、项目、文档、关联、Final Chunk 与原文件已精确
  清理，核验 PostgreSQL `0`、Chroma `0`、文件残留 `false`。FR-038 后续结果见下一节。

## 14. FR-038 前端接入与本地目录链路验收（2026-09-11）

- 已按 `docs/implementation/FR038-Vue处理列表与正式档案目录实施计划.md` 和 Multi-Agent/TDD 完成前端类型、
  API、Pinia 状态、处理列表分页/状态筛选、正式档案目录/筛选/分页/详情证据及页面接线。Luna 首轮
  RED 覆盖缺失的 API、Store 和组件行为；主审又以失败测试锁定快速筛选请求被去重、旧请求错误污染、
  日期为空与区间互斥、合同详情日期标签、本页数量冒充总数和无参刷新丢失页码。最终前端 9 个测试文件、
  `89 passed`，主 Agent 串行复验 `typecheck` 与 `build` 均通过。
- 本地页面经 `5173/api → 8000 → PostgreSQL` 验证：同项目 21 份文档第一页 20 条、第二页 1 条；
  `CONFIRMED` 状态筛选准确返回 2 条。正式目录只返回这 2 份档案，19 份待解析文档没有进入正式范围；
  合同筛选、日期为空筛选、合同签订日期语义、七字段证据定位和刷新后的数据库持久化均通过，浏览器控制台
  没有错误。
- 本轮 Chroma 8001 未运行，因此健康状态为 degraded（API、PostgreSQL 和 768 维 Embedding 正常，
  Chroma 失败）。验收数据通过真实 HTTP 上传，并在 PostgreSQL 中精确补齐 2 份不含向量的正式目录事实；
  这证明目录读取链路，不构成确认—INDEX 或 Chroma 链路证据。取消合同确认时后端先提交
  `PENDING_RECONFIRMATION`，随后因 Chroma 清理失败返回 `503`；目录刷新后合同已退出正式范围，但该次取消
  不能记为完整成功。
- 临时账号、项目、21 份文档、2 份档案扩展和原文件已精确清理并复核为 0/无残留。第一次清理因正式档案
  约束拒绝中间态并整体回滚；调整为先删证据和字段、再由档案删除级联快照后清理成功。下一步为 FR-039。

## 15. FR-039 前端接入与真实 RAG 页面验收（2026-09-11）

- 已按 `docs/implementation/FR039-Vue带证据问答实施计划.md` 和 Multi-Agent/TDD 完成前端 DTO、两个 POST API、
  Pinia 结果与并发隔离、独立带证据问答/原文检索面板及 `questions` 页面接线。Luna 首轮 RED 为 3 个失败；
  主审又以失败测试锁定回答引用卡缺少诊断分数，以及旧请求错误覆盖当前错误。最终前端 10 个测试文件、
  `99 passed`，标准 `typecheck` 和 `build` 通过。
- 后端 FR-039 定向测试 `41 passed`，完整回归 `415 passed, 2 skipped, 121 warnings`，`compileall` 和
  `git diff --check` 通过。`/health` 为 `200`，API、PostgreSQL、Chroma 与 768 维 Embedding 全部正常。
- 真实页面经 `5173/api → 8000 → PostgreSQL/Chroma/DeepSeek` 验证：正式检索 Top-8 返回目标原文且展示
  文件、文本位置、摘录和双分数；普通有据问题正确回答并引用。无据问题、仅存在于 `UPLOADED` 文档的问题、
  仅存在于另一项目的问题均固定拒答且引用为空；切换到另一项目后同一问题正确回答并引用，证明项目隔离生效。
  切换项目会清空旧结果，刷新后不会恢复本地伪造问答历史，浏览器控制台无错误。
- 第一份合同日期对照使用被机械标为 `CONTRACT` 的业务隔离说明，原文仍写“文档日期”，模型拒答；Top-10
  虽包含日期和通用字段语义，却没有该文档类型的直接原文证据。随后使用明确写有“资料类型：CONTRACT”和
  “合同签订日期：2026-05-20”的合同式资料复测，模型正确回答 `2026-05-20`，且只引用该日期原文，
  因此前一失败定性为测试素材语义失真，AC-FR-039-05 的真实合同场景通过，不修改生产检索或 Prompt。
- 首次自动清理在退出阶段返回稳定失败；重新通过业务删除接口核对项目和文档均为 0，再按唯一临时用户名
  清理认证主体。最终 PostgreSQL 项目、文档、主体为 `0/0/0`，两个临时项目的 Final Chunk 均为 `0`，
  第二轮合同对照账号、项目、文档和 Final Chunk 也全部复核为 0，临时脚本已删除。下一步进入 FR-040。

## 16. FR-040 前端接入与真实物理删除链路验收（2026-09-11）

- 已按 `docs/implementation/FR040-Vue文档物理删除实施计划.md` 和 Multi-Agent/TDD 完成项目级删除 API、
  Pinia 删除状态与派生数据刷新、处理列表逐行删除入口及带文件名的确认流程。Luna 首轮 RED 为
  新增 9 个测试失败；主审继续以 RED 锁定删除刷新期间切换项目、`503` 必须清空已阻断证据、
  `409` 不应清空仍有效证据。最终前端 10 个测试文件、`110 passed`，标准 `typecheck` 和 `build` 通过。
- 后端 FR-040 定向路由/服务测试 `5 passed`。完整回归首次出现项目素材评测集引用
  `handoff.md` 旧行号且答案仍写“下一步 FR-039”的失败；已按独立计划将该题换为引用
  `docs/design/接口设计.md` 的稳定删除恢复事实，材料测试 `6 passed`，最终完整回归恢复为
  `415 passed, 2 skipped, 121 warnings`，`compileall` 与 `git diff --check` 通过。
- 真实 `5173/api → 8000 → PostgreSQL/Chroma/DeepSeek` 链路验证：删除前文档为 `CONFIRMED`，
  正式目录包含该档案，必需清单为 `SATISFIED`，Top-8 检索和问答均返回该文档证据且 Final Chunk
  大于 0；删除返回 `204` 后，文档/档案/关联数据库计数均为 0，Final Chunk 为 0，原文件不存在，
  清单恢复 `MISSING`，同一问题检索为空并 `REFUSED_NO_EVIDENCE`，且存在恰好 1 条
  `DOCUMENT_DELETED` 脱敏审计。
- 两次临时运行最终按命名前缀核验账号、项目和文档均为 0，临时脚本已删除。浏览器控制 provider
  本轮持续连接失败，因此没有新增真实页面点击证据；View 的取消/确认、只调用一次 Store、逐行 loading
  和错误恢复由自动化测试证明。FR-041 的后续结果见下一节。

## 17. FR-041 前端接入与真实脱敏审计链路验收（2026-09-11）

- 已按 `docs/implementation/FR041-Vue脱敏审计查询实施计划.md` 和 Multi-Agent/TDD 完成前端审计 DTO、
  12 类受控筛选、服务端分页、Pinia 项目/竞态隔离、独立审计面板与页面接线。Luna 首轮先以
  `actor_id` 缺失建立后端 RED，再完成前端各层 RED/GREEN；主审发现三个枚举没有实际写入点后，
  修订计划并由 Luna 补齐字段更新、解析重试成功和建议重试成功审计。
- 实际代码与阶段文档曾有两处不一致：`AuditLogRead` 没有返回需求和接口设计已声明的 `actor_id`；
  `ARCHIVE_FIELD_UPDATED`、`PARSE_RETRIED`、`SUGGESTION_RETRIED` 只有筛选枚举，没有服务写入。
  当前均已修复。三类审计与对应成功业务数据同事务提交；失败重试不生成成功审计；摘要只保存
  `field_name`、`review_status`、`status`、`version` 等受控值，不保存字段正文、解析正文或模型内容。
- 主 Agent 首次完整回归发现两个旧测试假设失效：删除测试把同一文档的全部审计误认为只有删除审计，
  草稿服务夹具创建了没有项目归属的项目文档。Luna 仅修正测试事实后，后端完整回归为
  `416 passed, 2 skipped, 121 warnings`，前端完整回归为 `118 passed`；`compileall`、类型检查、
  生产构建和 `git diff --check` 均通过。
- 第一次真实代理运行命中了未自动重载的旧 8000 进程，响应仍缺 `actor_id`；脚本 `finally` 已清理该次
  临时范围。明确重启后端后，同一 `5173/api → 8000 → PostgreSQL` 验收通过时间倒序分页、受控类型
  筛选、操作人和时间、资源标识、脱敏摘要以及另一用户访问 `403`；临时账号、项目、知识库和认证会话
  最终复核残留为 `0`，临时脚本已删除。
- FastAPI `8000` 与 Vue `5173` 当前仍在运行，联合健康为 `200`，API、database、Chroma 与 768 维
  Embedding 全部正常。Codex browser provider 再次返回 `nodeRepl.fetch request failed`，所以本节没有
  新增真实 DOM 点击证据；组件和 View 行为由前端自动化测试证明，代理 API 结果不冒充浏览器点击。
- FR-030～FR-041 功能接入已闭环。当时下一学习步骤为更新最终验收报告、逐项核对 AC-FR-030～AC-FR-041
  的证据与未验证边界，并清理文档中已经过时的“前端待接入”描述；不在该步骤修改 RAG 生产策略。

## 18. 最终验收报告与文档一致性收口（2026-09-11）

- 已重写 `docs/implementation/验收报告.md`，将 AC-FR-030～AC-FR-041 共 45 项逐项映射到
  确定性测试、真实本地链路或组合证据，并单列浏览器 DOM、独立空库迁移自动化、云端容量、
  多实例、分布式事务和 Q-01 安全漏答等未验证边界。
- 已核对需求、架构、数据库、API、实施计划、学习计划、README 和旧制度 Agent 演示/导览中的
  当前状态。2026-08-28 等历史阶段结果继续保留，但已明确由 2026-09-11 的最终状态覆盖。
- 验收核对发现 AC-FR-040-03 原先只有项目权限依赖测试和其他项目路由的越权测试，没有直接覆盖
  项目文档 DELETE 的跨用户 HTTP 回归。Luna 已补充既有行为的特征测试；该测试首次即通过，
  没有伪造 RED。它验证 `403/PROJECT_FORBIDDEN`、删除服务未调用、数据库记录、原文件和快照不变。
- 补测后目标文件为 `3 passed`，后端完整回归更新为
  `417 passed, 2 skipped, 121 warnings`；`compileall` 与 `git diff --check` 均通过。
- P14 本地功能与验收收口完成后，当时确定的下一实现任务是修复前端 URL 与 Store 项目 ID 一致性风险；
  该任务的完成结果见第 19 节。

## 19. 前端项目 URL 与 Store 一致性修复（2026-09-12）

- 问题触发方式已确认：用户停留在旧账号的项目路由，退出后在同一 View 登录或注册另一账号；
  Store 会加载新账号项目并选择首项，但旧路由参数不会再次触发 watcher，因此地址栏仍显示旧项目 ID。
- Luna 按 `docs/implementation/前端项目路由与Store一致性修复实施计划.md` 执行。RED 为新增
  `4 failed`、既有 `10 passed`；四项分别覆盖登录、注册、有效深链接和退出登录。
- GREEN 仅修改相邻前端 `ArchiveWorkspaceView.vue` 与对应测试：认证成功后复用
  `syncRouteProject()`；退出完成后使用 `router.replace('/')`。Store、路由表、API 与后端未改。
- 主 Agent 复核目标 `14 passed`、前端完整回归 `122 passed`、`typecheck` 和 `build`
  通过，构建转换 68 个模块。首次主审 typecheck 因沙箱不能写相邻目录的 `tsbuildinfo` 返回
  `EPERM`，按已授权范围重新执行后通过；这不是 TypeScript 失败。
- browser provider 仍返回 `nodeRepl.fetch request failed`，没有新增 DOM 点击证据。
- 该阶段原定的提交范围审查已由第 20 节 services 分层重构完成；相邻前端不在本次提交范围。

## 20. services 分层重构（2026-09-13）

- `app/services/` 已按 `archive/`、`project/`、`rag/`、`agent/`、`identity/`、
  `infrastructure/` 分包；旧根级 service 文件不再作为源码入口。
- 档案与旧知识库文档生命周期已拆分；Chroma 客户端与旧知识库 Collection 操作已拆分；
  档案共享读取能力集中在 `app/services/archive/reads.py`。
- `tests/services/` 已镜像业务域；包边界测试覆盖目标模块集合、模块导入、旧导入与字符串
  MonkeyPatch 路径，以及跨 services 私有导入。
- TDD RED 证据：首次运行包边界测试得到 `3 failed`，原因是旧平铺模块仍存在、目标子包不存在、
  `app.services.archive` 无法导入；随后 GREEN 通过包边界和相关服务测试。
- services 测试 `118 passed, 1 skipped`，后端全量回归 `426 passed, 2 skipped, 121 warnings`；
  `compileall` 与 `git diff --check` 通过。旧 services Python 导入全文搜索无命中。
- 本轮重构已形成独立本地提交；提交后工作区为 clean，尚未推送。
- 本轮不执行真实 PostgreSQL/Chroma/DeepSeek 写入或 Vue E2E，不重新声明真实链路验收结果。

## 21. 制度 Agent 评测替换（2026-09-13）

- 当前评测入口已迁入 `evals/policy_agent/`，包含 HTTP 级 Runnable、6 条固定虚构数据、
  确定性契约评审器、两个语义评审器和项目/入口/准则/映射说明。
- TDD RED 为新模块缺失和数据集规则不完整；GREEN 后制度评测相关单测通过。Runnable 使用临时
  SQLite 业务库与 Checkpoint，经真实 FastAPI 路由注册、登录、创建知识库和 Agent 会话；不连接
  真实 PostgreSQL，也不读取业务数据。
- 真实 DeepSeek 运行 `20260913-015833` 的 6/6 样例通过，确定性与人工语义评分共 12/12 为
  `1.0`；pending 已全部评分，dataset analysis、action plan 和 Step 6 verifier 均已完成。
- 制度评测相关测试 `12 passed`；后端全量回归 `438 passed, 2 skipped, 127 warnings`；
  `compileall app tests scripts evals` 与 `git diff --check` 通过。
- 通过范围仅为“真实 DeepSeek + 注入的虚构制度检索结果”的单轮回答、拒答、澄清、闲聊和
  授权注入防护。它不证明真实 Chroma 检索、跨轮状态或连接失败恢复。
- 准备阶段的一次真实制度检索曾暴露 BGE 输出 768 维、既有制度 Collection 接受 512 维的
  不兼容，Chroma 返回维度错误并由 HTTP 映射为 503；该问题已由第 22 节的后续迁移解除。
- 当时只读预检确认 `mini_rag_knowledge_chunks_v1` 为 cosine、条目数 0；512 维查询接受，
  768 维查询明确拒绝。后续迁移仍保持原 Collection 名与 cosine 度量。
- 已删除根级 `pixie_qa/` 中属于已删除请假领域和旧制度入口的运行器、评审器、数据集及追踪；
  智慧档案评测源码已在后续迁入 `evals/archive/`，本地结果仍位于忽略目录。

## 22. 制度 Agent Collection 维度迁移（2026-09-13）

- `mini_rag_knowledge_chunks_v1` 已在空库前提下由 512 维原名重建为 768 维，Collection 名与
  cosine 度量未变；当前只读复核为条目数 0、768 维查询接受、512 维查询拒绝。
- 迁移脚本位于 `scripts/policy_collection_embedding_rebuild.py`，包含只读预检、显式
  `--apply`、768 维三字段 canary 和 512 维失败恢复；迁移实现提交为 `08afe49`。
- 真实虚构制度文档链路已验证上传、READY、Agent `COMPLETED` 和引用正确；临时 PostgreSQL
  六表与对应 Chroma 用户范围复核为 0。运行时使用共享 Checkpoint，本轮没有删除整个共享文件，
  因此该次单题结果不能表述为隔离 Checkpoint 验收。
- 迁移提交记录的自动化结果为脚本测试 `15 passed`、全量 `453 passed, 2 skipped`。提交后主审
  修正只读预检隐式创建、聚合查询加载完整行、维度探针吞异常、canary ID 未核对和底层连接错误
  直出问题。收口后脚本测试 `27 passed`，全量回归 `465 passed, 2 skipped, 127 warnings`，
  `compileall` 与 `git diff --check` 通过。
- 修正后的真实默认预检没有执行写入；当前环境无法建立 PostgreSQL 连接时返回稳定
  `POSTGRES_PREFLIGHT_FAILED` 并停止。Collection 仍为迁移后的 cosine/768 维空库。

## 23. 智慧档案评测目录迁移（2026-09-13）

- 智慧档案 Runnable、评审器、数据集和说明已从 `pixie_qa/archive_v1_p14/` 迁入
  `evals/archive/`；`run_app.py` 同步更名为 `runnable.py`。
- 对应测试已从 `tests/pixie_qa/archive_v1_p14/` 迁入 `tests/evals/archive/`；验收数据集生成器、
  Pixie 结果汇总脚本和测试中的导入/字符串入口已同步更新。
- `pixie_qa/` 不再是 Python 包，只保留 `.gitignore`、本地 Pixie 状态和忽略的历史结果；
  没有删除或改写 `pixie_qa/results/`。
- TDD RED 为新包不存在及旧路径仍被引用；GREEN 后包边界 `2 passed`、智慧档案评测测试
  `26 passed`、P14 验收脚本测试 `42 passed`；合并定向回归 `111 passed`，后端全量回归
  `467 passed, 2 skipped, 127 warnings`，`compileall app tests scripts evals` 与
  `git diff --check` 通过。本批只移动模块与导入，不重跑 DeepSeek、PostgreSQL、Chroma
  或历史 Pixie 数据集。

## 24. 文档职责目录收口（2026-09-13）

- `docs/review/` 根目录原有的 11 份 `*实施计划.md` 已迁入 `docs/implementation/`，包括
  企业规模 RAG、前端路由、项目素材评测和 FR-034～FR-041 相关计划。
- `docs/review/P14-rag检索质量改进/` 保持原专题边界；设计评审、设计补丁、验证冻结清单和
  历史对话记录未移动。
- 交接文档中的 7 处旧计划路径已同步更新；全文搜索确认仓库内不再引用这些旧路径，
  后端全量回归 `467 passed, 2 skipped, 127 warnings`，`compileall app tests scripts evals`
  与 `git diff --check` 通过。本批仅调整文档归类，不改变运行时代码或验收结论。

## 25. 旧 RAG Prompt 辅助模块归位（2026-09-13）

- 原 `app/agents/rag_agent.py` 不包含 LangGraph 状态、工具或编排，只负责旧知识库聊天的引用、
  上下文和 Prompt 消息组装，现已迁入 `app/services/rag/prompting.py`。
- `app/services/rag/chat.py` 已改用新模块；旧入口不保留。新增 `test_prompting.py` 固定引用顺序、
  上下文编号、历史角色和当前问题位置，并将新模块纳入 services 包边界检查。
- TDD RED 为新模块导入失败；GREEN 定向回归 `42 passed`，后端全量回归
  `470 passed, 2 skipped, 127 warnings`。本批不改变 Prompt 文本、HTTP 契约或业务行为。

## 26. Agent 与身份服务级测试补齐（2026-09-13）

- 新增 `tests/services/agent/test_sessions.py`，覆盖会话创建及知识库范围、Checkpoint 三类安全错误
  映射、用户可见历史与来源绑定、工具审计排序和损坏摘要拒绝。
- 新增 `tests/services/identity/test_authentication.py`，覆盖密码哈希、用户名冲突、统一登录失败、
  Access/Refresh 校验、Refresh 不轮换、注销幂等与撤销，以及历史用户密码初始化。
- TDD RED 为 services 测试镜像边界发现两个文件缺失，实际结果 `1 failed, 6 passed`；GREEN 后
  新服务测试与边界测试 `19 passed`，连同认证/Agent 路由的定向回归 `36 passed`，全部
  `tests/services` 为 `134 passed, 1 skipped`，后端全量回归 `483 passed, 2 skipped,
  127 warnings`。本批只新增确定性 SQLite/Fake Runtime 测试，不修改生产代码，也不证明
  真实 PostgreSQL、Checkpoint 并发或 LLM 质量。

## 27. 制度 Agent 应用服务职责拆分（2026-09-13）

- 原 `app/services/agent/sessions.py` 已按职责拆为会话创建 `sessions.py`、Checkpoint 与消息转换
  `messages.py`、脱敏工具审计 `audit.py`、Graph 执行与响应构造 `execution.py`。
- `app/routers/agent.py` 直接从四个职责模块导入；旧聚合入口不保留。执行成功和失败路径中的
  审计记录、数据库提交、错误映射顺序，以及 Prompt、Graph、HTTP 和数据库契约均未改变。
- 对应服务测试拆为四个镜像文件，并新增执行层可信范围、失败审计顺序和稳定错误映射测试。
  TDD RED 为目标模块缺失；GREEN 后 Agent 服务、包边界和 Router 定向回归 `27 passed`，
  全部 `tests/services` 为 `138 passed, 1 skipped`，后端全量回归
  `487 passed, 2 skipped, 130 warnings`。

## 28. V1 候选基线主审与评测来源修复（2026-09-14）

- 主 Agent 复核 `abceeca..1f2d092` 的 services、评测目录、Agent 职责拆分和发布文档提交；
  services 包边界、模块导入、旧 Python 导入和跨模块私有符号检查未发现行为偏离。
- 主审发现 `scripts/generate_enterprise_rag_eval_data.py` 的项目级 `source_files`
  仍指向四个已删除的根级 archive service，而原测试只校验文档级来源。
- 已按 `docs/implementation/services重构后企业评测来源路径修复实施计划.md`
  执行 TDD：RED 为 `1 failed, 9 passed`，GREEN 仅更新四个路径后为 `10 passed`。
- 主 Agent 完整回归为 `487 passed, 2 skipped, 130 warnings`；
  `compileall app tests scripts evals`、`git diff --check` 和旧 service Python 导入扫描通过。
- 修复计划、生成器、回归测试与交接/学习记录已提交为 `de868aa`。
  `v1.0.0` 保持指向 `1f2d092`，`v1.0.1` 指向 `de868aa`；两个标签均已推送。

## 29. NFR-021 文件日志按日期分层（2026-09-14）

- `LOG_FILE` 现在是日志基准路径；实际文件按日志记录的本地时间写入
  `<父目录>/YYYY/MM/YYYY-MM-DD<扩展名>`，无扩展名时使用 `.log`。
- `DatedRotatingFileHandler` 在下一条记录跨过日、月或年边界时切换文件，无需重启；控制台、
  request ID、格式、重复配置防护和当日按大小轮转保持不变。
- `LOG_BACKUP_COUNT` 只限制每个日期文件的大小轮转备份；不会自动清理其他日期目录。
  原有 `logs/app.log*` 未读取、迁移、重命名或删除。
- TDD RED 为新日期路径处理器尚不存在，测试收集失败；GREEN 后日志专项 `5 passed`。
  主 Agent 相关回归为 `11 passed`，完整后端回归为 `492 passed, 2 skipped, 130 warnings`；
  `compileall app tests scripts evals` 与 `git diff --check` 通过。
- 功能、测试、配置与文档已提交为 `52ff0c8` 并推送。

## 30. Router 函数签名与 HTTP 契约一致性（2026-09-14）

- 全 Router 审计确认四个返回注解与实际 Service 对象不一致：注册端点实际返回 `User`，项目创建、
  详情和更新实际返回 `Project`。函数注解已修正；FastAPI `response_model` 仍为 `UserRead` 或
  `ProjectRead`，外部字段过滤和 OpenAPI 契约不变。
- 第一轮路径审计发现项目 Router 的 27 个端点缺少 43 个显式路径参数；扩展到全部 Router 后又
  发现旧知识库文档 Router 的 4 个 `kb_id`，合计 31 个端点、47 个缺失声明，均已补齐。
- 补齐签名后，第二类审计确认 36 个端点共有 53 个路径参数只被依赖间接使用；现统一用 `_` 明确
  弃值，权限和业务范围仍只使用原有依赖产生的已验证对象。
- 项目文档列表的内部参数从 `status` 改为 `document_status`，通过 `Query(alias="status")` 保持
  外部查询名称不变，消除对 FastAPI `status` 导入的遮蔽。
- TDD RED 分别证明 4 个返回注解错误、31 个端点/47 个路径参数声明缺失、36 个端点/53 个路径参数
  未读取，以及 `status` 遮蔽；Router 全量回归为 `127 passed`，完整后端回归为
  `499 passed, 2 skipped, 130 warnings`；`compileall app tests scripts evals` 和
  `git diff --check` 通过。

## 31. 当前用户资料接口与前端显示名修复（2026-09-14）

- 新增受保护的 GET /auth/me：复用现有 Access Token 身份依赖，返回 UserRead 的公开资料；不新增密码字段、令牌字段或独立服务。
- 相邻 Vue 工作台在登录和刷新恢复会话后读取该接口；后端 name 用于页面显示的用户名，username 保留为账号。
- 前端 TDD：RED 为 API 方法缺失及登录后未请求资料；GREEN 后 API/Store 定向 68 passed、前端完整 124 passed，typecheck 与 build 通过。
- 后端路由测试 10 passed，覆盖当前资料、缺少认证和 Refresh Token 拒绝；运行中 8000 与 Vite 5173 代理的 OpenAPI 均暴露 /auth/me，匿名代理请求为 401。

## 32. FR-042 项目档案助手需求修正与架构草案（2026-09-14）

- 用户确认项目档案助手采用持久化会话 MVP：保留 FR-039 直接问答，新增目录与原文证据两个
  只读工具；会话绑定 `user_id + project_id + kb_id + thread_id`，复用 SQLite Checkpoint 机制与
  PostgreSQL 脱敏工具审计，不引入运行时多 Agent。
- FR-042 首次需求评审的 R1～R7 已裁决；第一次独立复审提出的 N1～N8 已由主 Agent 写入
  `docs/design/需求说明.md`，并追加 DEC-015～DEC-017。第二次独立复审因审查 Agent 用量上限
  中断，当前准确状态为“主审修正完成，独立二次复审待补”，不得写成评审通过。
- `docs/design/技术架构.md` 已按实际 `AgentSession`、AdminAgentRuntime、Checkpoint 消息投影、
  正式目录/检索、D5/D6 问答和项目删除代码形成 FR-042 主 Agent 草案。草案选择独立 Archive
  Graph 与 Checkpoint 文件、档案专用工具 DTO、每轮两个 Tool Call/60 秒预算、完成轮次标记、
  `DELETING` 会话和幂等线程清理；本机静态确认当前 `SqliteSaver` 提供 `delete_thread(thread_id)`。
- 本阶段只修改需求、决策、评审、架构和交接文档，没有创建迁移、模型、路由、Graph、测试或
  评测资产，也没有运行真实 PostgreSQL、Chroma、DeepSeek 或浏览器验证。下一步先由用户确认
  架构草案，或在审查 Agent 恢复后补独立需求复审；随后才进入数据库设计、API 设计、明确的
  Implementation Plan 和逐行为 TDD。

## 33. FR-042 架构草案主审评审（2026-09-14）

- 主 Agent 对 `docs/design/技术架构.md` §10.7 与受影响的 §12/§13/§16.1/§17/§18 完成一次
  核验式评审，结论为**有条件通过、本轮不予确认**，报告写入 `docs/review/FR-042-架构评审.md`。
- 核验成立的关键点：独立 Checkpoint 文件与 `JsonPlusSerializer(pickle_fallback=False)`、
  `messages.py` 硬编码 `search_company_policy` 不可复用、`retrieve_archive_chunks(top_k=8)`
  取值范围（但其可选阈值过滤后来被识别为 C1）、`list_formal_archives` 的四个筛选维度、空候选固定拒答、
  `delete_empty_project` 当前无行锁且不处理会话、上传路径已有 `Project.with_for_update()` 先例、
  `AgentSession` 当前无类型/项目/状态字段。
- 5 项必须修改：A1 项目行锁不得横跨 60 秒模型调用（会阻塞同项目上传并长期占用连接池，建议
  收窄为仅锁 AgentSession 行）；A2「单次决策多个 Tool Call 即整轮失败」与需求「一个或多个
  工具、单轮两次预算」及 AC-13 冲突；A3 证据工具结果投影未定义，`document_id`/`chunk_id`/
  分数可能进入模型上下文与审计；A4 `questions.py` 的 `_build_archive_prompt`/
  `_parse_model_decision` 为私有且公共入口自带检索，「复用 D5/D6」需先抽出公共判定函数；
  A5 `get_chat_model()` 为无参 `@lru_cache` 单例，「逐调用下传剩余预算」当前无法实现。
- 8 项建议（`turn_id` 缺列、Checkpoint 成功而 PG 提交失败的分支、失败轮的 HTTP 语义、
  会话上下文增长策略、存量 Agent 端点需按类型过滤、`has_source_evidence`/`document_ref`
  术语、`delete_thread` 在初次评审时未复现、目录投影建议共享查询不共享 DTO）；后续复现与裁决
  见第 34 节。
- 本轮只新增评审文档并更新需求评审与交接记录，未修改架构草案正文、未创建迁移/模型/路由/
  Graph/测试资产，也未运行真实 PostgreSQL、Chroma、DeepSeek 或 SQLite 集成验证。

## 34. FR-042 架构评审裁决与修正确认（2026-09-14）

- 用户确认主 Agent 对架构评审的逐项判断：A1 收窄普通消息锁到 AgentSession；A2 采用 Tool Call
  整批预算校验；A3 使用安全证据 DTO 与服务端引用映射；A4 抽出 FR-039/FR-042 共用 D5/D6
  判定；A5 将真实可限制等待的调用适配器纳入范围。完整失败轮返回 `200 + FAILED`，无法形成
  完整轮次的 Checkpoint/PG 保存失败返回 503。
- B1～B8 已写入架构下游边界：新增不存正文的 AgentTurn、选中引用映射、最近 10 轮/20,000
  码点模型上下文、制度存量会话类型迁移、字段证据派生口径，以及目录共享查询但不共享 DTO。
- 代码复核纠正两点：`retrieve_archive_chunks` 仍可能按非空配置执行 Reranker 阈值过滤，因此新增
  显式无统一阈值的 Top-8 回答候选入口；现有制度工具 `max_attempts=3`，FR-042 必须使用独立的
  `max_attempts=2` 重试策略。
- 当前项目解释器已确认 `SqliteSaver.delete_thread(thread_id)` 存在，并以内存 SQLite 连续删除
  不存在的线程两次成功；真实文件、存在的历史线程和项目删除联动仍须集成测试。
- 修正已写入需求 AC-01～20、BR-030～039、NFR-022～024、技术架构、DEC-018 和架构评审报告。
  FR-042 架构主审已通过并由用户确认；需求独立二次复审仍因审查 Agent 用量阻塞，数据库/API
  设计、Implementation Plan、迁移和代码实现尚未开始。

## 35. FR-042 架构修正复核（主审第二轮，2026-09-14）

- 对修正后的需求说明、技术架构 §7/§10.7/§12/§13/§15/§16.1/§17/§18、DEC-018 与代码基线做
  逐条复核：A1～A5、B1～B8、C1/C2 全部通过，需求/架构/DEC 三处口径一致，AC-01～20 在 §16.1
  全部有承接点，架构确认结论维持有效。复核记录写入 `docs/review/FR-042-架构评审.md` §10。
- 复核新发现 2 项数据库设计开工前必须补齐：
  D1 `AgentTurn` 只存轮次 UUID/会话/状态/时间，未定义与 Checkpoint 消息的关联键，而「最近 10 轮
  文本对投影」和「按 `turn_id` 成对出现」都依赖该映射；Checkpoint 失败或 PG 提交失败会打破
  位置对齐，不能靠顺序推断。建议规定 `HumanMessage.id = turn_id` 或为 AgentTurn 增加
  `user_message_id/assistant_message_id`（仍不保存正文）。
  D2 `AgentTurnCitation` 声明「随关联档案删除」，但 §10.6 文档删除流程与 §12.1 一致性表均未列入，
  仅项目删除（§10.7.5）写了清理；需补清理步骤并固化「历史正文不回溯改写、引用按当前可见性过滤」。
- 另 6 项建议：D3 `AgentTurn.RUNNING` 是否在轮次开始插入及崩溃遗留处置；D4「Graph 统一决定唯一
  一次重试」措辞；D5 `AgentExecutionStatus` 只有 COMPLETED、`AgentResponse`/`AgentMessageRead`
  的 `sources` 是 `SourceRead`，与 FR-039 引用形状不同，API 设计需另行定义；D6「最近 10 轮 /
  20,000 码点」为架构自定常量，缺需求或 DEC 依据；D7 请求级模型工厂与现有 `@lru_cache` 单例的
  连接复用策略；D8 Chroma/DeepSeek 请求级超时能力未真实探测，Fake 不能替代。
- 同时更正本报告自身：A3 原写「`retrieve_archive_chunks` 不施加 Reranker 阈值」不准确，过滤实际
  发生在 `_rerank_candidates()` L136-141，C1 的必要性由此确认。
- 本轮只新增评审与交接记录，未修改需求/架构/DEC 正文，未创建迁移、模型、路由、Graph、测试或
  评测资产，也未运行真实 PostgreSQL、Chroma、DeepSeek 或 SQLite 集成验证。

## 36. FR-042 MVP 范围收敛（2026-09-14）

- 用户确认当前目标是先完成“最小能通过的功能”，避免项目因低频故障和生产级并发要求继续膨胀。
  主 Agent 据此复审需求与架构，最新裁决追加为 DEC-019；DEC-018 全部废弃，DEC-017 的失败轮次
  持久化和工具批次部分废弃。
- MVP 保留：持久化档案助手会话、服务端 `user_id + project_id + kb_id + thread_id` 绑定、制度/
  档案会话类型隔离、目录和正式证据两个只读工具、CONFIRMED/删除阻断过滤、Top-8+D5/D6、当前
  回答引用、无依据拒答、输入校验与脱敏工具审计。
- MVP 简化：`AgentSession` 只新增 `agent_type` 与可空 `project_id`；不新增 AgentTurn、
  AgentTurnCitation 或 `DELETING`；复用同一受保护 Checkpoint 文件；每次模型决策最多一个 Tool
  Call、单轮最多顺序两个；最终基础设施失败统一返回 503；历史只返回完整用户/助手正文，不恢复
  历史引用。
- 延期项：SQLite/PostgreSQL 原子提交、跨组件动态 60 秒预算、完整失败轮次、历史引用恢复、同一
  会话并发消息和消息/项目删除并发一致性。上述限制不属于本地单实例 MVP 验收范围，后续只有真实
  故障、容量或部署需求出现时才重新立项。
- `FR-042-需求评审.md` 与 `FR-042-架构评审.md` 已追加最新复审，需求和架构主审均通过并由用户
  确认。当前只完成文档静态修订，未创建迁移、模型、路由、Graph、测试或评测资产，也未运行代码
  测试或真实 PostgreSQL、Chroma、DeepSeek、SQLite 验证。
- 下一步：先按 DEC-019/DEC-020 修订数据库设计和 API 设计，再产出 Implementation Plan；计划确认后才按
  Multi-Agent Development Workflow 和逐行为 TDD 开始实现。

## 37. FR-042 MVP 收敛复核（主审，2026-09-14）

- 复核对象：DEC-019 生效后的需求说明 FR-042/BR-035～039/NFR-022～024/§12.5/§16、技术架构
  §1/§3/§7/§10.7/§12/§15/§16.1/§17～§19，并与 `app/models/agent.py`、`app/schemas/`、
  `app/services/archive/` 和《接口设计》既有约定交叉核对。复核记录写入
  `FR-042-需求评审.md` §11 与 `FR-042-架构评审.md` §12。
- 复核结论：MVP 收敛成立，A1～A5 与 B1～B8 的落点未被回退；D1、D2、D3、D6、D7 因方案取消而
  自然消解，D4 措辞已修正，D8 随 NFR-024 重写降级为基线记录项。数据库设计与接口设计当前不含
  任何 FR-042 内容，与「尚未开始」一致。
- 待修正（3 项）：
  - 需求 C1（硬冲突）：AC-FR-042-03 要求「复用 FR-039 的引用 Schema 且不新增第二套结构」，
    但 FR-039 的 `ArchiveRetrievalItemRead` 必含 `chunk_id`/`document_id`/`score`，
    与 BR-038「用户可见响应不得包含持久化标识或检索分数」互相排斥。建议改为复用其
    「文件、位置、摘录」字段定义。API 设计前必须消除。
  - 架构 C2：§1 引言 L17～L19 仍写「独立的…Checkpoint 文件」，与 DEC-019、§3.1、§10.7.4、
    §17「共用同一受保护 Checkpoint 文件」矛盾。
  - 架构 C3：§16.1 AC-12 行 L919 仍写「独立文件」，同上。
- 需在接口契约定稿前确定（3 项）：C4 模型越界（多 Tool Call/第三次调用）应用 4xx 而非 503；
  C5 档案助手回答状态枚举与 FR-039 `ArchiveAnswerStatus`、现有 `AgentExecutionStatus` 的关系；
  C7 模型输入投影与历史接口是否使用同一配对规则（以及上下文无上限需登记为已知限制）。
- 两条实现前提：`get_chat_model()` 当前是无参 `@lru_cache` 单例且未设 `timeout`，「复用现有可配置
  超时」需先做参数化改造；档案入口不能复用 `AgentSessionCreate`（其 `kb_id` 为必填），须按
  《接口设计》§2.1 只接受路径中的 `project_id`。
- 本轮只新增评审与交接记录，未修改需求/架构/DEC 正文，未创建迁移、模型、路由、Graph、测试或
  评测资产，也未运行代码测试或真实 PostgreSQL、Chroma、DeepSeek、SQLite 验证。

## 38. FR-042 MVP 复核问题裁决（2026-09-14）

- C1 按推荐方案闭环：档案助手使用脱敏引用 DTO，复用 FR-039 的 `filename`、`location_type`、
  `location_start`、`location_end`、`excerpt` 字段名称与语义，但不复用含 UUID 和分数的完整 DTO。
- C2/C3 已清除技术架构中的“独立 Checkpoint 文件”残留；制度与档案 Agent 使用独立 Graph 和工具，
  复用同一受保护 Checkpoint 文件，并由全局唯一 `thread_id` 与会话类型隔离。
- C4 确认模型 Tool Call 越界使用 `503 ARCHIVE_AGENT_MODEL_OUTPUT_INVALID`，真实依赖失败使用
  `503 ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`；不使用会错误归因给客户端的 4xx。
- C5 对外复用 `ArchiveAnswerStatus`：目录/有据为 `ANSWERED`，无依据为
  `REFUSED_NO_EVIDENCE`；`CATALOG` 仅为 Graph 内部状态。档案入口必须使用专用 Schema，不能复用
  要求客户端提交 `kb_id` 的 `AgentSessionCreate`。
- C6/C7 已闭环：没有本轮工具依据不得陈述档案事实；历史与模型输入使用同一完整轮次投影，忽略
  ToolMessage 和孤立消息；上下文不设独立上限作为短会话 MVP 已知限制。
- `get_chat_model()` 的固定可配置请求超时已列为 Implementation Plan 必须包含的最小改造，不引入
  跨组件动态总预算。裁决追加为 DEC-020。
- 当前需求与架构已通过，可以进入数据库设计与 API 设计；仍未授权直接编写迁移、模型、路由或
  Graph 代码。本轮没有运行代码测试或真实服务验证。

## 39. FR-042 数据库设计草案（2026-09-14）

- 已按用户要求在实际修改前创建并切换到分支 `codex/fr-042-archive-agent-mvp`；
  创建分支时的既有未提交改动原样保留。
- 已核对 `app/models/agent.py`、`app/models/project.py`、`0003_agent_api.py`、当前迁移头
  `0010_account_auth`（文件 `0010_account_password_authentication.py`）和项目删除服务。代码基线确认：现有
  `AgentSession` 没有会话类型/项目字段，工具日志外键没有级联删除，项目删除尚未
  处理 Agent 会话或 Checkpoint。
- `docs/design/数据库设计.md` 已形成 FR-042 最小草案：只为 `agent_sessions`
  增加 `agent_type` 和可空 `project_id`，以检查约束和 `(project_id, kb_id)` 复合外键
  防止类型错配与项目/知识库错绑；工具日志表只把会话外键改为
  `ON DELETE CASCADE`。不新增 AgentTurn、历史引用、会话删除状态或第二份 Checkpoint。
- 草案为后续前向迁移预留 `0011_archive_agent_scope`，包括存量会话回填
  `POLICY`、档案会话约束、工具日志级联和本地降级前置条件；迁移文件尚未创建。
- 删除空项目的数据顺序已固定为：锁定 Project 并读取当前项目的 ARCHIVE 线程
  → 幂等清理 Checkpoint → 删除 Project 并级联删除档案会话/日志。KnowledgeBase 与
  `project_id IS NULL` 的制度会话保留；不承诺 PostgreSQL/SQLite 原子性或并发删除。
- 当前仅完成文档静态设计与 diff 检查，未修改 SQLModel，未创建迁移，也未运行数据库、
  Chroma、DeepSeek 或 Checkpoint 验证。待用户确认本数据库设计后，下一步只进入
  FR-042 API 设计，仍不直接实现代码。

## 40. FR-042 数据库设计主审评审（2026-09-14）

- 复核对象：`数据库设计.md` §5、§6.1、§6.11（L416～L467）、§11（L591～L624）、§12（L626～L660），
  并与需求说明 FR-042、技术架构 §7/§10.7、DEC-019、现有 SQLModel 与 `0003`～`0010` 迁移、
  `tests/test_migration_service.py`、`tests/models/test_schema_comment_contract.py` 交叉核对。
  评审记录写入 `docs/review/FR-042-数据库设计评审.md`。
- 结论：**方案方向成立，但有 3 项必须在迁移落地前修正**，修正后可进入 API 设计。
- 必须修正：
  - **DB-1（确定的错误）** 数据库设计 L596 与 handoff L692 写「`down_revision` 指向
    `0010_account_password_authentication`」，但该迁移的 revision id 实际是 `0010_account_auth`
    （`0010_account_password_authentication.py` L10，受 Alembic VARCHAR(32) 限制取短名；
    实施计划 L198、验收报告 L112、handoff L116 与 `test_migration_service.py` L77 均用短名）。
    照抄会导致 `alembic upgrade` 报 `Can't locate revision`。
  - **DB-2** `agent_tool_call_logs.agent_session_id` 的外键在 `0003_agent_api.py` L57 是匿名创建的，
    PostgreSQL 实际名为 `agent_tool_call_logs_agent_session_id_fkey`；§11 第 9 步未写 drop 目标，
    迁移会在第一步失败。
  - **DB-3** `tests/models/test_schema_comment_contract.py` L45～L50 断言注释映射键集合与 SQLModel
    列集合完全相等，而 L32～L38 只合并 0006/0008/0010。给 `AgentSession` 加两列会使该测试必红；
    必须在 0011 提供两列注释，**并同时修改该测试文件**加载 0011。
- 建议 4 项：DB-4 SQLite 默认不启用外键，`test_migration_service.py` 未设 `PRAGMA foreign_keys=ON`，
  §12 的两条级联/复合外键验收在该链路上不成立，须挂到启用 PRAGMA 的测试或标注为 PostgreSQL 门；
  DB-5 复合外键建议显式写 `MATCH SIMPLE`；DB-6 `0011_archive_agent_session_scope` 恰为 32 字符，
  建议缩短为 `0011_archive_agent_scope`；DB-7 Project 行锁跨越外部 Checkpoint 删除的持有窗口
  需在架构或数据库设计中显式写明。
- 核验成立：`projects` 上 `uq_projects_id_kb_id` 在 `0005` L81 已存在，复合外键引用目标合法；
  `ON DELETE CASCADE` 有 `0005` L107 先例；制度会话因 `project_id IS NULL` 在结构上不可能被级联
  清理，比服务层约定更强；VARCHAR+CHECK 符合 §5 约定且有 `app/models/project.py` 先例；
  先加列回填 `POLICY` 再加 CHECK 的顺序可执行；新增列范围与 DEC-019 完全一致，未引入
  AgentTurn、历史引用、`DELETING` 或第二份 Checkpoint。
- 本轮只新增评审记录与本节，未修改数据库设计正文、SQLModel 或迁移，未运行任何代码测试、
  数据库迁移或真实服务验证。

## 41. FR-042 数据库设计评审修正（2026-09-14）

- DB-1 已修正：下一迁移 revision id 缩短为 `0011_archive_agent_scope`（长度 24），
  `down_revision` 明确指向真实 revision id `0010_account_auth`，并区分其文件名
  `0010_account_password_authentication.py`。
- DB-2 已修正：迁移顺序要求 PostgreSQL 先用 Inspector 按受限列和引用表精确
  找到旧匿名外键，当前预期默认名为
  `agent_tool_call_logs_agent_session_id_fkey`；删除后以具名外键
  `fk_agent_tool_logs_session_cascade` 和 `ON DELETE CASCADE` 重建。SQLite 由 batch alter 加
  naming convention 处理反射到的匿名约束。
- DB-3 已修正：`0011` 必须为 `agent_type/project_id` 提供注释映射，并在同一
  TDD 切片修改 `tests/models/test_schema_comment_contract.py` 合并 `0011`。该既有测试的
  修改已列为 Implementation Plan 必须项。
- DB-4 采用最小双层验证：SQLite 测试连接显式开启 `PRAGMA foreign_keys=ON`，用于
  确定性 TDD 验证错绑拒绝和级联语义；完成前再用目标 PostgreSQL 验证真实迁移头、
  外键名、`MATCH SIMPLE` 与 `ON DELETE CASCADE`。SQLite 通过不替代 PostgreSQL 门。
- DB-5～DB-7 已接受：复合外键 DDL 显式写 `MATCH SIMPLE`；revision id 采用短名；
  Project 行锁持有到 PostgreSQL 提交，锁窗口内同项目会话创建与上传阻塞，但锁不得
  扩大到模型调用或普通消息执行。
- 数据库设计的主审必修项已闭环；待用户确认后可进入 FR-042 API 契约设计。
  本轮仍未修改 SQLModel、迁移或测试代码，也未运行数据库、Chroma、DeepSeek 或 Checkpoint 验证。

## 42. FR-042 API 契约草案（2026-09-15）

- 用户已确认 FR-042 数据库设计；当前分支仍为 `codex/fr-042-archive-agent-mvp`。
- `docs/design/接口设计.md` 已新增 FR-042 最小契约，只包含四个项目嵌套端点：
  创建会话、发送消息、读取完整轮次历史和读取脱敏工具日志。不新增会话列表、
  详情、重命名、用户删除、流式响应或工具直调端点。
- 创建请求固定为 `extra="forbid"` 的空 JSON 对象 `{}`，以便稳定拒绝客户端提交
  `user_id/project_id/kb_id/thread_id/agent_type`。创建响应只返回会话 ID、项目 ID
  和时间，不暴露内部知识库、Graph 线程或会话类型。
- 档案助手会话使用 `session_id + user_id + project_id + kb_id + ARCHIVE` 整体查找；
  已授权项目下任一错配统一返回 `404 ARCHIVE_AGENT_SESSION_NOT_FOUND`。既有制度入口
  必须反向固定过滤 `POLICY`，不能使用 ARCHIVE 会话打开 Checkpoint。
- 消息请求固定 `message`，按 `CRLF/CR → LF`、去除首尾 Unicode 穽白、`1..2000`
  码点的顺序校验。响应只使用 `ArchiveAnswerStatus`，当前证据引用只含轮次内单调编号
  `S1..S16`、文件名、位置和摘录；单次证据工具仍固定 Top-8，两次调用不重用编号。
  目录临时引用同理为轮次内 `D1..D40`。两类响应均不包含文档/Chunk ID、分数或 `document_ref`。
- 历史接口返回按 Checkpoint 顺序的 `USER/ASSISTANT` 完整轮次，ToolMessage 与不完整轮次
  均隐藏；本期不恢复历史引用，因此每个历史项的 `citations=[]`。历史与工具日志都不
  分页，仅面向本地短会话 MVP。
- 工具日志顶层复用 `AgentToolCallLogRead`，但参数/结果摘要已固定白名单；不存查询原文、
  筛选值、文件名、摘录、资源 ID、分数、Token、提示词或异常原文。同一 `tool_call_id`
  的传输重试只形成一条审计。
- 稳定错误新增 `404 ARCHIVE_AGENT_SESSION_NOT_FOUND`、
  `503 ARCHIVE_AGENT_MODEL_OUTPUT_INVALID` 和 `503 ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`。
  目录空与无证据均是 `200`，分别使用固定目录空文案和 `REFUSED_NO_EVIDENCE`。
- `DELETE /projects/{project_id}` 路径不变；空项目只在 ARCHIVE Checkpoint、会话、日志和
  Project 都清理后返回 `204`，KnowledgeBase 和 POLICY 会话保留。
- API 文档§4.4 的 FR-039 Citation 示例原先与实际 Schema 不一致：文档误写了
  `citation_id` 且遗漏 `chunk_id/score/reranker_score`。本轮已按当前
  `ArchiveQuestionResponse.citations: list[ArchiveRetrievalItemRead]` 修正示例，并明确 FR-042 只复用
  其文件/位置/摘录字段，不复用含内部标识与分数的完整 DTO。
- FR-042 AC-01～AC-19 已全部映射到第 13 节。当前仅完成 API 文档草案和静态自审，未创建
  `app/schemas/archive_agent.py`、Router、Service、Graph、迁移或测试，也未运行 PostgreSQL、
  Checkpoint、Chroma 或 DeepSeek 验证。待用户确认 API 契约后，下一步是产出 FR-042
  Implementation Plan，计划确认前仍不进入代码 TDD。

## 43. FR-042 API 契约主审评审（2026-09-15）

- 评审记录：`docs/review/FR-042-接口设计评审.md`（主 Agent 主审，不替代需求侧独立复审）。
- 核验成立：`ArchiveAnswerStatus` 两值、`ArchiveRetrievalItemRead` 必含持久化标识与分数、
  `uq_agent_tool_logs_session_call` 真实存在且 `tool_name` 为字符串、§4.4 FR-039 引用示例
  已与代码逐字段一致且 `citation_id` 仅出现在第 13 节、AC-01～AC-19 全部有落点、
  项目层 `404 PROJECT_NOT_FOUND`/`403 PROJECT_FORBIDDEN` 与既有依赖一致。
- 必须在 API 定稿前补齐 4 项：
  - **API-1**：`judge_archive_answer` 的失败映射未定义。FR-039 冻结契约与现有实现
    （`questions.py` L245～L261、P14-D5 L113/L130/L131）都把判定层失败映射为
    `ARCHIVE_ANSWER_UNAVAILABLE`，而 AC-16/§13.6 要求 FR-042 返回
    `ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`。共用函数时会直接违反 AC-16。
  - **API-2**：两次证据调用时传给判定层的候选集合与 `S` 编号基准未定义；判定层按传入
    列表位置编号并反查（`questions.py` L44～L53、L145、L274～L278），取并集还是最后一次
    未写明会导致引用静默错位。
  - **API-3**：模型零工具调用却陈述档案原文事实时，§13.3 的"普通回答"分支会返回
    `200 ANSWERED`，AC-06 后半句没有判别机制、无法写成测试。
  - **API-4**：写回 Checkpoint 的最终 AIMessage 是判定层文本还是工具循环文本未定义，
    影响 AC-01/AC-07 与 BR-032。
- 建议 5 项：`answer` 正文 `[S1]` 标记来源、`document_ref` 两套 `D` 命名空间、参数无效
  调用的 `FAILED` 审计与 `error_code` 白名单、不分页接口的条数上限声明、会话响应不含
  `agent_type` 的理由。
- 文档同步项：需求 L1108～L1109 与数据库设计 L678 仍写"数据库/API 尚未开始/尚未定稿"；
  接口设计 L599 与 §4.4 的 `chunk_id` 示例口径不同。
- 本轮未修改任何设计正文、代码、迁移或测试，未运行真实服务；等待用户就 API-1～API-4 裁决。

## 44. FR-042 API 契约主审修正（2026-09-15）

- 已按 `docs/review/FR-042-接口设计评审.md` 的 API-1～API-9 修正需求、架构、数据库状态和
  API 设计正文；评审报告 §9 复核结论为 **API 契约通过主审，待用户确认**。
- API-1：公共 D5/D6 判定函数只返回结果或中性失败；FR-039 继续映射
  `ARCHIVE_ANSWER_UNAVAILABLE`，FR-042 映射 `ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`，
  不改变既有 FR-039 对外契约。
- API-2：正常完成时只由最后一次成功工具调用决定最终结果。证据分支只把该次原始 Top-8
  按原顺序交给公共判定层并重新编号 `S1..S8`，不合并、去重或重排两次调用结果；较早结果
  只用于下一次工具选择或查询调整。
- API-3/API-4：零个成功工具调用一律固定拒答；HTTP `answer` 与 Checkpoint 最终 AIMessage
  完全相同。证据正文来自判定层，目录正文来自确定性目录投影，工具选择循环自由文本不持久化。
- API-5/API-6：不向回答正文注入 `[Sn]`，不修改 FR-039；FR-042 公共 Citation 去除
  `citation_id`。目录调用内引用改为 `A1..A20`，证据调用内为 `S1..S8`，与判定 Prompt 内部
  证据文档 `D` 分组互不相通。
- API-7～API-9：已注册工具参数无效计入调用额度并写一条 `FAILED` 脱敏审计；安全错误码固定
  四值。历史和工具日志不分页且不设条数上限，登记为短会话限制。创建响应不含 `agent_type`
  的理由已写明。
- 同步修正接口设计中的 64 位 `chunk_id` 示例，以及需求、数据库设计和学习计划的阶段状态。
- 本轮只修改设计与状态文档，未创建 `0011`、SQLModel、Schema、Router、Service、Graph 或测试，
  未运行 PostgreSQL、SQLite Checkpoint、Chroma、DeepSeek 或代码测试。
- 下一步：等待用户确认本版 API 契约；确认后先产出可执行的 FR-042 Implementation Plan，
  计划再次确认前不得进入代码 TDD。

## 45. FR-042 API 契约主审最终复核（2026-09-15）

- 复核记录：`docs/review/FR-042-接口设计评审.md` §10。结论：**MVP API 契约主审通过，确认可接受。**
- API-1～API-9 逐条核验通过：中性错误与双入口映射、只认最后一次成功证据调用并按原顺序重建
  `S1..S8`、零成功工具调用一律拒答、HTTP `answer` = Checkpoint 最终 AIMessage、不注入 `[Sn]`
  且不暴露 `citation_id`、三个编号命名空间互不相通、失败审计与四个安全错误码、
  不分页接口边界、会话响应不含 `agent_type` 的理由。
- 本轮新增核验成立：`judge_archive_answer` 的编号基准链路自洽（`questions.py` L44～L61、
  L139～L146、L274～L278，`candidate_count ≤ 8` 消除 `S1..S16` 越界）；固定拒答文案与
  `questions.py` L25 `_REFUSAL_ANSWER` 逐字一致；`AgentToolCallLogRead` 不含会话归属字段；
  `delete_empty_project`（`project/management.py` L178～L201）明确保留知识库，与 §13.7
  「KnowledgeBase 和 POLICY 会话不在删除范围」一致；数据库设计 DB-1～DB-7 已全部落位。
- 本次复核当时发现两项缺口（现已在 §46 闭环）：
  - **P-1**：传给公共判定层的 `question` 未定义——固定为用户本轮规范化消息，而非模型改写的检索 query。
  - **P-2**：空候选短路未写入 API 契约——0 候选时不得调用判定层，否则 DeepSeek 抖动会把
    确定性 `200 REFUSED_NO_EVIDENCE` 变成 `503`。
- 建议三项：P-3 目录正文确定性模板（内容字段、排序、禁止 `A{n}`/`document_ref`）、
  P-4 模型正文自带 `[Sn]` 的处理、P-5 需求 §15 登记「历史与工具日志读取不分页且不设条数上限」。
- Implementation Plan 必须显式包含：P-1/P-2 的 §13.3 补齐、P-3 模板、`0011_archive_agent_scope`
  迁移与 PostgreSQL 集成门、`tests/models/test_schema_comment_contract.py` 并入 `0011` 注释的必改项、
  `get_chat_model()` 固定可配置请求超时改造、`questions.py` 抽出公共 `judge_archive_answer`
  且不改变 FR-039 对外契约。
- 本轮只写评审与交接记录，未修改 `接口设计.md`、需求、技术架构、数据库设计或任何代码；
  未运行 PostgreSQL、SQLite Checkpoint、Chroma、DeepSeek 或代码测试。

## 46. FR-042 API 最终缺口补齐（2026-09-15）

- 已在进入 Implementation Plan 前补齐最终复核 P-1～P-5，避免把未冻结的可观察行为带入实现计划。
- 公共判定层的 `question` 固定为本轮规范化用户消息；模型改写的证据检索 `query` 只影响候选获取。
- 证据工具返回 0 个候选时不调用公共判定模型，直接短路为固定
  `REFUSED_NO_EVIDENCE` 和空引用；至少 1 个候选才进入 D5/D6。
- 非空目录正文使用固定模板：按既有目录稳定顺序逐行输出文件名与五字段的
  `value/source/has_source_evidence`；不输出 `A{n}`、`document_ref`、确认时间、UUID、版本或分数。
- 为保持 FR-039 公共判定输出不变，服务端不注入、剥离或拒绝模型自行写入正文的 `[Sn]`；
  客户端不得解析该文本，结构化 `citations` 数组是唯一可信引用。
- 需求 §15 已同步历史与工具日志读取不分页、不设条数上限的短会话限制。
- 评审报告 §11 复核 P-1～P-5 全部闭环。当前仍只完成文档设计，未创建或修改 FR-042 代码、
  迁移和测试，也未运行 PostgreSQL、SQLite Checkpoint、Chroma 或 DeepSeek。
- 下一步：等待用户确认最终 API 契约；确认后产出 FR-042 Implementation Plan。计划必须覆盖
  `0011_archive_agent_scope` 与 PostgreSQL 集成门、注释契约测试并入 `0011`、
  `get_chat_model()` 固定可配置超时、公共 `judge_archive_answer` 且保持 FR-039 对外契约，
  以及所有已冻结 API 行为的 RED/GREEN 切片。计划再次确认前不进入代码 TDD。

## 47. FR-042 API 冻结前落点核验（2026-09-15）

- 核验记录：`docs/review/FR-042-接口设计评审.md` §12。结论：**P-1～P-5 全部闭环，API 契约达到可冻结状态。**
- P-1：§13.3 L920～L921 与技术架构 §10.7.3 L720～L722 一致固定判定层 `question` 为用户本轮规范化
  消息；§13.9 的 AC-03/05/06/19 行已同步。
- P-2：§13.3 L922～L923 与 `questions.py` L201～L224 既有短路同构，且保证 `candidate_count ≥ 1`，
  同时保护 `_parse_model_decision` 的编号校验。
- P-3：模板五字段与 §13.6 L988～L992 名称/顺序一致；排序沿用 `catalog.py` L135 的
  `confirmed_at DESC, id DESC`；禁止 `A{n}`、`document_ref`、`confirmed_at`、UUID、版本、分数。
- P-4/P-5：`[Sn]` 原样透传且不作为公开契约、`citations` 为唯一可信引用；需求 §15 L1106～L1107
  登记不分页与不设条数上限。
- 设计文档已无「普通回答/自由回答」残留成功分支，也无 `S1..S16`/`D1..D40` 旧编号口径。
- 遗留点（不违反任何 AC，建议 Implementation Plan 冻结前定调）：
  - **W-1**：目录正文模板无分页信息，「当前目录结果共 {当前页条目数} 份」可被读成总数；
    `catalog.py` L102～L104 已返回 `page/page_size/total`，模型又可控制 `page`。三选一收口：
    补页码与总数、只改措辞、或固定 `page=1`。
  - **W-2**：`request_id` 仅以 §13.3 示例出现，建议在 §13.8 补 `ArchiveAgentResponse` 字段清单。
  - **W-3**（可选）：handoff §42 L775～L777 的 `S1..S16`/`D1..D40` 已被 §44 取代，可加一句指针。
- 本轮只写评审与交接记录，未修改 `接口设计.md`、需求、技术架构、数据库设计或任何代码；
  未运行 PostgreSQL、SQLite Checkpoint、Chroma、DeepSeek 或代码测试。

## 48. FR-042 API 冻结前分页与 DTO 收口（2026-09-15）

- W-1 采用完整分页信息方案：非空目录正文固定显示 `page`、当前页条目数和 `total`，均来自
  最后一次成功目录工具结果；页内编号从 `1` 重新开始且不表示全局序号。模型仍可控制
  合法 `page/page_size`，没有收紧为只读第一页。
- W-2 已补齐 `ArchiveAgentResponse` 字段清单：仅包含 `session_id`、`answer_status`、`answer`、
  `citations` 和 `request_id`，禁止范围、Graph 和检索内部字段。
- §42 中的 `S1..S16/D1..D40` 是当时 API 草案的历史记录，已由 §44、§46 及当前正式接口设计
  替代；Implementation Plan 和代码不得引用旧编号规则，必须使用目录调用内 `A1..A20` 与证据
  调用内 `S1..S8`。
- 本轮仍只修改设计、评审与交接文档，未修改代码、迁移或测试，未运行真实服务。
- 下一步：等待用户确认最终 API 契约；确认后开始编写 FR-042 Implementation Plan。

## 49. FR-042 Implementation Plan 草案（2026-09-15）

- 用户已确认 §48 收口后的 FR-042 API 契约。主 Agent已创建
  `docs/implementation/FR042-项目档案助手MVP实施计划.md`，当前为待用户确认草案；未开始代码 TDD。
- 计划按实际代码拆为 P00 基线门、P01 会话类型/迁移、P02 公共 Top-8/D5-D6/模型超时、
  P03 Schema/会话创建/双向隔离、P04 两个只读工具、P05 Archive Graph/Checkpoint、
  P06 四端点/历史/审计、P07 项目删除联动，随后执行后端集成、相邻 Vue、真实 DeepSeek 固定集和交付文档。
- 每个切片严格执行实际 RED → 最小 GREEN → REFACTOR；实现任务边界清晰后优先交给 GPT-5.6 Luna，
  GPT-5.6 Sol 负责计划、RED/GREEN 证据和 Git diff 主审。
- 计划显式纳入 `0011_archive_agent_scope`、PostgreSQL 集成门、`0011` 注释合并、
  `get_chat_model()` 固定可配置超时、FR-039/FR-042 共用无统一阈值 Top-8 与
  `judge_archive_answer`、请求内原始候选隔离、目录分页模板及 AC-01～AC-19。
- 相邻 Vue 仓库当前在 `feature/ui-feedback-and-layout` 且存在未提交改动；Vue 修改前必须先由用户
  确认这些改动的归属和目标基线，再建立对应 `codex/fr-042-archive-agent-mvp` 分支。不得自动 stash、
  reset 或夹带现有改动。
- 真实 LLM 质量按仓库既有 `evals/archive/` 结构执行：复用 12 题，新增至少 2 个目录题和 2 组
  两轮追问；Mock 只证明确定性控制流，未完成真实 DeepSeek 固定集不得宣称 FR-042 质量通过。
- 当前下一步：用户确认本 Implementation Plan 后，从 P00 开始；在此之前不创建迁移、测试或代码。

## 50. FR-042 P00 基线门（2026-09-15）

- 用户已确认 `docs/implementation/FR042-项目档案助手MVP实施计划.md`。当前后端分支仍为
  `codex/fr-042-archive-agent-mvp`，P00 未创建任何 FR-042 功能代码、迁移或测试。
- 第一次后端全量测试得到 `1 failed, 488 passed, 2 skipped`。唯一失败为
  `tests/evals/archive/test_project_grounded_materials.py::test_project_grounded_candidates_trace_to_declared_source_lines`：
  FR-040 项目有据评测仍把 `docs/design/接口设计.md` 的删除契约标为旧行号 `620～628`，实际原文已因
  既有设计扩展移动到 `642～650`，属于可解释的基线资料定位漂移，不是 FR-042 功能失败。
- 按 Multi-Agent Workflow 将该机械修正交给 GPT-5.6 Luna：先实际复现 RED，再只修改
  `evals/archive/datasets/archive-question-project-grounded.json` 中该样本的候选定位和三组
  `expected_evidence` 行号；主 Agent核对新位置逐行对应原摘录，未接受源码或设计正文附带修改。
- 修正后后端全量测试为 `489 passed, 2 skipped, 130 warnings`，耗时 96.57 秒；
  `python -m compileall -q app tests evals scripts` 通过。warning 为既有 Starlette/httpx2 与
  Pixie/jsonpickle 弃用提示，本轮未处理。
- 静态基线：Alembic head 为 `0010_account_auth`；`AgentSession` 仍只有
  `created_at/id/kb_id/thread_id/updated_at/user_id`；OpenAPI 尚无
  `/projects/{project_id}/agent-sessions` 路由，符合“FR-042 尚未实现”。
- 本轮未运行 PostgreSQL 真实迁移、共享 SQLite Checkpoint 读写、Chroma、真实 DeepSeek 或浏览器；
  P00 通过不能冒充这些后续门槛。
- 相邻 Vue 仓库仍在 `feature/ui-feedback-and-layout` 且存在未提交改动；它不阻塞后端 P01，但 Vue
  阶段开始前必须由用户确认其归属和目标基线，不得自动 stash/reset。
- 下一步学习检查点：用户确认后进入 FR042-P01，只实施 `AgentSession` 类型/项目绑定和
  `0011_archive_agent_scope` 迁移的 RED/GREEN，不提前开始公共判定、Graph、Router 或 Vue。

## 51. FR-042 P01 会话范围与数据库迁移（2026-09-15）

- 当前分支为 `codex/fr-042-archive-agent-mvp`。按 Multi-Agent Workflow 将 P01 的模型、迁移和测试
  交给 GPT-5.6 Luna 实现，主 Agent复核 Git Diff、修正任务边界并完成最终验收；未提交、未推送。
- RED 已实际形成：模型测试因缺少 `AgentType` 无法收集，迁移 head 仍为 `0010_account_auth`，注释契约
  未合并 `0011`。GREEN 新增 `AgentType.POLICY/ARCHIVE`、`AgentSession.agent_type/project_id`、
  `0011_archive_agent_scope` 以及数据库级类型/项目组合、复合项目知识库外键、项目索引和工具日志级联。
- SQLite 相关模型与迁移测试覆盖存量会话回填 `POLICY`、非法类型、POLICY/ARCHIVE 项目组合、跨知识库
  错绑、项目删除级联、制度会话和知识库保留，以及有/无 ARCHIVE 会话时的安全 downgrade。
- 真实 PostgreSQL 使用随机且预先确认不存在的临时 schema 执行 `0001` 到 `0011` 全链迁移。第一次复测
  揭示旧 `command.check()` 会把历史注释、Enum/VARCHAR 和旧约束元数据差异误当作 P01 失败，故门禁
  收敛为本期结构与行为的直接断言；第二次复测又证明 `pg_get_constraintdef()` 会省略默认
  `MATCH SIMPLE` 文本，最终改由 `pg_constraint.confmatchtype = 's'` 验证真实语义。
- 最终真实 PostgreSQL 门为 `1 passed`：确认 revision、目标列、具名 CHECK、项目索引、复合外键
  `MATCH SIMPLE ON DELETE CASCADE`、工具日志级联、四类非法插入拒绝、合法 POLICY/ARCHIVE 写入及
  项目删除后的级联/保留行为。每次临时 schema 均在所有权匹配后删除，未修改现有业务 schema。
- 最终相关测试为 `14 passed, 1 skipped`；跳过项仅因常规本地进程未注入 `POSTGRES_TEST_URL`，已有上述
  独立真实 PostgreSQL 通过证据。后端全量为 `496 passed, 2 skipped, 130 warnings`，`compileall` 通过；
  warning 仍为既有 Starlette/httpx2、Pixie/jsonpickle 提示。
- 下一步学习检查点：用户确认后进入 FR042-P02，只实施共享模型固定超时、无统一阈值 Top-8 回答候选、
  D5/D6 公共判定和 FR-039 不回归测试，不提前创建 P03 会话 Router、Graph 或 Vue。

## 52. FR-042 P02 共享模型、候选与证据判定基础（2026-09-15）

- 当前分支仍为 `codex/fr-042-archive-agent-mvp`。P02 先由 GPT-5.6 Luna 写测试并实际得到
  `9 failed, 40 passed`：失败分别来自超时配置、模型工厂参数、无阈值候选入口、FR-039 入口迁移和
  公共判定结果/中性异常缺失，属于有效 RED。
- GREEN 增加 `deepseek_request_timeout_seconds`（默认 30 秒且必须为正数）并同步 `.env.example`；
  `get_chat_model()` 保留无参缓存入口和客户端默认重试语义，同时允许后续 Archive Runtime 显式取得
  `max_retries=0` 的独立缓存实例，两种入口都使用固定请求超时。
- `retrieve_archive_answer_candidates` 复用正式文档范围、Top-30 Chroma 候选、本地 Reranker 和稳定
  排序，只固定返回原始 Top-8，不使用公开 `archive_reranker_score_threshold`；既有
  `retrieve_archive_chunks` 仍保留公开阈值和 `top_k` 契约。
- `questions.py` 抽出不可变 `ArchiveAnswerDecision`、中性 `ArchiveAnswerJudgmentError` 和
  `judge_archive_answer`。公共函数只消费调用方问题、候选和模型，复用原 D5/D6 Prompt、严格 JSON
  解析与引用编号；不检索、不构造模型、不包含 HTTP 错误码。FR-039 改用无阈值候选入口，空候选仍
  不调用模型，公共判定失败仍映射既有 `503 ARCHIVE_ANSWER_UNAVAILABLE`。
- 实现 Agent 在 GREEN 后续验证阶段触发子 Agent 用量上限，主 Agent按计划中的无子 Agent 退化规则接管
  Diff 审查和测试，没有扩大文件或需求范围。相关配置/模型/检索/问答测试为 `49 passed`；FR-039
  Router 与 D5/D6 固定资料契约测试为 `16 passed`。
- 最终后端全量测试为 `505 passed, 2 skipped, 144 warnings`，`python -m compileall -q app tests`
  通过。warning 为既有 Starlette/httpx2 与 Pixie/jsonpickle 弃用提示；本轮未调用真实 DeepSeek，
  Mock 只证明确定性控制流，不能作为 FR-042 回答质量通过证据。
- 下一步学习检查点：用户确认后进入 FR042-P03，只实施 HTTP Schema、档案会话创建和双向五要素隔离，
  不提前创建 P04 只读工具、Graph、Checkpoint 会话执行或 Vue。

## 53. FR-042 P03 路由切片歧义修正（2026-09-15）

- 用户确认 P03 不注册空壳端点：本切片 OpenAPI 只允许出现
  `POST /projects/{project_id}/agent-sessions`，并负向断言消息、历史和工具日志三个端点不存在；
  P06 新增对应 OpenAPI 正向验收并完成四端点闭环。
- POLICY ID 的反向隔离分两层验证：P03 先在 ARCHIVE 五要素查找 service/dependency 层拒绝，保证
  Graph/Checkpoint 之前已有安全边界；P06 再验证三个真实 ARCHIVE 路由统一返回
  `404 ARCHIVE_AGENT_SESSION_NOT_FOUND`。
- Implementation Plan 的 P03 RED/GREEN、P06 RED、AC 映射、文档状态和当前下一步已同步。本次只调整
  实施切片与验收时机，不改变四个冻结端点、六个 DTO、错误码、Top-8 或 D5/D6 契约。
- 当前下一步：按修订后的 FR042-P03 进入 TDD RED；本切片不创建 P04 工具、Graph、Checkpoint 执行
  或三个未实现路由。

## 54. FR-042 P03 HTTP Schema、会话创建与双向隔离（2026-09-15）

- 当前分支仍为 `codex/fr-042-archive-agent-mvp`。既有 Luna 实现 Agent 已达到子 Agent 用量上限，
  主 Agent 按 Implementation Plan 的退化规则接管本切片，并继续执行实际 RED、最小 GREEN 和 Diff 审查；
  未改变冻结需求、API、数据库结构或切片范围。
- Schema RED 为缺少 `app.schemas.archive_agent` 导致收集失败；GREEN 新增六个独立 DTO，固定空对象
  创建、`extra="forbid"`、消息换行与 Unicode 首尾空白规范化、1～2000 码点，以及不含内部 ID/分数
  的引用和响应字段。Schema 定向测试为 `4 passed`。
- Service/dependency RED 为档案会话创建、五要素查找与注入模块缺失；GREEN 后创建只写 PostgreSQL，
  以同一时刻生成 `created_at/updated_at`，不构造模型或 Checkpoint，并使用
  `session_id + user_id + project_id + kb_id + ARCHIVE` 整体查找。制度依赖同步收紧为只接受
  `POLICY + project_id IS NULL`；相关服务与依赖测试为 `5 passed`。
- Router 测试最初暴露测试夹具将带外键的 User/KnowledgeBase/Project 同批提交，以及提交后读取已过期
  User 实例的问题；修正夹具后取得有效 RED：创建请求返回 `404`，唯一原因是目标路由尚未注册。
  最小 GREEN 只注册 `POST /projects/{project_id}/agent-sessions`，空对象创建、字段隐藏、项目 404/403、
  `422 VALIDATION_ERROR` 和其余三个端点不存在的 OpenAPI 契约共 `10 passed`。
- 既有制度消息、历史和工具日志三个入口均已加入 ARCHIVE ID 反向隔离回归，处理函数被替换为失败哨兵，
  三个请求仍统一得到 `404 AGENT_SESSION_NOT_FOUND`，证明拒绝发生在读取 Checkpoint/日志或执行服务前。
  多目录同名测试模块造成的 pytest 收集冲突已通过将路由测试命名为
  `tests/routers/test_archive_agent_routes.py` 消除；服务包边界清单已纳入新增模块。
- P03 相关测试最终为 `45 passed, 7 warnings`；后端全量回归为
  `526 passed, 2 skipped, 144 warnings`；`python -m compileall -q app tests` 通过。warning 仍为既有
  Starlette/httpx2 与 Pixie/jsonpickle 弃用提示。本切片未调用真实 PostgreSQL、Chroma 或 DeepSeek；
  P01 的真实 PostgreSQL 迁移/约束证据仍有效，但不能由本轮 SQLite Router 测试替代。
- 下一步学习检查点：用户确认后进入 FR042-P04，只实施目录与原文证据两个只读工具及请求内安全映射，
  不提前创建 Archive Graph、Checkpoint 执行或消息、历史、工具日志三个 HTTP 端点。

## 55. FR-042 P04 两个只读工具与请求内安全映射（2026-09-15）

- 用户已明确授权连续执行后续切片。P04 由 GPT-5.6 Luna 按冻结计划执行 TDD；有效 RED 为
  `list_agent_formal_archives` 导入失败和 `app.agents.tools.archive_tools` 模块缺失，不是夹具或环境错误。
- `catalog.py` 抽取并复用正式范围、删除阻断、五类筛选和稳定排序谓词，新增 Agent 专用安全投影；
  目录项只含调用内 `A` 引用、文件名、确认时间和五字段 `value/source/has_source_evidence`，正文逐字固定
  分页首行与五字段模板，空目录使用固定文案。
- 新增目录与证据两个只读工具。模型工具 Schema 隐藏服务端 `user_id/project_id/kb_id`、`top_k`、
  `document_ref` 与注入参数；证据查询规范化后固定调用无统一阈值 Top-8 候选入口，只把 `S1..S8`、
  文件名、位置和摘录写入 ToolMessage。
- 原始 `document_id/chunk_id/score/reranker_score` 只保留在按 `tool_call_id` 隔离的请求级注册表；
  ToolMessage 和可序列化消息投影不含这些字段，失败调用清理自身记录，请求成功或异常退出均清空注册表。
- 实现 Agent 的 P04 相关回归为 `77 passed`、全量为 `535 passed, 2 skipped, 144 warnings`。主 Agent
  独立复核目录/工具/FR-038/FR-039 相关测试为 `63 passed, 93 warnings`，全量为
  `535 passed, 2 skipped, 144 warnings`；`compileall app tests` 通过。warning 仍为既有弃用提示。
- 本切片未连接真实 PostgreSQL、Chroma 或 DeepSeek；只证明确定性范围、参数和安全投影，不代表
  FR-042 工具选择或回答质量通过。下一步直接进入 P05，只实现 Archive Graph、共享 Checkpoint Runtime
  与可信最终投影，不注册消息、历史或工具日志 HTTP 端点。

## 56. FR-042 P05 Archive Graph、Checkpoint 与可信最终投影（2026-09-16）

- GPT-5.6 Luna 实现 Agent 在分析阶段长时间未产生 RED 或文件改动；主 Agent 确认无外部阻塞后终止该子任务，
  按已确认实施计划接管 P05。首次有效 RED 为 `app.agents.archive` 模块缺失造成 2 个收集错误。
- GREEN 新增档案专用 Graph/State/Prompt/Runtime/完整轮次投影，并抽取 POLICY/ARCHIVE 共用严格
  SQLite Checkpoint 存储。Graph 限制模型每次决策最多一个工具、单轮最多两次；参数失败占额度，
  多工具、未注册工具和第三次调用均在禁止执行前失败。
- 最终响应只由最后一次成功工具决定：目录使用确定性模板，证据使用规范化用户原问题与该次原始
  Top-8 调用公共 D5/D6 判定，零成功工具或空证据使用固定拒答。Checkpoint 只保存 HumanMessage 与
  服务端可信最终 AIMessage，不持久 ToolMessage、自由文本、原始候选、标识或分数。
- 对模型、工具和证据判定的连接/超时只额外尝试一次，内部重试不增加工具调用数或重复检索。
  补充 RED 证明了判定重试、工具最终失败的安全 `FAILED` 事件，以及工具成功后模型失败仍保留已有事件；
  三条均在预期位置失败后由最小实现转绿。
- P05 聚焦测试为 `18 passed`；连同旧制度 Agent、P04 工具、FR-039 问答/检索的相关回归为
  `73 passed, 102 warnings`；后端全量为 `552 passed, 2 skipped, 146 warnings`，`compileall app tests`
  与 `git diff --check` 通过（仅既有 CRLF 提示）。
- 本切片未调用真实 DeepSeek、PostgreSQL 或 Chroma；上述证据只证明确定性 Graph/Checkpoint 控制流，不是
  工具选择或回答质量评测结论。下一步直接进入 P06，实现消息执行、完整轮次历史、脱敏工具审计和
  四端点闭环，不提前修改项目删除流程或 Vue。

## 57. FR-042 P06 消息执行、历史、审计与四端点闭环（2026-09-16）

- 当前工具环境无可调用的实现子 Agent 接口，主 Agent 按 Multi-Agent Workflow 退化规则执行 P06，
  仍保持“冻结计划→实际 RED→最小 GREEN→Diff 主审”。服务层首次 RED 为
  `app.services.agent.archive_execution` 模块缺失；Router RED 为三个路径/OpenAPI 契约不存在。
- 新增档案助手应用服务：注入 `user_id + project_id + kb_id`，执行同一 `thread_id`，只用 Graph
  可信结果构造 `ArchiveAgentResponse`，成功后更新会话时间。完整轮次历史与下一轮模型输入复用
  同一投影，历史引用固定为空。
- 工具日志按 `(agent_session_id, tool_call_id)` 幂等写入，只接受两个冻结工具名、四个安全错误码和
  冻结参数/结果摘要键；读取时再做白名单复核。自动重试的累计耗时只落一条记录，不持久查询原文、
  筛选值、文件名、摘录、回答、范围 ID、文档/Chunk ID 或分数。
- Router 现已完成四端点。消息 Schema 的 422 在会话查找和 Runtime 之前结束；三个真实 ARCHIVE 入口对
  POLICY ID 统一返回 `404 ARCHIVE_AGENT_SESSION_NOT_FOUND`。Graph 越界映射
  `ARCHIVE_AGENT_MODEL_OUTPUT_INVALID`，模型、工具、Checkpoint、Runtime 与 PostgreSQL 最终失败映射
  `ARCHIVE_AGENT_DEPENDENCY_UNAVAILABLE`，不泄露异常正文或 FR-039 错误码。
- 跨层集成测试使用真实 Router、Archive Graph、SQLite Checkpoint 和业务库，用固定候选/模型桩验证
  连续两轮上下文、固定拒答、证据回答、五字段引用和 PostgreSQL 脱敏工具日志；响应与日志均无内部
  ID、分数或查询原文。
- P06 相关回归为 `82 passed, 19 warnings`；后端全量为
  `567 passed, 2 skipped, 146 warnings`；`compileall app tests` 与 `git diff --check` 通过（仅既有
  CRLF 提示）。本切片未调用真实 DeepSeek/Chroma/PostgreSQL，Mock 不代表工具选择或回答质量通过。
- 下一步直接进入 P07：在既有空项目删除流程中幂等清理 ARCHIVE Checkpoint 线程、会话和工具日志，
  不改变公开删除路由或 Vue。

## 58. FR-042 P07 项目删除联动（2026-09-16）

- 当前分支仍为 `codex/fr-042-archive-agent-mvp`。当前工具环境没有可调用的实现子 Agent 接口，主 Agent
  按已确认 Implementation Plan 的退化规则执行实际 RED、最小 GREEN 和 Diff 主审；未新增公开路由、
  删除状态或数据库结构。
- P07 首次有效 RED 为 4 个服务测试因 `delete_empty_project` 不接受隔离 `checkpoint_path` 而失败。
  GREEN 在既有空项目删除服务内增加 Project 行锁、ARCHIVE 会话枚举、Checkpoint 幂等删除，以及
  工具日志→会话→项目的显式事务清理；文档门禁仍在任何会话或 Checkpoint 清理前返回既有 409。
- 真实临时共享 SQLite Checkpoint 文件验证了目标 ARCHIVE 线程删除、其他项目 ARCHIVE 与 POLICY 线程
  保留；Checkpoint 失败时业务事务 rollback，Checkpoint 已删但数据库提交失败时同一删除可安全重试。
  Router 回归证明公开 DELETE 仍返回 204、旧项目/会话不可访问、KnowledgeBase 与 POLICY 会话保留。
- P07 聚焦测试为 `15 passed, 3 warnings`，扩大到迁移、模型、依赖、项目/Agent 路由的相关回归为
  `59 passed, 9 warnings`；后端全量为 `572 passed, 2 skipped, 148 warnings`；
  `python -m compileall -q app tests` 与 `git diff --check` 通过（仅既有 CRLF 提示）。
- P07 本地实现阶段没有连接或写入真实 PostgreSQL、Chroma、DeepSeek，也没有修改相邻 Vue 仓库。
- `tests/test_migration_service_postgres.py` 已把该真实门补成可选测试：专用空库升级到 `0011` 后，创建
  ARCHIVE 会话/工具日志与真实临时 Checkpoint，再调用项目删除服务并核对 Project、会话、日志和线程
  清零，同时保留 KnowledgeBase/POLICY。未设置 `POSTGRES_TEST_URL` 的普通本地预检为 `1 skipped`。
- 用户随后明确授权真实 PostgreSQL 隔离写入。第一次尝试因 Windows pytest 用户临时目录无权限而在
  测试 setup 前失败；当次随机 schema 已按所有权核验清理。将 pytest 临时目录固定到工作区后，第二次
  在新的随机 schema 完成 0001→0011 迁移、约束、Project 行锁删除服务、日志/会话清理和真实临时
  SQLite Checkpoint 线程删除，结果为 `1 passed in 32.08s`。
- 第二次随机 schema 同样按所有权核验删除，并以独立只读系统目录查询确认剩余数量为 0；临时运行器和
  pytest 临时目录均已清理。随后后端全量复跑为 `572 passed, 2 skipped, 148 warnings`。
- 下一步进入 Swagger、真实 Chroma/DeepSeek 固定集与 Vue 验收。新的外部写入须另获用户授权；Vue
  阶段仍须先处理相邻仓库的既有脏工作区和分支门。

## 59. FR-042 P08 第二轮真实评测与判定收敛（2026-09-16）

- 用户已分别授权两轮 17 条真实 DeepSeek 固定集。第二轮结果目录为
  `pixie_qa/results/20260916-093042`；32 条 pending 已逐条落盘评分，数据集详细/摘要分析和根行动计划
  均已写入，官方 Step 6 verifier 通过。没有跳过任何成功运行产生的分析义务。
- 第二轮严格条目通过率为 `15/17（88.2%）`，较第一轮 `12/17（70.6%）` 提升。ToolPath 为
  `16/16`，证明事实问题绕行目录工具的提示修正生效；Privacy `17/17`、目录 `2/2`、无据拒答
  `2/2`、隔离拒答 `2/2`、受控失败 `1/1` 均通过。
- 剩余失败为 Q-02 与 MULTI-02 第一轮：安全候选分别直接包含“编制单位：北辰设计院”和
  `文档日期：2025-03-18`，工具路径正确但公共判定错误拒答。8 个单轮有据问题达到 `7/8`，但两组
  多轮只有 MULTI-01 完整通过，因此 §10.3 的多轮质量门仍为 `1/2`，不得宣称 FR-042 质量通过。
- 在完成第二轮 Step 6 后继续 TDD：提示契约 RED 后，`questions.py` 增加“先定位目标文档，再核对同一
  `document_ref` 字段”的窄化规则；数据材料 RED 后，生成器新增逐轮 `expected_document_filenames`
  和写出前的同文档事实绑定门；评估器 RED 后，多轮 EvidenceFaithfulness 改为逐轮评分并取最低分，
  任一核心事实失败时必须低于 0.5。
- 本轮三个 RED 均实际运行并在预期断言失败，最小 GREEN 后相关测试为 `41 passed`；后端全量为
  `596 passed, 2 skipped, 207 warnings`，`compileall app tests evals scripts` 与 `git diff --check`
  通过（仅既有 CRLF 提示）。上述本地测试不证明修改后的真实 DeepSeek 质量。
- 当前未发起第三轮真实模型调用，也未修改相邻 Vue 仓库。Pixie 展示服务可能仍运行在既有 7118 端口，
  未经用户确认不停止。下一步须由用户重新授权第三轮真实 DeepSeek 固定集；完成该轮 Step 6 并确认
  多轮 `2/2` 后，才能进入 Swagger、Vue 与交付文档的最终验收。

## 60. FR-042 P08 第三轮真实评测、不合格分析与复盘（2026-09-16）

- 用户已授权第三轮 17 条真实 DeepSeek 固定集，结果目录为
  `pixie_qa/results/20260916-151651`。17 条 HTTP/Graph/模型调用均完成；Pixie 在最终控制台汇总时因
  Windows GBK 无法编码 `⏳` 而退出，故使用 `scripts/recover_fr042_pixie_result.py` 从已落盘的
  17 份 Trace 恢复结果，没有重复调用付费模型。`meta.json` 已明确记录恢复事实。
- 32 条待评分均已完成。数据集详细/摘要分析、根行动计划详细/摘要均已写入，官方 Step 6 verifier
  返回 `Step 6 completion check passed.`，当前结果目录没有 pending。
- 第三轮严格通过 `15/17（88.2%）`。ToolPath `16/16`、Privacy `17/17`、目录 `2/2`、多轮 `2/2`、
  受控失败 `1/1` 均通过；MULTI-02 已由第二轮失败变为两轮完整通过。
- 当前失败为 Q-02、Q-03，EvidenceFaithfulness 均为 0.0。两题都只正确调用一次证据工具，候选也分别
  含“编制单位：北辰设计院”和“项目阶段：施工阶段”，但候选只暴露英文文件名，缺少把用户所说
  “设计说明”“施工方案”映射到目标文档的模型可见身份信息，公共判定层因此保守拒答。
- 有据单轮问答仅 `6/8`，低于 §10.3 的 `7/8` 门槛，所以 FR-042 真实模型质量仍不通过。当前不应继续
  堆叠 Prompt 或虚构标题证据；应先裁决是否把已确认档案的稳定标题/显示名作为模型可见候选字段，并
  保证它与文件名、`document_ref` 和字段摘录同源。
- 用户要求的简明记录已建立在 `docs/review/FR-042-真实模型质量评测/`，其中《评测复盘》记录第一轮
  问题、第二轮优化和第三轮现状，《问题排查与解决方案》固定排查顺序、解决方案与通过标准。
- 下一步先完成上述契约裁决，再按 TDD 增加文档身份绑定正例、跨文档负例和身份不足拒答测试，实施最小
  修正并完成回归。第四轮真实 DeepSeek 调用须另获用户授权；当前未修改 Vue，未提交或推送。

## 61. FR-042 DEC-021 文档标题身份绑定本地修正（2026-09-16）

- 用户确认采用主 Agent 推荐方案：证据候选复用当前 `CONFIRMED` 档案已人工确认的 `TITLE`，作为可空
  `document_title` 帮助模型把自然语言文档称呼绑定到同一 `document_ref`；不新增数据库列，不根据
  文件名或模型输出推断标题。该稳定决策已登记为 DEC-021，并同步需求、架构、接口与实施计划。
- 本轮使用 eval-driven-dev 已完成第三轮 Step 6 后的行动项。当前无可调用的 Luna 实现接口，主 Agent
  按既定退化规则执行 TDD 和 Diff 审查，没有改变公开 HTTP Schema、引用 DTO、数据库迁移或工具参数。
- RED 新增并实际运行 4 项契约：正式标题读取服务不存在、证据工具未注入标题、公共判定 Prompt 未展示
  标题、固定集没有逐轮标题绑定；结果为 `4 failed`，失败位置均与预期一致。
- GREEN 在目录服务中按 `project_id + CONFIRMED + 删除可见性` 读取目标文档标题；证据工具对检索候选
  再做一次正式范围交集，只向模型投影可空标题。标题只用于身份，事实答案仍要求同一 `document_ref`
  的 `excerpt`；Checkpoint 继续只保存受信用户/助手消息，公开引用与工具审计字段不变。
- 固定集标题来自 `scripts/generate_archive_v1_eval_data.py` 的既有虚构正式元数据，Q-02/Q-03 分别绑定
  “星河办公楼改造工程设计说明”和“星河办公楼改造工程施工方案”，禁止测试脚本猜测标题。生成器新增
  `expected_document_titles` 硬门，并要求标题、文件名和答案事实共享请求内文档引用。
- 最小 GREEN 四项为 `4 passed`；扩展到目录、工具、公共判定、评测材料、Graph、执行服务和 Router 为
  `94 passed, 134 warnings`；后端全量为 `598 passed, 2 skipped, 207 warnings`。warning 均为既有
  Starlette/httpx2 与 Pixie/jsonpickle 弃用提示。
- `python -m compileall -q app tests evals scripts` 与 `git diff --check` 已通过，后者仅显示既有 CRLF
  提示；固定集抽查确认 Q-02、Q-03、MULTI-02 的预期标题、文件名和候选标题一致。
- 当前仍只证明本地确定性行为和固定集数据约束，不能证明 DeepSeek 已修复 Q-02/Q-03。下一步须由用户
  另行授权第四轮真实模型固定集；当前未提交、未推送、未改 Vue。

## 62. FR-042 P08 第四轮真实评测通过（2026-09-17）

- 用户明确同意将本轮虚构、去标识化固定集发送至 DeepSeek，仅授权第四轮完整 17 条评测。结果目录为
  `pixie_qa/results/20260916-155823`；未发起第五轮调用。
- Pixie 使用 UTF-8 环境原生完成运行与落盘，没有复现第三轮 Windows GBK 汇总异常。17 条均经过 HTTP
  入口、生产 Archive Graph、真实 DeepSeek 和临时 Checkpoint；目录/检索外部世界为受控注入数据。
- 严格通过 `17/17`。有据单轮 `8/8`、无据 `2/2`、隔离 `2/2`、目录 `2/2`、多轮 `2/2`、Trace
  隐私 `17/17`、受控失败 `1/1`；Q-02、Q-03 已分别正确回答“北辰设计院”“施工阶段”并返回同文档引用。
- 32 条 Agent Evaluator pending 已依据每轮输入、工具路径、安全候选、响应和引用逐条评分并写回，当前
  pending 为 0。详细/摘要分析和详细/摘要行动计划均已生成，官方 Step 6 verifier 返回
  `Step 6 completion check passed.`。
- 本结论只证明一次修复后的固定集真实模型运行通过，不能替代真实 PostgreSQL、Chroma、文件和 Vue
  端到端证据，也不能证明长期稳定性。下一步先完成 Swagger/HTTP 真实本地闭环，再进入 Vue 最小联调。
- 当前未提交、未推送、未修改 Vue。Pixie 服务仍保持在既有 7118 端口；未经用户确认不停止。

## 63. FR-042 真实后端闭环验收通过（2026-09-17）

- 用户授权进入实际本地后端闭环验收。只读预检发现 8000/8001/5432/7118 均未监听、本机无 Docker CLI，
  项目当前 PostgreSQL 配置指向远端开发实例且 `public` Schema 为 `0010_account_auth`。本轮没有迁移或
  写入共享 `public`，而是在同一实例创建随机隔离 Schema，并在结束时删除。
- 验收使用本地 FastAPI、临时 Chroma 1.5.9、本地 BGE/Reranker、文件系统、SQLite Checkpoint 和真实
  DeepSeek；资料是一份完全虚构 TXT。随机 Schema 从空状态迁移至 `0011_archive_agent_scope`。
- 主链路完成：注册/登录→创建项目→上传→解析→七字段人工确认→`CONFIRMED`/Chroma 索引→创建档案
  会话→目录题→有据题与同文档引用→无据固定拒答→读取 3 组完整历史→读取脱敏工具审计。
- 第一次业务验收脚本错误要求三轮恰好 3 条工具日志；第二次仍错误要求无据题至少调用一次证据工具。
  需求、架构、API 与单测明确允许单轮最多两个调用，且零成功工具时固定拒答。第三次按正式契约验证：
  目录 1 次、有据 1 次、无据 0 次，最终通过。前两次失败均为验收器过约束，不是应用缺陷。
- 最终聚合：健康检查通过、迁移 `0011`、文档已索引、目录/有据/无据三类响应通过、历史 3 轮、工具
  审计 2 条、公开响应脱敏通过。清理后 PostgreSQL/Chroma/文件/Checkpoint 均为零，随机 Schema 已删除。
- 简明复盘位于 `docs/review/FR-042-本地后端闭环验收/`。临时 Chroma 和验收目录已停止并删除，8000、
  8001 均未继续监听；先前 §62 所述 Pixie 7118 当前也已不再监听。当前未修改 Vue、未提交、未推送；
  下一步进入相邻 Vue 仓库分支门和最小联调。

## 64. FR-042 Vue 确定性接入完成，真实业务代理联调待恢复 Chroma（2026-09-17）

- 相邻 Vue 仓库基线工作区干净，已从 `feature/ui-feedback-and-layout` 创建并切换到
  `codex/fr-042-archive-agent-mvp`；未执行 stash、reset、提交或推送。
- 按 TDD 完成 FR-042 独立“档案助手”入口：新增四端点 DTO/API、当前会话 Store、顺序消息、当前回答
  状态、五字段脱敏引用、历史刷新、脱敏工具日志和项目切换清理；现有 FR-039 单轮智能检索保持独立。
- RED 分别证明 API 方法缺失、Store 动作缺失、面板缺失、View 路由/侧栏缺失和过期历史错误污染；
  GREEN 后前端完整回归为 `11` 个测试文件、`130 passed`，类型检查和生产构建均通过。
- 本地临时启动 FastAPI 与 Vite 后，新增页面深链返回 `200`；FR-042 创建会话端点经直接 `8000` 和
  Vite `5173/api` 均返回相同 `401 AUTHENTICATION_REQUIRED`，证明受保护路由与代理可达。
- 健康检查显示 PostgreSQL 和 768 维 Embedding 正常、Chroma 未运行，直接与代理均返回 `503 degraded`；
  浏览器控制服务返回 `nodeRepl.fetch request failed`。因此没有新增登录后三类问题或真实 DOM 点击证据。
- 标准 `python run.py` 启动自动执行 Alembic，使当前开发库 `public` Schema 从 `0010_account_auth` 升至
  `0011_archive_agent_scope`；未执行回滚。临时 FastAPI/Vite 已停止，8000/5173 均无监听。
- 下一步恢复本地 Chroma 并使用现有演示用户/项目/正式 TXT 完成 Vite 代理顺序验收：新建会话→目录题
  →有据题→无据题→历史→工具日志；完成后再审查两个仓库的提交范围，不提前提交或推送。

## 65. FR-042 Vue 真实代理闭环验收通过（2026-09-17）

- 用户明确授权 Vue 真实代理闭环验收。本轮没有使用共享演示数据，而是建立随机隔离 PostgreSQL
  Schema、独立临时 Collection、文件目录和 Checkpoint；资料、账号、项目和人物均为虚构数据。
- 隔离 Schema 从空状态迁移到 `0011_archive_agent_scope`。全部业务请求经 Vite `5173/api` 代理完成：
  注册登录、创建项目、上传解析、七字段人工确认、正式索引、创建会话、目录题、有据题、无据题、
  历史与工具日志；前端深链接同时返回 `200`。
- 最终三轮状态为 `ANSWERED / ANSWERED / REFUSED_NO_EVIDENCE`；有据回答包含目标事实和 1 条同文件
  脱敏引用，无据回答使用固定文案；历史为 6 条完整消息且引用按 MVP 契约恒空；工具日志只有目录与
  证据两个只读工具，共 2 条，安全摘要未出现范围 ID、原文事实或分数。
- 前三次未通过均已定位：公开响应不含 `final_chunk_count`、历史引用按契约恒空、复合问题一次真实模型
  调用保守拒答。前两项是验收器过约束；第三项通过把闭环烟测收敛为单一直接证据问题解决，复杂质量
  仍由已完成的 17 条固定集承担。第四次完整通过，未修改产品代码。
- 验收前确认临时正式 Collection 有 Chunk、Checkpoint 非空、文件已落盘；结束后隔离 Schema 和两个
  Collection 剩余数量均为 0，8 个临时文件、Checkpoint 与 ASCII 临时目录已删除。8000/5173 已停止；
  用户原有 Docker Chroma `8001` 保持运行且未清理其他 Collection。
- 简明记录位于 `docs/review/FR-042-Vue真实代理闭环验收/`。本轮证明真实 HTTP 代理闭环，不包含新的
  浏览器 DOM 点击证据，也不证明长期稳定性。下一步是审查两个仓库 diff 与提交范围；未经用户要求不
  提交、不推送，也不发起第五轮真实固定集。

## 66. FR-042 提交前契约偏差修复（2026-09-17）

- 提交前审查发现后端 Runtime 构造阶段的 `AppError` 会原样泄漏 `DEEPSEEK_NOT_CONFIGURED`，违反
  FR-042 冻结错误码；Vue 同时仍使用两个旧工具名，导致真实工具日志显示原始英文名称。
- 后端按 TDD 增加构造 `AppError` 的失败用例，实际 RED 为返回 `DEEPSEEK_NOT_CONFIGURED`；修正后
  显式区分 Runtime 打开、执行和关闭阶段：打开/关闭异常统一映射依赖不可用，执行阶段已有业务错误
  保持原样。新增回归用例同时覆盖模型输出业务错误与关闭阶段 `AppError`。
- Vue RED 使用真实 `search_confirmed_archive_evidence` 后中文标签断言失败；GREEN 将两个映射改为
  `list_formal_archives` 与 `search_confirmed_archive_evidence`，并同步 API/Store 测试中的脱敏摘要。
- 后端路由相关测试 `20 passed`，后端全量 `601 passed, 2 skipped, 207 warnings`；Vue 相关测试
  `76 passed`，全量 `130 passed`，类型检查和生产构建通过；后端 `compileall` 与两个仓库
  `git diff --check` 通过。warning 仍为既有弃用和 CRLF 提示。
- 未重复执行第五轮真实 DeepSeek 固定集，因为本轮没有修改 Prompt、Graph、检索或公共判定层；未新增
  业务决策，未提交、未推送。下一步只做两个仓库最终提交范围审查和显式暂存规划。

## 67. FR-042 最终提交范围审查（2026-09-17）

- Vue 仓库当前 12 个修改/新增文件全部属于 FR-042，可作为一个独立提交；构建产物与 TypeScript 缓存
  均未进入状态清单。
- 后端应拆为当前用户资料前置、FR-042 功能、FR-042 评测资产和 FR-042 文档四类提交。详细边界记录在
  `docs/review/FR-042-提交范围审查/提交范围.md`；实际暂存必须逐路径执行，禁止 `git add -A`。
- `scripts/policy_collection_embedding_rebuild.py` 与其测试明确排除。当前 diff 会撤销 §22 已完成的安全
  收口，包括只读预检、聚合计数、维度错误分类、canary 精确命中和异常脱敏；该组修改与 FR-042 无依赖，
  且归属未确认，因此本轮保持未暂存，不擅自修改或回滚。
- 用户已授权继续提交。后端当前用户资料、FR-042 功能和评测资产已分别提交为 `f497f15`、`26abb22`、
  `c6661dd`；本文档随 FR-042 文档提交。每次提交前均已核对 `git diff --cached`，上述两个排除文件
  始终不在索引中。下一步完成 Vue 独立提交，再推送两个仓库的同名功能分支。
