# Mini RAG 当前交接

## 1. 当前分支与产品范围

- 后端仓库：`mini-rag-handwrite`，当前工作分支为 `codex/fr-042-archive-agent-mvp`（截至本次核对，本地分支领先同名远端 6 个提交，工作区有未提交改动）；P08 功能已由 GitHub PR #6 合入 `main`，后续 `app/` 注释提交已合并并推送。
- 相邻 Vue 仓库：`mini-rag-milvus-vue`，当前工作分支为 `main`；P08 功能分支已快进合并并推送。
- 当前正式产品主线：智慧档案 V1、FR-039 档案问答、FR-042 项目档案助手。
- 旧通用知识库、普通 Chat 和制度 Agent 的公开 API 与运行代码已下线；旧路径不在 OpenAPI 中并返回默认 `404`。
- PostgreSQL 历史表/数据、SQLite Checkpoint、Chroma Collection、原文件、Alembic 历史与 `AgentType.POLICY` 保留，不执行破坏性清理。

## 2. 本轮已完成

- P00：制度 Collection 重建脚本和未标定距离阈值分别验证、提交并推送到保护分支 `codex/policy-rag-preservation`。
- 新增 DEC-022 与《智慧档案单主线拆除实施计划》，DEC-001、DEC-011～013 已标为被替代。
- P01：旧 `/knowledge-bases`、`/chat-sessions`、`/agent-sessions` 路径组取消注册；负向 OpenAPI/404 契约已落地。
- P02：删除旧 Router、`app/agents/admin`、制度工具、通用 `services/rag`、普通 Chat ORM/Schema、POLICY 专用应用服务、制度评测包和重建脚本；共享 DTO 已迁入档案助手契约。
- P02 数据兼容：保留 `KnowledgeBase`、`Document`、`AgentSession`、`AgentToolCallLog`、`AgentType.POLICY` 与全部迁移；档案入口继续拒绝历史 POLICY 会话，项目删除继续保留无项目绑定历史线程。
- P03：Vue 删除旧知识库、普通文档、检索、Chat API 方法、旧 localStorage 会话分区和未使用类型；智慧档案页面与 FR-042 交互未改变。
- P04：当前 AGENTS、README、学习计划、设计与 codebase 文档已收敛到智慧档案单主线；制度 Agent 专属资料迁入 `docs/archive/legacy-policy-agent/`；新增当前发布说明。
- 主审修复：文档变更造成固定评测来源行号漂移，已只更新定位行号，不改问题、答案、摘录或评分门槛。
- 合并收口：前后端功能分支均在同步最新远端 `main` 后无冲突合并；合并文件树与已验收功能分支一致。
- FR042-P08：新增当前项目最近档案助手会话查询；Vue 进入档案助手页面时自动恢复最近会话，并加载完整可见历史和脱敏工具记录。
- P08 主审补齐错误 `user_id`/`kb_id` 范围反例及登录后认证状态变化竞态；不增加会话列表、客户端会话持久化、历史引用恢复、迁移或索引。
- P08 真实刷新恢复已通过：隔离 PostgreSQL Schema、独立 SQLite Checkpoint、真实 Vite 代理和浏览器整页刷新共同验证最近会话、完整历史及 1 条脱敏工具记录自动恢复。
- 真实刷新验收使用固定本地模型向真实 Archive Graph 写入受控历史，没有调用 DeepSeek；项目删除后会话、工具记录和 Checkpoint 均由 1 归零，隔离 Schema 与临时 SQLite 文件已删除。
- P08 收口：后端 `main` 已包含功能和注释完善提交，Vue `main` 已包含自动恢复提交；两个 `main` 均已推送到各自 origin。
- 新增公开世界银行 P153548 smoke 数据集，完成真实 DeepSeek Pixie 评测收口；结果仅作为注入证据回答层的质量证据，不把被忽略的运行目录作为 Git 产物。
- 完成 P153548 隔离持久层端到端验收：4 份真实 PDF 均完成上传、解析、字段确认和正式索引；PostgreSQL、Chroma、文件与 Checkpoint 均验证真实写入和业务清理归零，临时 API、Schema、Collection 与运行目录已删除。
- 完成 `LUSHAN-01` Top-30 双排序与只读向量归因：标准证据未进 Chroma Top-30，排除 Reranker 和解析遗漏；控制变量显示“世界银行”与原文 `IBRD` 的术语不对齐是当前主因，整页 Chunk 稀释仅为未证实的次要可能。
- 完成别名与切分只读对照：追加别名仍只到 dense 第 46；`800/300` 固定窗口使三条有据题全部退化；检索侧将贷款语境的“世界银行”规范为 `IBRD IDA` 时，`LUSHAN-01` 达到 dense 第 6、Reranker 第 3。该结果仅形成候选方案，未授权或修改正式 `app/`。
- 完成双路 Top-30 候选并集只读复核：并集 44、交集 16；第 16 页在当前内存重建向量上的原问题/`IBRD IDA` dense 名次为 120/8，原问题/补充表达重排名次为 21/3。原问题重排未达目标 Top-8；补充表达重排达 Top-8。三种 Q1 条件的单次真实 DeepSeek 回答均为 `US$300 million`，引用有效页面，但单次观察不构成稳定质量验收。
- 已记录四种重排条件各 5 次的延迟与 44 条候选逐项分数；详见 FR-042 排查文档和本地忽略文件 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/acceptance/dual-rerank-scores-20260923.json`。内存向量来自解析原文，未包含 `evidence_values` 上下文，不能当作正式 Chroma 复刻。
- 完成 44 候选池上的纯离线融合排序比较：RRF `k=10/60` 的指定页名次分别为 7/8，分数归一化等权排序为 6，平均名次为 9；RRF `k=10` 的 Top-8 包含 5 个已人工核实的金额支持页。该指标不是完整相关性召回或冻结评估器成绩；逐候选结果保存在本地忽略文件 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/acceptance/dual-rerank-fusion-20260923.json`。
- 两组并集重排中位数相加约 20.57 秒仅为串行耗时粗略推算，未联合计时。排序后处理无需额外推理，但取得两组重排结果需要两路 CrossEncoder 推理；尚无正式预算决策。
- 完成 dense Top-K 并集敏感性模拟：每路 K=10/15/20/25/30 的并集为 18/26/32/39/44；目标页保持在池内，RRF k=10 的目标排名恒为 7，min-max 融合恒为 6，五个已核实支持页均进入两者 Top-8。该结果复用 44 条已有候选分数，未实际调用较小池的 Reranker，不含延迟证据；逐策略排名保存在本地忽略文件 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/acceptance/dual-rerank-k-sensitivity-20260923.json`。
- 完成每路 Top-10/15/20 实际候选并集 CrossEncoder 延迟测量，候选数 18/26/32；每个池、每种查询表达先预热 1 次，再进行 5 组交错顺序的真实 CPU 推理。两路串行中位数分别为 9.35/13.55/19.59 秒；单路与逐次结果见 FR-042 排查文档及本地忽略文件 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/acceptance/dual-rerank-k-latency-20260923.json`。已先核对历史分数查询表达与当前生产查询构造相符；此项只证实本地推理耗时，不验证正式 Chroma/evidence_values 排序质量。
- 单独核查 Q2“最终到期年限与宽限期”后，第 16 页是该批资料中数值组合唯一的页面，但 dense rank 37、未进 Top-30，因此停止后续排序/模型测试。P153548 固定资料中没有“贷款语境问题、原文使用世界银行措辞”的反向正例。此前发现的白俄罗斯公告直链当前直接抓取返回 404，不以搜索摘要作为已核验事实；已改用可访问的官方 Belarus Snapshot PDF 作为独立扩展来源，但其同时出现 World Bank 与 IBRD，不构成严格的无 IBRD/IDA 反向例。
- 完成用户授权的真实 Chroma / `evidence_values` 双路检索排序诊断：原问题与 `IBRD IDA` 各取 dense Top-30，交集 15、并集 45。原问题重排的公开证据覆盖排名 22；补充表达重排第 3；RRF k=10 第 7；min-max 等权第 6。各策略均未达到精确标准摘录匹配。融合 Top-8 未送入回答模型，不能据此宣称引用或端到端质量修复。隔离 PostgreSQL Schema、Chroma 数据库/Collection、临时文件与 Checkpoint 均已核验清理。脱敏摘要位于被忽略文件 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/acceptance/dual-rerank-chroma-evidence-20260923.json`。
- 已完成 [P153548 融合候选回答引用对照实验计划](../review/FR-042-项目档案助手/P153548融合候选回答引用对照实验计划.md)：五组 Top-8、每组 3 次真实模型判定，实际 15/15 次完成；C 组（并集使用 `IBRD IDA` 重排）冻结标注引用 3/3，A/B/D/E 均为 0/3，后四组引用了非冻结页面并按保守规则保留人工复核状态。A/B 公开证据未进回答 Top-8，C/D/E 进入；未据此选定正式融合规则。
- 本次正式 Chroma 链路分段观测为原问题 dense `305.191 ms`、补充 dense `64.199 ms`、原问题重排 `11664.199 ms`、补充表达重排 `9930.984 ms`、融合后处理 `1.793 ms`；这是单次本地运行，不是正式延迟预算。回答组中位数和逐次脱敏结果见排查报告及被忽略文件 `tests/pytest_docs/public_projects/lushan_earthquake_p153548/acceptance/fusion-answer-chroma-evidence-20260923.json`。
- 回答对照完成后，PostgreSQL、Chroma、文件和 Checkpoint 均归零，临时 Schema、数据库/Collection 和目录已删除；本轮没有修改正式 `app/`、配置、冻结集或公开 API。
- 为 DEC-024 扩展来源评测保存了官方 Belarus Snapshot PDF 原件，SHA256 为 `e7661b3a6580d1e2eaf3317e2d286fdc242377e9785a89129d671b27781dbe5f`；项目 `archive-v1-parser-v1` 成功提取全部 20 页，解析快照 SHA256 为 `c0f6643b5f76f50720e0041531680a194bea71dbb0776e25a4d2c7aab77a2b13`。人工核验第 7、16 页，并记录 M6 的 IBRD 融资金额、2015-03-05 已提款状态、Bruzgi 运力及 IFC advisory work。资料位于忽略的 `tests/pytest_docs/public_projects/world_bank_belarus_m6/`，含来源清单、样例标注及待实现的确定性门控测试向量；在来源准备阶段尚未索引、未调用模型、未改正式代码或冻结集。
- 完成 DEC-024 第三步真实检索-only 对照：隔离索引 P153548 四份固定 PDF 与 Belarus Snapshot 一份扩展来源；七个问题、两个补充查询、九次真实 CPU Reranker 排序，`model_calls=0`。`LUSHAN-01` 并集 45/交集 15，公开证据原问题重排第 22、补充表达第 3；LUSHAN-02/03 公开证据重排第 1/5；WB 贷款正例两路候选相同（20/20），支持页均进入两种重排 Top-2。否定题只有相关背景、无答案支持型候选。详见方案 §8 与忽略的 `tests/pytest_docs/public_projects/world_bank_belarus_m6/acceptance/retrieval-only-81808eeb127f4ecabbf6703ee6ff9a7c.json`；结果不证明答案/引用卡片质量，严格反向正例仍未验证。
- 第三步临时资源已清理：PostgreSQL 非迁移业务行数为 0 后移除隔离 Schema；Chroma Database/Collection、上传目录、Checkpoint、API 与专用日志均已核验归零。运行中修复了验收脚本的直接入口导入、标准证据 helper 导入和 Chroma AdminClient 管理接口误用，具体原因与定位记录见 FR-042 扩展来源方案 §8.2。
- 已按 TDD 为扩展来源回答/引用准备器补齐内层检索阶段、清理步骤、四层残留布尔值及六枚举异常类别安全透传；主审修正 Chroma 名称比较保护、复合失败优先级、CLI `_emit` 白名单、主体阶段优先及参数文档。另发现并修复隔离配置漏重绑持久化验收模块的 Engine/Settings；新增回归测试实际 RED 后 GREEN。目标验收器两个模块同进程 `95 passed`，全量后端 `687 passed, 2 skipped, 225 warnings`，全量 compileall 通过。
- 最新独立全量命令进程级移除了 PostgreSQL 集成测试开关，退出码 0；`compileall -q app tests scripts evals pixie_qa` 与 `git diff --check` 均通过。输出未暴露 `.env` 配置。
- 第四次扩展来源准备未生成候选：主错误定位为 `prepare.empty_target_validation`，清理探针定位为 `residuals.postgres_count`。脚本报告 `cleanup_verified=false`；事后只读复核确认隔离 Schema/Chroma Database、Checkpoint、上传目录和日志目录均不存在，唯一残留的运行目录为空并已按精确路径安全移除。原始异常未输出，底层原因仍未确认；随后按 TDD 增加八个预检子阶段和最内层阶段优先级。
- 第六次准备失败的旧 Engine/Settings 根因已按 TDD 修复。第七次真实隔离检索生成 21 条候选，来源哈希通过、失败组和跳过组均为 0；摘要报 `cleanup.delete_chroma_database` 失败且 `cleanup_verified=false`。事后只读核验确认专用 Schema、Chroma Database/Collection、Checkpoint、文件、运行目录、日志均不存在；异常子步骤/类别未保存，底层错误未确定，原摘要保留。
- Step 4 准备阶段审计确认 7 组×3 条、每条 8 个有效页候选，5 个有据条件 15/15 含主要答案支持页，脱敏字段检查通过。Pixie 数据集生成器顶层 Runnable 缺失已按 TDD 修复并通过 `Dataset.model_validate`；该句的调用数 `0/21` 只表示准备阶段，真实运行结果见第 5 节。
- 最新 §5.2 检索-only 隔离复验（`6f2c6ee6e9894b44`）完成：九个检索诊断用例、来源哈希通过、候选集生成 `21` 条、`model_calls=0`；LUSHAN-01 双路并集 `54`（交集 `6`），公开目标证据补充重排第 `3`，FR-042 脚本 Graph 工具 Top-8 目标证据第 `3`。Belarus 贷款明细第 16 页原/补充排名 `1/7`，项目背景第 7 页 `2/17`。运行器及独立只读复核均确认专用 Schema、Chroma、Checkpoint/运行目录、日志归零。最新脱敏摘要留在被忽略目录；细节及错误排查链见[真实检索验收报告](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)阶段 §5.2 复验。
- 本轮修复摘要脱敏器海象变量遮蔽输入摘要的脚本缺陷；RED/GREEN 后相关验收模块 `103 passed, 2 warnings`。最终后端全量 `732 passed, 2 skipped, 241 warnings`；`compileall -q app tests scripts evals pixie_qa` 通过，`git diff --check` 通过（仅有既有 LF/CRLF 提示）。另合并记录 Graph Checkpoint、SQLite WAL 清理、重复 Chroma 删除及第九用例白名单问题，见同一报告。
- 答案/引用评测与本次检索-only 验收分开；本次 DeepSeek 调用数为 `0`，候选数据的 `21` 条不是模型调用数。下一步如继续模型评测，先依计划和报告核对该项授权与预算状态，再决定运行获准范围；不得重跑默认全数据集目录。

## 3. 验证证据

- 后端最新全量：`732 passed, 2 skipped, 241 warnings`，退出码 0；两个目标验收器模块 `103 passed`，全量 `compileall` 通过。运行时移除仅供测试的 PostgreSQL 集成环境开关并将日志置于专用临时目录；该结果不包含真实 PostgreSQL 并发/迁移集成测试。
- 后端 `compileall -q app tests scripts evals pixie_qa`：通过。
- 融合回答对照脚本定向测试：`26 passed`；真实 DeepSeek 对照：15/15 次完成，清理四层归零。
- 本轮确定性评估相关测试：`19 passed`；P153548 Pixie 评测为 5 个条目、6 轮、21/21 个 evaluations 得分 `1.0`，`pending=0`，Step 6 已完成。
- P153548 持久层验收与双排序诊断的相关脚本测试：`53 passed`；真实质量门槛未通过，Top-8 有据覆盖 2/3，FR-039 有据回答缺目标引用，FR-042 三轮仅无据拒答轮通过。
- 新增隔离双路排序验收脚本的定向测试：`17 passed`；覆盖候选并集、重排/离线融合摘要、隔离配置拒绝默认资源与脱敏结果等行为。
- 最终后端全量回归：`534 passed, 2 skipped, 179 warnings`；扩展来源检索脚本定向测试 `12 passed`，`compileall -q app tests scripts evals` 与直接 CLI `--help` 通过。
- Alembic：真实 PostgreSQL `upgrade head` 通过；本轮没有新增迁移。
- Belarus Snapshot 来源准备复核：项目 PDF 解析器对原件提取 20 个页片段，人工检查第 7、16 页所需事实均存在；`tests/services/archive/test_parser.py` 定向回归 `8 passed`。本次没有改解析器代码，先前一次路径错误的 pytest 调用未发现或运行测试。
- 当前 Markdown（排除历史归档和评测素材）链接检查：53 个文件通过。
- Vue：11 个测试文件、`136 passed`；`npm run typecheck`、`npm run build` 通过。
- 合并后的本地复验：后端 `492 passed, 2 skipped, 179 warnings`、`compileall -q app tests scripts evals` 通过；Vue `136 passed`、类型检查和构建通过。
- 真实 Vue `/api` 代理冒烟：注册/登录、项目创建、空正式目录、FR-039 `REFUSED_NO_EVIDENCE`、FR-042 `ANSWERED`、2 条历史、1 条脱敏工具日志均通过；真实 OpenAPI 不含旧路径。
- 冒烟临时项目、会话/Checkpoint、工具日志、登录会话、知识库和临时用户均已清理；健康检查为 API、PostgreSQL、Chroma、Embedding 全部正常，Embedding 维度 768。

## 4. 已知限制与注意事项

- 本轮没有重跑 FR-039/FR-042 完整真实固定集；§5.3 只按批准方案运行选定的十个答案/引用样本。语义算法未改变，确定性回归不能替代完整真实模型质量结论。
- 2026-09-23 已完成真实 Chroma / `evidence_values` 双路候选及回答/引用对照：真实并集 45 条上，A/B 公开证据未进回答 Top-8，C/D/E 进入；五组各 3 次，只有 C 组达到冻结标注引用 3/3。A/B/D/E 的非冻结页面仍按 `needs_manual_review` 记录，不能只凭页码不同宣称引用质量通过；15 次结果只覆盖 `LUSHAN-01`，不构成正式融合选择或 FR-042 质量门。
- 2026-09-23 真实对照的分段耗时为原问题 dense `305.191 ms`、补充 dense `64.199 ms`、两路重排 `11664.199/9930.984 ms`、融合后处理 `1.793 ms`；仅为单次本地观测。回答模型中位数、引用页和 token 用量见排查报告，结果文件已脱敏且处于 Git 忽略目录。
- 旧方案记录的 `IBRD IDA` dense 名次为 6，本次精确替换复核为 8，差异原因未确认；两次结果都记录，不推断原因。P153548 四份固定资料没有“贷款语境问题、原文用世界银行措辞”的反向正例；Belarus Snapshot 已保存为独立扩展来源，但不是严格无 IBRD/IDA 样例。
- P08 已有真实 PostgreSQL + Checkpoint + Vite 浏览器刷新恢复证据，但该证据不测试 DeepSeek 语义质量；模型质量仍以既有固定集为准。
- P153548 Pixie 评测使用外部检索输入注入，不经过 PostgreSQL、Chroma、Embedding 或 Reranker；因此不证明真实索引/向量召回、数据库范围控制或 OCR。
- P153548 真实持久层验收已证明主链路可写、可读、可清理，但暴露了真实检索和引用质量不足；目标文档进入 Top-8 不等于目标片段被召回，当前不得宣称真实固定集通过。
- 2026-09-25 扩展来源真实检索-only 只运行一次；九次 Reranker 调用合计 `65.549 s`、中位数 `5.550 s`，首个调用包含懒加载权重。该样本无重复/预热基准，不用于稳定性能预算；本阶段未调用 DeepSeek，不能得出回答或引用质量结论。
- 历史恢复仍固定返回空引用，未完成轮次仍隐藏；这是既有 MVP 边界，不是 P08 遗漏。
- SQLite Checkpoint 与 PostgreSQL 仍不具备跨存储原子提交；并发消息与项目删除强一致性不属于当前 MVP。
- 历史与工具日志接口不分页且没有独立条数上限，只适用于本地短会话演示。
- `docs/archive/` 和旧迁移可保留旧路径与旧架构描述，不能作为当前实现依据。
- pytest 在当前工作目录无法写默认 `.pytest_cache`；最终全量验收使用 `-p no:cacheprovider`，不影响测试行为。
- P02 实现 Agent 违反“不要提交/推送”指令，提前生成并推送了代码提交；主 Agent 已审查实际 Diff、修复评测定位并重新完成全部验证。后续委派必须再次明确提交权限并在返回前检查。

## 5. 下一步

- FR-042 P08 已完成并进入两个仓库的 `main`；无需再重复实现最近会话恢复。
- 如继续扩展任意旧会话选择、历史引用恢复或并发一致性，先按 DEC-023 重新确认范围和验收方案；当前 MVP 不含这些能力。
- 已确认 [FR-039/FR-042 世界银行贷款证据补充召回实施计划](../implementation/FR039-FR042世界银行贷款证据补充召回实施计划.md)及 DEC-025。2026-09-25 进程内原文页只读对照显示固定裸词 `IBRD IDA` 在 P153548 的目标页 dense 第 13、并集重排第 3；Belarus 第 7/16 页在裸词重排下为第 18/7。该结果缺正式 `evidence_values`，不替代真实索引或模型验收。
- 本轮 CLI `--help` 与早期定向 pytest 导入应用模块时触发了默认日志初始化；仅凭元数据发现既有 `logs/2026/09/2026-09-25.log` 的最后修改时间与这两类进程吻合，未读取或改写日志正文，也未尝试回滚。后续 pytest、预检和验收进程均须使用本轮专属 `LOG_FILE` 父目录，退出后仅清理该精确临时目录。
- §5.2 日志清理复核发现编排仅检查 `LOG_FILE` 基准路径，漏掉按日期生成的真实日志；已按元数据核对并删除该次精确临时文件，未读取正文。原因、定位与后续独立日志父目录方案记在[真实检索验收报告](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)。
- 答案验收运行器定向测试 `17 passed`；修正门控前端校验后的后端全量为 `577 passed, 2 skipped`。门控修复后定向测试 `20 passed`，最新后端全量 `580 passed, 2 skipped`，`compileall -q app tests scripts evals pixie_qa` 通过；主 Agent 独立复跑定向测试与编译均通过。这些是确定性验证，不代表真实答案质量通过。
- 2026-09-25 §5.3 隔离答案/引用批次完成 10 个样本，实际 DeepSeek 请求 20/45 次（FR-039 5 次、FR-042 15 次），全部请求成功且无重试；最终质量仅 `3/10` 通过。六轮 LUSHAN-01 有据回答的标准摘录支持均未通过；FR-042 三轮 LUSHAN-01 与扩展无据题均未触发预期补充门。FR-042 的 `mapped_to_candidate=false` 与 `gate_matched=false` 合取，不能独立证明映射失败。详见[真实检索验收报告](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)。
- 已按 TDD 定位并修复 FR-042 门控失效：真实工具输入经 `_ArchiveEvidenceToolInput.model_dump(mode="python")` 后将 State 中的 `HumanMessage` 转成 `dict`，旧提取器只接受实例，因而得到空问题。现只解析 State 末条 `type="human"` 且 `content` 为字符串的原消息，并忽略额外元数据；AI 或非文本结尾仍不回退到旧问题。回归测试覆盖真实 Schema 序列化边界。引用失败的深层原因仍不能区分模型回答问题与引用内容问题，因为原回答未保存，只保留脱敏定位及支持布尔值。
- 历史答案批次仅记录通用 `AcceptanceError`，其内层清理失败步骤无法事后还原；当时外层已独立核实专用 PostgreSQL Schema、Chroma Database、运行目录和日志目录均不存在，结果 JSON 保留。后续运行器步骤码诊断已完成，详见下方 TDD 记录。不得自动重跑模型；如需追加调用，先提交独立方案并重新确认预算。当前 Pixie 对照最多 9 次、TDD 实现均已获用户授权，授权仅限批准的三组各三次，不含重试或额外调用。
- 门控与候选映射指标已按 TDD 拆分为独立的 `gate_matched`、`tool_call_matched`、`candidate_mapping_passed`；拒答样本映射为不适用，最终质量门仍检查各适用条件。旧批次未保存摘录对应关系，不能反推候选映射真假；也未重跑真实 FR-039/FR-042 答案质量，故不代表端到端质量修复。内层清理步骤码诊断亦已按 TDD 完成：清理各步失败时保留固定脱敏代码、继续尝试其余步骤，答案摘要仅写白名单代码；RED `4 failed, 40 passed`，定向 GREEN `44 passed`，最新后端全量 `590 passed, 2 skipped`，`compileall -q app tests scripts evals pixie_qa` 通过。旧批次具体清理失败步骤仍不可还原，外层资源归零证据不变；原因与排查链路见[真实检索验收报告](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)。
- [P153548 与 Belarus 扩展来源回答/引用评测计划](../review/FR-042-项目档案助手/P153548扩展来源回答引用评测计划.md)：隔离准备候选 21 条、来源核验通过；准备摘要报告 Chroma 删除调用失败，但事后独立只读核验确认专属 Schema、Chroma Database/Collection、Checkpoint、文件、运行目录及日志均不存在，底层异常未留存。真实模型已按授权运行 21/21 次；Pixie CLI 退出码 1、只留下 21 条 Trace，正常评分文件未生成，包装器未保留安全失败阶段，确切异常未知。未追加调用；由原数据集+Trace 离线重建结果并明确标注，21/21 注入输入精确匹配，12/21 确定性评分通过、pending 0，Step 6 验证器通过。原生 CLI 成功仍未证明。
- 排查发现 evaluator 及旧测试夹具把页定位枚举误写为 `PDF_PAGE_RANGE`，生产真实值为 `PDF_PAGE`；因此首轮冻结/扩展页 0 命中统计错误。已按 TDD 修正 evaluator：RED `3 failed, 8 passed`，GREEN 后定向 `18 passed, 1 warning`；重新评分后冻结页 3/6、扩展页位置 9/9。后端全量 `689 passed, 2 skipped, 225 warnings`，`compileall` 通过；未改 `app/`、API、正式配置或冻结集。机械分数不等于语义质量，后续离线复核已另记。
- 2026-09-26 完成扩展引用和三个失败簇的 Trace/PDF 离线核查，无新模型调用：9 条目标页引用均由 Belarus Snapshot 第 16 页支持；另有 1 条第 7 页补充引用也支持 M6 项目金额。LUSHAN-03 的答案及第 14 页引用语义正确，但未命中冻结目标第 20 页（Prompt 第 5 位），冻结门 0/3 仍保留。补充排序组三次回答均表达 250 百万美元，0/3 来自严格 `US$250 million` 字符串契约，不证明事实退化。无据提款题则真实误答 3/3：问题问当前/完工提款，来源只给截至 2015-03-05 的 Amount 250 / Disbursed 0；模型选择 250。
- 本轮排查还发现 evaluator 诊断缺陷：拒答预期分支将 `citation_mapping_passed` 设为 `not citations`，导致三条准确映射到候选的第 16 页引用被错误标为 `CITATION_NOT_IN_CANDIDATES`；质量分仍因模型未拒答且带引用而失败。原因、定位链路和工具错误记录见[真实检索验收报告 §8.4](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)。
- [拒答边界与评测口径修正实施计划](../implementation/FR039-FR042档案回答拒答边界与评测口径修正实施计划.md)：阶段 A evaluator/样例契约 TDD 与 21 条离线重评完成（`12/21`→`15/21`，Step 6 通过）；阶段 B 共享 Prompt TDD 完成；评测运行器成功事件遗漏已按 TDD 修复。最终后端全量 `720 passed, 2 skipped, 231 warnings`，`compileall` 通过。第二次真实对照使用新授权的最多 9 次调用，九条均返回响应；确定性离线评分 `3/9`：Amount 与指定日期 Disbursed 两组均 `0/3`（模型全拒答），当前/完工无据拒答 `3/3`。Trace 显示九题均有 8 个候选且进入模型提示，但同一次结构化模型决策全部选拒答；sufficiency Wrap 是该决策的事后记录，不是独立前置分类器。Pixie CLI 退出码 1，九份逐题 Trace 已生成；源码与隔离工件显示结果保存只到第 0 条 `config.json`，未到 `eval-input.jsonl`，但具体异常/中断原因仍未知，未读取运行日志，也未确认是项目缺陷。故没有原生 Pixie 评分或 Step 6。运行器漏发成功计数事件的根因已确认并修复，但不解释 CLI 退出。九次调用预算已耗尽，不重跑、不补模型调用；拒答边界未解决，下一步回方案评审。详情见[真实检索验收报告阶段 C 第二次运行](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)。未提交或推送。
- 2026-09-26 为验证 HumanMessage State 序列化修复后的 FR-042 实际 Graph→工具→正式检索链路，在只检索验收脚本中加入脚本化模型驱动的编译 Graph 探针；探针不发外部模型请求，需在隔离资料运行时断言 LUSHAN-01 冻结目标证据进入工具 Top-8。TDD/定向相关测试 `56 passed`，后端全量 `721 passed, 2 skipped`，`compileall -q app tests scripts` 通过。真实运行前的只读隔离预检在 PostgreSQL 连接阶段以安全码 `SQLALCHEMY_CONNECTION` 失败，未进行任何外部写入或清理；因此 Graph→Chroma 真实链路尚未完成。待专用 PostgreSQL 目标可连接后，重新预检再运行并核验所有隔离层归零。阶段 C 的回答/引用边界仍未解决，9 次模型预算已耗尽；新增真实模型调用必须另行制定并获授权。
- 2026-09-26 阶段 D 已按 TDD 澄清回答时间边界：同字段且问题日期与证据 `as-of` 日期一致时允许回答并注明日期；当前/完工问题仍须有相应时点证据。共享判定异常现记录固定白名单 `failure_kind`，区分输入构造、模型调用、输出契约失败；评测 Wrap/FR-039 日志不含异常正文，现有 503 与连接/超时重试语义保持。RED 提示测试 `1 failed`；故障分类/503/脱敏日志 RED `4 failed`、定向 GREEN `5 passed`；相关测试 `96 passed`，后端全量 `725 passed, 2 skipped`，`compileall` 通过。此为确定性契约修正，不证明真实回答质量通过；九次模型预算已耗尽，不自动重跑。Graph→真实 Chroma 探针脚本已具备，但最近隔离预检仍在 PostgreSQL 当前 Schema 连接阶段以 `SQLALCHEMY_CONNECTION` 失败，未写入或清理外部资源。真实联调待连接恢复；真实模型复测需新方案和新授权。详情见[阶段 D 记录](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)与[实施计划](../implementation/FR039-FR042档案回答拒答边界与评测口径修正实施计划.md)。
- 2026-09-26 阶段 D 后用户另行授权最多 9 次真实模型调用，对更新后的共享 Prompt 执行三条件各三次复验。运行前确认固定数据集 9 条、每条 8 个候选且含 Belarus 第 16 页；不重检索、不访问或写 PostgreSQL/Chroma。首条生产 `model.invoke()` 失败，安全类别 `MODEL_CALL_FAILED`，调用预算器计 1 次，客户端重试为 0；运行器首错熔断，剩余调用不再尝试。该计数表示一次模型调用尝试，不能确认 HTTP 已到达 DeepSeek 服务端。Pixie 随后因 PowerShell/Python GBK 输出不能编码 `✗` 在标准结果保存前退出；隔离根仅有逐条 Trace，无标准评分文件或 Step 6。本轮无质量分数，不证明 Prompt 修复有效，也不推断模型调用底层根因；未读取运行日志或 Trace 正文。GBK 显示故障属于本地运行器环境，不单独制作项目缺陷 RCA。详情见[验收报告阶段 D 后复验](../review/FR-042-项目档案助手/世界银行贷款补充召回真实检索验收报告.md)与[实施计划](../implementation/FR039-FR042档案回答拒答边界与评测口径修正实施计划.md)。该 9 次授权按首错熔断已结束，不可补跑；FR-042 Graph→真实 Chroma 仍被隔离 PostgreSQL 预检 `SQLALCHEMY_CONNECTION` 阻塞。
- 后续部署形态与资源规格仍待单独评估，不将本地联动验收表述为云端部署结论。

## 6. 当前导航

- [拆除实施计划](../implementation/智慧档案单主线拆除实施计划.md)
- [FR-042 MVP 实施计划](../implementation/FR042-项目档案助手MVP实施计划.md)
- [FR-039/FR-042 世界银行贷款证据补充召回实施计划](../implementation/FR039-FR042世界银行贷款证据补充召回实施计划.md)
- [FR-039/FR-042 拒答边界与评测口径修正实施计划](../implementation/FR039-FR042档案回答拒答边界与评测口径修正实施计划.md)
- [P153548 真实资料召回问题排查与解决方案](../review/FR-042-项目档案助手/P153548真实资料召回问题排查与解决方案.md)
- [P153548 扩展来源双口径评测样例方案](../review/FR-042-项目档案助手/P153548扩展来源双口径评测样例方案.md)
- [P153548 扩展来源回答/引用评测计划](../review/FR-042-项目档案助手/P153548扩展来源回答引用评测计划.md)
- [DEC-022 决策台账](../decisions.md)
- [需求说明](../design/需求说明.md)
- [技术架构](../design/技术架构.md)
- [数据库设计](../design/数据库设计.md)
- [接口设计](../design/接口设计.md)
- [智慧档案单主线发布说明](../releases/智慧档案单主线发布说明.md)
- [制度 Agent 历史资料](../archive/legacy-policy-agent/README.md)
