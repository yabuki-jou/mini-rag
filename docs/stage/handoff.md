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
