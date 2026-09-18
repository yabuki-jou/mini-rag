## 教学方式

当前学习与项目交付主线只围绕智慧档案 V1、FR-039 档案问答和 FR-042 项目档案助手。通用知识库、普通 Chat 和制度 Agent 已下线；本文件中较早的制度 Agent/RAG 记录仅是学习历史，不代表当前可用能力。

- 学习者的目标岗位是 AI 应用开发工程师和 Agent 开发工程师。
- 讲解时结合本项目中的真实代码，不只讲抽象概念。
- 按步骤教学，一次只发送当前步骤；学习者回复 `ok` 后再进入下一步。
- 企业行政 Agent 的 10 步架构调整阶段曾获用户明确授权自动连续推进，现已全部完成；该授权不自动延续到后续学习阶段。
- 智慧档案 V1 的需求、架构、数据库、API 与实施计划基线已确认；AV1-P01 Parser 可行性验证与规则冻结、AV1-P02 虚构验收资料与人工标注、AV1-P03 数据库/模型/公共授权基础、AV1-P04 前置模型分层调整、P04.1 项目 CRUD/模板复制 API、P04.2 清单项 CRUD/派生状态、P05 项目内上传/重复校验/容量控制、P06 正式解析/快照/重试、AV1-P07 手工草稿/字段证据/人工检查、AV1-A01 账号密码认证/JWT 会话/Bearer 身份切换的隔离验证，以及 AV1-C01 Chroma 独立可行性验证均已完成。P05 已通过 SQLite/TestClient 契约测试和当前 PostgreSQL 开发库的项目行锁并发验证，且测试数据与临时文件已清理。已确认将全部向量能力从 Milvus 迁移至 Chroma；AV1-C02 已完成代码、离线单测、本机项目命名空间切换/读写隔离删除验证，以及 Docker API + 外部 PostgreSQL + Chroma + 本地 BGE 健康验证。revision `0010_account_auth` 已在当前 PostgreSQL 开发库实际迁移并核对结构；当前以本地开发为准，云端部署、完整栈资源验证和部署拓扑均暂定至 V1 功能完成后。进入代码实践后恢复“一次一个步骤，学习者回复 `ok` 后再继续”的教学节奏，除非学习者再次明确授权连续推进。
- 每一步标题都使用 `（当前步骤/总步骤数）` 标记进度。
- 创建方法和类时，每个字段都需要说明作用，给出代码时添加适量注释，重点说明设计原因、数据流转和容易出错的地方，不必逐行注释。
- 每个实践步骤结束时，说明需要修改哪些文件、如何验证，以及预期结果。
- 后续编码统一采用 TDD：先为当前可观察行为写并运行 RED 测试，再进行最小 GREEN 实现，最后在测试保持通过时 REFACTOR；每一步的学习记录必须分别说明这三阶段的证据。LLM 输出质量另以真实模型评估和固定评估资料验证，不用 Mock 单测替代。
- 遇到错误时先根据报错和代码核实原因，不假定学习者或助手的判断一定正确。
- 如果需求、环境或代码状态存在矛盾或缺失，逐项询问或检查后再继续。

## 进度维护

- 完成一个步骤或一周的学习后，更新 `LEARNING_PLAN.md` 中的“当前进度”和对应检查项。
- 不要因为开始了某一步就将其标记为完成；必须在代码运行并通过该步验证后更新。
- 修改范围默认限定在当前项目，不修改原始参考项目 `py-doc-qa-deepseek-server`。

# AI 应用开发与 Agent 开发学习计划

## 学习目标

通过 12 周学习，掌握 Python、FastAPI、RAG、LangChain、LangGraph、Tool Calling 和基础工程化，最终完成一个可以写入简历并进行演示的智慧档案与企业文档智能项目。

总体实践方法：

```text
跑通开源项目
→ 理解核心流程
→ 修改现有项目
→ 独立仿写
→ 加入业务 Agent
→ 工程化、部署和求职整理
```

## 当前进度

当前正式交付范围已按 DEC-022 收敛为智慧档案单主线；下方早期制度 Agent、通用 RAG 和评测条目保留作学习流水，不作为当前实现、发布或验收依据。

- 第 1–8 周：已完成
- 第 9 周：智慧档案与企业文档智能的需求、架构、数据库、API 与实施计划基线已确认；AV1-P01 Parser 可行性验证与规则冻结、AV1-P02 虚构验收资料与人工标注、AV1-P03 数据库/模型/公共授权基础、AV1-P04 前置模型分层调整、P04.1 项目 CRUD/模板复制 API、P04.2 清单项 CRUD/派生状态，以及 P05 项目内上传/重复校验/容量控制已完成
- 当前进度：企业行政 Agent 架构调整 10/10 已完成；智慧档案 V1 的 P01～P13 实现切片、P09 真实确认—INDEX/取消确认清理、P11 单文档检索 P95 基线及 P10～P13 确定性测试均已完成。P14 阶段 C.2 已完成 Top-10 → Top-20 候选池实验；C.3-A 已通过开发环境隐藏诊断观测完整 Top-20，C.3-B 与 C.3-C 均已完成单变量复验：固定查询表达 `档案证据检索问题：{query}` 未改变 63/63 个 Chunk 上下文、固定集有据 `7/8`、无据 `0/2`、隔离 `2/2` 的结果，不存在可冻结的独立重排阈值。C4-A Top-30 已获独立授权并完成真实复验：候选池完整 `12/12`，公开覆盖式召回 `7/8`，严格诊断 `5/8`，其中 `GROUNDED-01/05` 为匹配语义不一致、`GROUNDED-07` 两种语义均未命中；当前无据 `0/2`、隔离 `2/2`，P95 `1819.88 ms`，无据最高分与 Top-20 基线无变化；质量门槛未通过。C4-B 已按独立授权完成仅 Reranker 查询表达复验：Embedding 保持基线，候选池完整 `12/12`，公开覆盖式召回 `7/8`，严格诊断 `5/8`，无据 `0/2`、隔离 `2/2`，P95 `3765.54 ms`，无据最高分升至 `0.986686`、`0.952463`；质量门槛仍未通过。公开接口最多返回 10 条。C4-B 阶段当时未通过质量门；后续 D6-B 的通过结果见下文。新的 Chunk/窗口调整、换模型或评测集改动仍需独立重新授权。P13 已完成真实故障恢复：故障服务返回稳定错误后，健康服务重试成功，并核验 PostgreSQL、Chroma、文件、审计和临时范围清理均为零。FR-034/035～FR-041 的 Vue 接入与真实链路验收已完成；后端最近完整回归和 `compileall` 结果以本轮验证记录为准；当前开发与验证基线为本地，云端部署与完整栈资源验证仍暂定至 V1 功能验收完成后决定。
- 第 10–12 周：P14 全链路验收、相邻 Vue 工作台联调、文档交付与最终回归已完成；后续进入验收发现项修复与提交整理
- 2026-09-13 services 分层重构已完成代码迁移与确定性回归：services 按业务域分包，档案/旧 RAG 文档生命周期和 Chroma 客户端/Collection 已拆分；`tests/services/` 已镜像新结构，包边界、模块导入、旧导入/MonkeyPatch 路径和跨模块私有导入检查已加入。services 测试 `118 passed, 1 skipped`，后端全量回归 `426 passed, 2 skipped, 121 warnings`；`compileall` 与 `git diff --check` 通过。真实 PostgreSQL、Chroma、DeepSeek 写入和 Vue E2E 未在本轮重新执行。
- 2026-09-13 评测源码目录收口已完成：制度 Agent 与智慧档案评测分别位于 `evals/policy_agent/`、`evals/archive/`，`pixie_qa/` 仅保留本地 Pixie 状态与忽略的历史结果。智慧档案迁移相关定向测试 `111 passed`，后端全量回归 `467 passed, 2 skipped, 127 warnings`；`compileall app tests scripts evals` 与 `git diff --check` 通过。本轮未重跑 DeepSeek、PostgreSQL、Chroma 或历史 Pixie 数据集。
- 2026-09-13 文档职责目录已收口：`docs/review/` 根目录的 11 份实施计划迁入 `docs/implementation/`，评审、设计补丁、冻结清单、历史对话和 P14 诊断专题保留在 `docs/review/`；仓库内旧路径引用已清零。后端全量回归 `467 passed, 2 skipped, 127 warnings`，`compileall app tests scripts evals` 与 `git diff --check` 通过。
- 2026-09-13 旧 RAG 聊天提示构造已从孤立的 `app/agents/rag_agent.py` 下沉到 `app/services/rag/prompting.py`；新增服务级单测固定引用、上下文和消息顺序，后端全量回归 `470 passed, 2 skipped, 127 warnings`。
- 2026-09-13 `tests/services/agent/` 与 `tests/services/identity/` 已补齐，直接固定 Agent 会话范围、Checkpoint 错误映射、可见历史、工具审计，以及注册、登录、刷新、注销和历史用户密码初始化行为。TDD RED 为镜像测试文件缺失，GREEN 定向回归 `36 passed`；全量回归 `483 passed, 2 skipped, 127 warnings`。
- 2026-09-13 制度 Agent 应用服务已按实际职责拆为 `sessions.py`、`messages.py`、`audit.py` 和 `execution.py`；Router 与测试同步改用新入口，不保留聚合转发层。TDD RED 为三个目标模块缺失，GREEN 定向回归 `27 passed`；全量回归 `487 passed, 2 skipped, 130 warnings`。
- 2026-09-14 V1 候选基线主审确认 services 分包、旧导入清理和现有行为回归通过；同时发现企业评测清单的项目级元数据仍引用四个已删除 service 路径。按 TDD 补充项目级来源存在性检查，RED 为 `1 failed, 9 passed`，更新四个路径后 GREEN 为 `10 passed`；完整回归 `487 passed, 2 skipped, 130 warnings`，`compileall app tests scripts evals` 与差异检查通过。修复已提交为 `de868aa`；`v1.0.0` 保持指向 `1f2d092`，`v1.0.1` 指向 `de868aa`，两个标签均已推送。
- 2026-09-17 FR-042 项目档案助手已按“最小可演示闭环”完成需求、架构、数据库、API、P00～P07 实现、第四轮真实 DeepSeek 固定集和真实后端闭环。固定集为 `17/17`；真实闭环在随机隔离 PostgreSQL Schema 中迁移到 `0011`，完成虚构 TXT 上传、解析、人工确认、Chroma 索引、目录问答、有据引用、无据拒答、三轮历史、脱敏工具审计，以及 PostgreSQL/Chroma/文件/Checkpoint/Schema 零残留清理。当前下一学习步骤为相邻 Vue 工作台最小接入和最终交付验收；本次没有迁移共享开发库 `public` Schema。
- 2026-09-18 FR042-P08 最近会话恢复已按 TDD 完成：后端新增当前项目最近 `ARCHIVE` 会话端点，Vue 进入档案助手页面后自动恢复并加载历史与脱敏工具记录；主审补齐五要素反例和登录状态变化竞态。后端全量 `492 passed, 2 skipped`，Vue 全量 `136 passed`，`compileall`、类型检查与构建通过。随后使用隔离 PostgreSQL Schema、独立 SQLite Checkpoint、真实 Vite 代理和浏览器整页刷新完成联动验收；固定本地模型只用于写入受控历史，没有调用 DeepSeek。项目删除后会话、工具记录与 Checkpoint 均归零，隔离 Schema 和临时文件已清理。下一步是提交范围审查及提交/推送；历史引用与不完整轮次仍不恢复。
- 2026-09-17 前后端文档职责已重新收口：8 份 Vue 实施计划和 FR-042 Vue 验收记录迁入前端仓库并保留源提交信息；后端历史 stage/设计评审归档，验收报告归位 `docs/review/`，两边均建立简明文档索引和当前 handoff。迁移触发的评测来源路径按 TDD 修正，RED 为来源文件缺失导致 `10 failed`，GREEN 后定向 `10 passed`；固定项目资料的旧 handoff 行号问题在完整回归中暴露并改指不变的归档快照，最终后端全量 `601 passed, 2 skipped, 207 warnings`，`compileall app tests scripts evals` 通过。
- 2026-09-13 制度 Agent 当前评测已迁入 `evals/policy_agent/`：6 条固定样例使用真实 DeepSeek 和注入的虚构制度结果完成评测，契约与人工语义评分共 `12/12` 为 `1.0`，Step 6 完整性检查通过。准备阶段发现的 768 维 Embedding 与 512 维制度 Collection 不兼容已通过空库原名重建解除；真实虚构制度文档上传、处理、Agent 问答与引用验证通过，临时 PostgreSQL 六表和对应 Chroma 范围归零。迁移脚本安全收口后定向测试 `27 passed`，后端全量回归 `465 passed, 2 skipped, 127 warnings`，`compileall` 与 `git diff --check` 通过。该单题结果不替代完整制度质量评测，共享 Checkpoint 文件也未作为临时文件整体删除。
- 当前 P14 状态：D6-B 已在 D1 base/768 独立 Collection 的实验基线完成正式 AV1-P02 12 题，结果为有据回答并正确引用 `7/8`、无据拒答 `2/2`、隔离 `2/2`，达到已确认质量门；Q-01 仍为有直接证据时的安全漏答。正式默认已切换为 bge-base/768、`evidence_values`，正式 Collection 已完成空库重建；FR-034/035～FR-041 的 Vue 接入与真实链路验收已完成，最终验收报告已按 45 项 AC 汇总证据与限制。前端 URL 与 Store 项目 ID 一致性风险已按 TDD 修复，前端全量 `122 passed`；功能代码、结构和 V1 发布基线均已收口并推送，下一步进入作品集材料。云端部署仍另行确认。
- **P14-D5 当前进度（2026-09-08，正式质量未通过）：** 结构化一次调用已实现；正式运行保持 D1 base/768、`evidence_values`、base Reranker、Top-30、`c4_a`、固定 12 题和独立 Collection。候选池完整 `12/12`，Top-5 公开覆盖 `6/8`，有据回答并正确引用 `5/8`、无据 `2/2`、隔离 `2/2`，检索 P95 `5225.84 ms`，问答调用 P95 `1658.41 ms`。用户已确认合同资料的 `DOCUMENT_DATE` 表示合同签订日期，Q-01 因此按既定 Ground Truth 定性为漏答/证据充分性判定失败；同配置 D4 快照确认 Q-02/Q-07 正确证据分别位于第 `8`/`6`，均被 Top-5 截断；实际 Prompt 还缺少 D5 方案要求的文件名/定位，导致文档字段无法稳定绑定。Pixie Step 6、完整回归 `375 passed, 2 skipped, 90 warnings`、`compileall` 和三层清理均已完成。D6-A 只增加文档绑定、D6-B 再评估 Top-8，二者仍须分别授权。
- **P14-D6-A 当前进度（2026-09-08，已完成且安全门通过）：** 用户授权只增加服务端可信文档绑定并保持 Top-5。TDD RED 新增两个行为测试并实际得到 `2 failed`；GREEN 后同文档候选共享请求内 `D1/D2` 引用，Prompt 包含文件名、定位和摘录且不暴露持久化 ID。问答服务 `18 passed`，完整回归 `377 passed, 2 skipped, 100 warnings`，`compileall` 与 `git diff --check` 通过。经用户明确授权外发固定集中的虚构/脱敏问题和证据后，复用 D5 同一 Top-5 完成真实 DeepSeek 12 题：有据仍为 `5/8`、无据 `2/2`、隔离 `2/2`；Q-07 从跨文档 V1.0 错答变为固定拒答，证据忠实 `12/12`、拒答质量 `11/12`、响应契约 `12/12`，问答阶段 P95 `1193.20 ms`。24 个 pending 评分、dataset analysis、action plan 和 Step 6 verifier 均已完成。D6-A 只证明安全改善，后续 D6-B 已按独立授权完成。
- **P14-D6-B 当前进度（2026-09-08，已完成且实验基线质量门通过）：** 用户独立授权仅将问答候选从 Top-5 扩至 Top-8，并指定由 Luna 子 Agent 实际执行。Luna 先新增第八引用映射测试，RED 实际因请求仍为 Top-5 失败，GREEN 后生产代码唯一变量改为 `top_k=8`；随后按 TDD 新增独立 `d6b-capture`，历史 D5 捕获仍固定 Top-5，评估脚本相关测试 `42 passed`。主 Agent 复核问答/运行器相关测试 `61 passed`，完整回归 `380 passed, 2 skipped, 105 warnings`，`compileall` 和 `git diff --check` 通过。真实固定集保持 D1 base/768、`evidence_values`、base Reranker、Top-30、`c4_a`、原 12 题和独立 Collection：候选池 `12/12` 完整，Top-8 公开覆盖 `8/8`，最终有据正确引用 `7/8`、无据 `2/2`、隔离 `2/2`；Q-02/Q-07 正确，Q-01 仍错误拒答。证据忠实 `12/12`、拒答质量 `11/12`、响应契约 `12/12`，检索 P95 `5801.62 ms`，问答 P95 `2046.44 ms`。Pixie 全部 pending 评分、dataset analysis、action plan 和 Step 6 verifier 已完成；PostgreSQL 临时范围归零，实验 Collection 已删除，临时 API 已停止。默认 bge-small/512 与正式 Collection 未切换。
- **历史进度覆盖（2026-08-27）：** P09 已完成真实确认—INDEX 路由纵向链路、真实 Chroma/BGE canary
  和取消确认清理验证；P10 清单关联/处理列表/正式目录/审计、P11 正式检索服务、P12 证据问答服务
  和 P13 物理删除服务的实现切片已完成。固定问题集的阈值/召回质量、DeepSeek 问答质量、P13 真实
  跨存储故障恢复，以及相邻 Vue 工作台的完整端到端验收仍未完成，归入 P14。Vue 已完成 FR-031
  清单 CRUD 的 DTO/API、Pinia Store、页面与正式 `5173/api → 8000` 代理 canary；API、Store、组件均按 TDD 记录有效 RED，
  FR-031 完成时的前端检查点为 4 个测试文件、23 个测试，typecheck 和 build 通过。真实 canary 覆盖健康、认证、项目和清单 CRUD，
  临时用户/会话/项目/知识库清理为零。Vue 的 FR-032/033 项目级上传、处理列表、首次解析和专用解析重试
  DTO/API、Pinia Store、页面与正式 `5173/api → 8000` 文件代理 canary 也已按 TDD 完成：API 方法、处理列表方法、
  四个 Store 行为和组件文件缺失的有效 RED 转为前端全量 5 个测试文件、35 个测试，typecheck/build 通过。真实 canary
  中两份虚构 TXT 均为 `201/UPLOADED`；有效文本为 `200/PARSED`，无有效文本的普通解析和专用重试均为
  `422/PARSE_TEXT_UNAVAILABLE`，最终状态为 `PARSED` 与 `PARSE_FAILED`。首次清理因 Chroma 暂不可用保守中断；
  心跳恢复后经既有物理删除服务完成文档、项目、会话、账号和内部知识库的精确清理并核对为零。当时记录为 FR-034～FR-041 仍未接入；后续 FR-034/035～FR-041 的 Vue 接入与真实链路验收均已完成。
- **P14 续做记录（2026-08-28）：** 已以 P02 的 12 个虚构问题运行真实 PostgreSQL、确认—INDEX、BGE/Chroma 固定集对照。原文、确认字段“字段名+值”和“仅字段值”三种纯稠密表示均保持项目隔离 `2/2`，但在同时拒绝两个无据问题的阈值上限下，有据召回分别为 `2/8`、`5/8`、`6/8`；均未达到已确认的至少 `7/8`，因此 `min_relevance_score` 尚未冻结，P12 真实 DeepSeek 质量也不得宣称通过。已新增 P13 真实故障恢复运行器及其测试，要求验证稳定失败、可见性阻断、文件保留、重试后的 PostgreSQL/Chroma/文件归零和脱敏审计；当前本机未实际完成故障服务启动，P13 真实恢复仍待验收。后端完整回归为 `237 passed, 2 skipped, 36 warnings`，并通过 `compileall`；FR-034～FR-041 前端接入仍待继续。
- **P14 阶段 A 实施记录（2026-08-31）：** 已按 `docs/review/P14-rag检索质量改进/P14-C2-检索质量优化方案.md` 为固定集运行器增加逐题脱敏诊断。RED 证据为缺少诊断构造函数时的导入失败，以及诊断持久化参数缺失时的 `TypeError`；GREEN 后运行器定向测试为 `17 passed`，完整后端回归为 `240 passed, 2 skipped, 36 warnings`，并通过 `compileall`。随后在健康的本机 API、PostgreSQL、Chroma 和 512 维 BGE 链路上完成真实固定集：标准证据均进入前 3 候选（6 题第 1、1 题第 2、1 题第 3），完整候选为有据 `8/8`、无据拒答 `0/2`、隔离 `2/2`；拒绝全部无据候选时为有据 `5/8`、无据拒答 `2/2`、隔离 `2/2`。未出现 `OUT_OF_SCOPE` 候选，故阶段 A 决策为进入阶段 B 的字段证据感知索引；阈值仍未冻结，P12 真实 DeepSeek 质量仍不得开始。
- **P14 阶段 B 代码记录（2026-08-31）：** `evidence_values` 已按 TDD 实现：RED 为 `4 failed, 16 passed`，原因分别是配置模式、按 Chunk 上下文参数、证据映射函数和索引入口均未实现；GREEN/REFACTOR 后，定向测试为 `21 passed`。该模式只把已确认、非无原文证据、属于当前快照且定位/锚点匹配的字段值写入对应 Chunk 的向量输入，不改变原文引用、Chunk ID、默认模式或旧三种模式。现有 `8000` 健康检查全绿，但 Codex 新启动进程不能新建远程 PostgreSQL 连接，故尚未完成真实固定集重建；阶段 B 和阈值冻结均未完成，阶段 C 不得开始。
- **P14 阶段 B 真实复验（2026-08-31，未通过）：** 本机 8000 在 `.env` 的 `evidence_values` 模式下完成真实固定集；安全统计为 12 份文档、63 个获得字段上下文的 Chunk、零覆盖文档 0，证明映射生效。完整候选为有据 `7/8`、无据 `0/2`、隔离 `2/2`；拒绝全部无据候选时为有据 `3/8`、无据 `2/2`、隔离 `2/2`，未达门槛且劣于阶段 A。由于阶段 A 已证实正确证据进入候选集，阶段 C 的既定前置条件不成立；阈值和默认模式均不冻结，后续优化必须先获得用户重新确认。
- **P14 阶段 C 边界确认（2026-09-01）：** 用户已确认只增加本地 `BAAI/bge-reranker-base`，对完成授权与元数据范围校验后的 Chroma Top-10 候选重排；不引入 BM25、混合召回、远程排序、Chunk 粒度调整或跨项目检索。模型已下载并完成一次本地加载与基础排序验证，但尚未接入服务，重排阈值和 P14 固定集结果均未产生。下一步是阶段 C 的 TDD RED 测试。
- **P14 阶段 C 实施、回归与真实固定集（2026-09-01，未通过）：** 已按 TDD 完成旧 distance 预过滤移除、固定 Top-10 候选池、范围校验后重排、`RERANKER_UNAVAILABLE` 稳定错误、并列稳定排序和独立 `reranker_score` 阈值标定运行器；对外保留兼容 dense `score`，另返回最终 `reranker_score`。RED 包含旧阈值导致高重排候选未进入模型的 `1 failed, 5 deselected`，以及最终重排分数契约的 `1 failed, 1 passed`；GREEN/REFACTOR 后阶段定向测试 `37 passed, 15 warnings`，完整回归 `255 passed, 2 skipped, 44 warnings`，`compileall` 通过。补充的清理回归保证主验收错误不能掩盖临时数据清理失败。随后在健康的本机 API、PostgreSQL、Chroma、512 维 BGE 与本地 Reranker 链路运行 12 题固定集：12 份文档共 63 个字段上下文 Chunk，零覆盖文档为 0；完整候选为有据 `7/8`、无据拒答 `0/2`、隔离 `2/2`，拒绝全部无据候选时为有据 `3/8`、无据拒答 `2/2`、隔离 `2/2`。不存在可冻结的重排阈值，P12 真实 DeepSeek 质量不得开始；验收临时账号、项目、文档、Final 向量与内部知识库已复核清理。
- **P14 阶段 C.1 双排序诊断（2026-09-01，未通过）：** 仅修改固定集运行器，复用健康本机 `8000`，未启动临时服务，也未修改检索、模型、候选池、Chunk 或阈值。RED 为 3 个失败测试，分别锁定 dense 排名必须按 distance 重建、有据题的独立重排字段、无据题最高 Reranker 候选与候选池不完整语义；GREEN 后运行器定向测试 `20 passed`，完整回归 `256 passed, 2 skipped, 44 warnings`，`compileall` 通过。真实 12 题仍为有据 `7/8`、无据 `0/2`、隔离 `2/2`；`GROUNDED-07` 的候选池为 10，标准证据 dense 与 Reranker 排名均为空，已直接证实为 Chroma 召回缺失。两个无据题的最高 Reranker 分数分别为 `0.981258` 与 `0.696090`，对应 dense distance 为 `0.289213` 与 `0.334844`。这不授权任何后续实现：Chunk 调整、查询表达、换模型和修改 AV1-P02 Ground Truth 均须重新授权；本轮虚构数据已复核清理。
- **P14 阶段 C.2 Top-20 候选池（2026-09-01，未通过）：** 用户明确授权仅将交给本地 `BAAI/bge-reranker-base` 的 Chroma 候选池从 Top-10 扩大到固定 Top-20；未改变公开 `top_k≤10`、Chunk、查询表达、模型、阈值或固定评测集。TDD RED 为配置和服务仍请求 Top-10 的 4 个失败断言；GREEN 后 P14 相关测试 `43 passed`，完整回归 `258 passed, 2 skipped, 46 warnings`，`compileall` 通过。复用健康本机 `8000` 重跑 12 题，固定集仍为有据 `7/8`、无据 `0/2`、隔离 `2/2`，不存在可冻结阈值；公开响应仍最多 10 条，故安全运行器不能仅凭返回项证明 `GROUNDED-07` 是否进入内部 Top-20。两个无据题最高 Reranker 分数仍为 `0.981258`、`0.696090`，对应 dense distance 为 `0.289213`、`0.334844`。运行器清理分支曾返回失败并遗留认证会话及 12 个解析快照，随后按用户授权精确清理并核对 PostgreSQL 用户、会话、知识库、项目、文档、Final Collection 及本地快照文件均为零。C.2 不冻结阈值，不进入 P12；Top-30、Chunk 调整、查询表达、换模型和评测集修改仍需重新授权。
- **P14 阶段 C.3-A 内部 Top-20 双排序诊断（2026-09-01，未通过）：** 按 TDD 新增仅开发环境隐藏的诊断接口和运行器脱敏投影；公开检索行为、模型、Chunk、阈值和固定集均未改变。定向检索/路由/运行器测试 `37 passed`，`compileall` 通过。复用本机 `8000` 运行同一 12 题：Chroma 原始候选和范围校验后候选均为 20；`GROUNDED-01/05/07` 的标准证据在完整 Top-20 中均未出现，dense 与 Reranker 排名均为空；两个无据题最高 Reranker 分数为 `0.981258`、`0.696090`，对应最强候选 dense distance 为 `0.289213`、`0.334844`。聚合仍为有据 `7/8`、无据 `0/2`、隔离 `2/2`，无阈值可冻结，P12 不得开始。运行器直连远程 PostgreSQL 的清理因本机权限失败，随后按用户授权精确清理并复核临时账号、会话、知识库、项目、文档、Final Collection 和解析快照为零。记录当时 C.3-B 召回侧和 C.3-C 无据分离仍需分别重新授权；当前 C.3-B 已按后续记录完成，C.3-C 仍不得自动执行。
- **P14 阶段 C.3-B `values` + 本地 Reranker 复验（2026-09-01，未通过）：** 用户明确选择只复验 `values` 表示与现有本地 `BAAI/bge-reranker-base` 组合；保持 Final Chunk、定位、引用、公开接口、查询表达、模型和固定集不变。复用提升权限后的健康本机 `8000`，同一 12 题重建 12 份确认资料，63/63 个 Chunk 获得上下文，零覆盖文档为 0；P95 检索延迟 `13105.77 ms`。完整候选仍为有据 `7/8`、无据 `0/2`、隔离 `2/2`；拒绝全部无据候选时仍为有据 `3/8`、无据 `2/2`、隔离 `2/2`。完整 Top-20 中 `GROUNDED-01/05/07` 仍未出现标准证据；两个无据题最高 Reranker 分数为 `0.981258`、`0.696090`，对应最强候选 dense distance 为 `0.289213`、`0.334844`。运行器清理仍返回失败，随后按用户授权精确删除 `p14be-*` 临时会话、知识库和账号，并核对 PostgreSQL、Final Chroma 与解析快照为零。`values` 复验未修复召回缺失，也未解决无据高分；阈值不冻结、P12 不开始，C.3-C 仍需独立产品/范围授权。
- **P14 阶段 C.3-C 查询表达复验（2026-09-01，未通过）：** 用户明确授权只调整查询表达；采用固定模板 `档案证据检索问题：{query}`，同时用于 BGE 查询和本地 `BAAI/bge-reranker-base` 输入，不使用 LLM 改写、查询扩展或换模型。为保持运行器上下文计数可用，实际复验沿用 `.env` 的 `evidence_values` 基线（此前 C.3-B 文档将其简写为 `values`，差异已在 P14 台账注明）；Top-20、Final Chunk、定位、引用、公开接口和 AV1-P02 固定集均不变。复用唯一健康本机 `8000` 完成同一 12 题：63/63 个 Chunk 有上下文，P95 `3791.19 ms`，完整候选有据 `7/8`、无据 `0/2`、隔离 `2/2`；拒绝全部无据候选时有据 `3/8`、无据 `2/2`、隔离 `2/2`。`GROUNDED-01/05/07` 仍未进入完整 Top-20；两个无据题最高 Reranker 分数为 `0.973935`、`0.707142`，对应最强候选 dense distance 为 `0.287029`、`0.311391`。运行器清理失败后，已按授权精确删除本轮 `p14be-*` 残留，并核对 PostgreSQL、Final Chroma 和解析快照均为零；查询表达未改善召回或无据分离，C.3-D 阈值标定条件未成立，P12 不开始。代码定向回归 `58 passed`，随后执行全量回归和 `compileall`。
- **P14 阶段 C4-A Top-30 候选池（2026-09-02，已完成，质量未通过）：** 用户明确授权只将 Chroma 候选池从 Top-20 扩大到固定 Top-30，公开接口仍最多返回 10 条；已按 TDD 完成配置、服务候选池、运行器 C4-A 观测和匹配语义诊断，定向测试 `50 passed, 17 warnings`，全量测试 `273 passed, 2 skipped, 46 warnings`，`compileall` 通过。复用健康本机 `8000` 完成真实固定集复验：候选池完整 `12/12`，公开覆盖式召回 `7/8`，严格诊断 `5/8`，`GROUNDED-01/05` 为匹配语义不一致，`GROUNDED-07` 两种语义均未命中；当前无据 `0/2`、隔离 `2/2`，P95 `1819.88 ms`，两个无据题最高 Reranker 分数及 dense distance 相对 Top-20 基线均无变化。按该次运行决策门记录无据分离失败，未冻结阈值；C4-B/C4-C 后续需分别授权。本次临时用户、会话、知识库、项目、文档、快照和 Final Collection 已核对为零残留。
- **文件存储空目录清理（2026-09-02，已完成）：** 用户发现 `data/files/` 留有空知识库目录；按 TDD 修改文件删除逻辑，使标准 `kb_id/document_id/filename` 路径在删除最后一个文件或解析快照后仅尝试删除空知识库目录，同知识库其他文档存在时保留目录。文件删除、P13 服务和相关路由测试 `33 passed, 8 warnings`，全量测试 `273 passed, 2 skipped, 46 warnings`，`compileall` 通过。已清理当时 `23` 个一级空目录，未触及非空上传文件、数据库或本地备份；真实运行中的 `8000` 进程需重启后才会加载此源代码改动。
- **P14 阶段 C4-B 查询表达复验（2026-09-02，未通过）：** 用户明确授权仅替换本地 `BAAI/bge-reranker-base` 的 Reranker 查询表达 `请从项目档案中查找与问题直接匹配的原文证据：{query}`，Embedding 查询保持 C4-A 基线，Top-30、Final Chunk、公开 Top-10、模型和固定集不变。按 TDD 新增受控查询模式、诊断模式字段和独立运行器阶段；定向测试 `47 passed, 19 warnings`，全量测试 `275 passed, 2 skipped, 48 warnings`，`compileall` 通过。复用健康本机 `8000` 完成同一 12 题：模式 `c4_b`，候选池完整 `12/12`，公开覆盖式召回 `7/8`，严格诊断 `5/8`，无据 `0/2`、隔离 `2/2`，P95 `3765.54 ms`；两个无据最高分为 `0.986686`、`0.952463`，相对 Top-20 基线分别上升 `0.012751`、`0.245321`，dense distance 未变化。运行器自动清理报告失败后，已按 `p14be-*` 精确补清理并核对 PostgreSQL、Final Chroma 和 P14 临时目录为零。C4-B 未改善无据分离，阈值不冻结、P12 不开始；C4-C、换模型、Chunk/窗口调整和评测集修改仍需独立重新授权。
- **P14 阶段 P13 真实跨存储恢复（2026-09-02，已通过）：** 在健康本机 `8000`、真实 PostgreSQL、Chroma 和文件系统基础上，启动仅监听回环 `8002` 的故障服务并使其连接不可用的本地 Chroma 端口。运行器验证故障删除返回 `DOCUMENT_DELETE_INCOMPLETE`、文档可见性被阻断且原文件保留；随后健康服务重试删除成功。脱敏结果中 `failure_reported`、`visibility_blocked`、`file_preserved_before_retry`、`recovery_delete_succeeded`、`postgres_zero`、`chroma_zero`、`file_zero`、`audit_retained`、`cleanup_completed` 均为 `true`。临时 `p14be-*` 用户、会话、项目和文档复核为零，故障服务已停止。此结果只完成 P13 真实恢复验收，不解除 P12 检索质量与 DeepSeek 验收阻塞，也不替代 Vue 端到端验收。
- **文件存储空目录清理（2026-09-02，已完成）：** 用户发现 `data/files/` 留有空知识库目录；按 TDD 修改文件删除逻辑，使标准 `kb_id/document_id/filename` 路径在删除最后一个文件或解析快照后仅尝试删除空知识库目录，同知识库其他文档存在时保留目录。文件删除、P13 服务和相关路由测试 `33 passed, 8 warnings`，全量测试 `273 passed, 2 skipped, 46 warnings`，`compileall` 通过。已清理当时 `23` 个一级空目录，未触及非空上传文件、数据库或本地备份；真实运行中的 `8000` 进程需重启后才会加载此源代码改动。
- P09 历史切片记录（2026-08-27 之前）：确认前置条件、`CONFIRMED` 状态转换/审计、Final Chunk/独立 Collection 契约、`ArchiveOperation(INDEX)` 内部事务编排、确认路由接入和取消确认基础路由切片，以及当时的测试和 canary 边界。当前状态以本节的 2026-08-28 P14 续做记录为准。

- **P14 阶段 D1 bge-base-zh-v1.5 隔离实验（2026-09-02，已完成，质量未通过）：** 用户授权在不改正式 `.env` 的前提下，以进程级环境变量加载外部 `bge-base-zh-v1.5`（768 维）、保持 `evidence_values`、Top-30、C4-A 查询表达、本地 `bge-reranker-base` 和 AV1-P02 固定集不变。TDD 新增实验 Collection 写入注入和重建脚本：定向测试 `11 passed`；随后全量后端回归 `279 passed, 2 skipped, 48 warnings`，`compileall` 通过。前置验证确认 `get_final_collection()` 由配置驱动、模型/Collection 缓存需重启实例、512→768 维写入被 Chroma 拒绝（本机客户端实际异常类型为 `InvalidArgumentError`）。真实固定集使用同一 12 题、12 份文档、63 个上下文 Chunk：候选池完整 `12/12`，公开覆盖召回 `8/8`（较 C4 基线 `7/8` 提升），严格候选池命中 `6/8`，严格语义不一致 `2` 题，无据拒答 `0/2`，隔离 `2/2`，P95 `3859.89 ms`；三项门槛因无据拒答未同时满足，默认 bge-small 512 维不切换，C4-C 阈值标定与 P12 继续阻塞。实验 Collection 已精确删除，正式 Collection 保持 `0` 条；PostgreSQL 临时用户、项目、文档和 P14 临时文件均核对为零。新增脚本为 `scripts/archive_v1_d1_embedding_rebuild.py`，不会写入 `ArchiveOperation` 业务记录。
- **P14 阶段 D5 正式 12 题（2026-09-08，质量未通过）：** 在不修改 `.env`、默认模型和正式 Collection 的前提下，用进程级 D1 base/768 隔离环境完成真实 PostgreSQL、Chroma、确认索引、Top-5 问答候选和 DeepSeek 结构化决策。候选池 `12/12` 完整，Top-5 公开覆盖 `6/8`，最终有据回答并正确引用 `5/8`、无据拒答 `2/2`、隔离 `2/2`；用户已确认合同资料的 `DOCUMENT_DATE` 表示合同签订日期，Q-01 按既定 Ground Truth 定性为漏答/证据充分性判定失败；Q-02 为 Top-5 目标证据缺失，Q-07 为跨文档字段误配。质量门未通过，D5/P12 不标记完成；Pixie Step 6 已完整收口，三层临时数据已核对为零。
- **P14 正式 Embedding/Collection 切换（2026-09-08，已完成）：** 用户明确授权采用 D6-B 已验证的 `bge-base-zh-v1.5`、768 维与 `evidence_values`，并重建正式 `archive_final_chunks`。按 Multi-Agent/TDD 完成默认配置、Compose 和安全重建脚本；主审相关测试 `34 passed`，全量回归 `391 passed, 2 skipped, 105 warnings`，`compileall` 通过。重建前正式 Collection、确认档案和文档计数均为 `0`；重建结果为 0 文档、0 Chunk，768 维 canary 写入后已删除。临时 API 联合健康检查确认 API、PostgreSQL、Chroma 均为 `ok`，Embedding 实际输出 `dimension=768`，随后已停止。项目 `.env` 未读取或修改，若其中存在旧覆盖值，直接本地启动时仍需由使用者私下同步。

> 进度只能在完成代码实践和验证后更新。

### FR-034/035 前端接入与真实本地 E2E（2026-09-10）

相邻 Vue 工作台已完成 FR-034/035：7 个前端文件共 55 个自动化测试通过，`npm run typecheck`、
`npm run test` 和 `npm run build` 均通过。真实链路为 `5173/api → 8000`：TXT 文档 AI 建议成功，
regenerate 成功并将版本从 `v2` 更新到 `v3`，人工保存后为 `v4` 且建议入口消失；此前失败的 PDF
重试成功并返回 `PDF_PAGE` 证据，刷新页面后草稿可以恢复。

本轮修复由真实模型输出中文字段键和格式不稳定触发；生产建议 Prompt 已明确七个英文字段键、枚举和值列契约，
并绑定 `response_format={"type":"json_object"}`。后端建议定向测试为 `11 passed`，完整回归为
`414 passed, 2 skipped`，`compileall` 与 `git diff --check` 通过。

该阶段记录的 Embedding 健康阻塞已在后续 FR-036 复验中解除。临时用户、项目、3 份文档、
原文件和快照已精确清理，数据库与文件均无残留。

### FR-036 确认、取消确认与重新确认（2026-09-10）

相邻 Vue 工作台已按 Multi-Agent/TDD 接入确认、取消确认与重新确认。真实页面经
`5173/api → 8000 → PostgreSQL/Embedding/Chroma` 验证：文档从 `v9` 确认为
`CONFIRMED v10`，9 个 Final Chunk；取消后为 `PENDING_RECONFIRMATION v11`，Final Chunk 为 0；
重新确认后为 `CONFIRMED v12`，重建 9 个 Final Chunk，刷新后状态仍保持。

前端最终为 7 个测试文件、65 个测试通过，`npm run typecheck` 与 `npm run build` 通过；后端确认
相关测试 10 个通过，全量回归为 `414 passed, 2 skipped, 121 warnings`，`compileall` 与差异检查通过。
当前 `/health` 为 `200`，API、database、Chroma 与 768 维 Embedding 全绿。FR-036 验收当时的
`RERANKER_UNAVAILABLE` 已在使用者私下修正运行时配置并重启 8000 后解除，正式 RAG 结果见下一节。
临时文档、项目、账号、会话、内部知识库和 Final Chunk 已全部核对为零；FR-037～FR-041 的 Vue 接入与真实链路验收已完成。

### Reranker 恢复后的企业规模真实 RAG 复测（2026-09-10）

重新生成 2 个隔离项目、16 份虚构企业资料和 20 题，其中 4 份 PDF 共 30 页。真实业务接口形成
88 个 Final Chunk，Top-30 候选池 `20/20` 完整，有据目标证据进入 Top-8 为 `12/12`，检索 P95
为 `7993.01 ms`。第一次 DeepSeek 运行响应契约 `20/20`、机械门 `19/20`；逐题核对确认
`GROUNDED-08` 的问题只问第一步，正确回答和引用却被包含第二步的 expected_answer 误判。

Luna 按 TDD 只收窄该企业扩展题的 expected_answer，保留完整证据原文，未修改 AV1-P02 或生产
RAG；生成器测试 `10 passed`。复用同一捕获再运行 DeepSeek 后，有据语义正确并引用 `12/12`、
无据拒答 `4/4`、隔离拒答 `4/4`、响应契约和机械门 `20/20`，问答 P95 为 `1328.36 ms`。
两次运行各 40 个 Agent evaluator 均已人工评分，Step 6 校验通过。临时数据库范围和正式 Collection
均复核为零；当前没有证据要求修改生产检索、Prompt、模型或回答服务。最终后端全量回归为
`415 passed, 2 skipped, 121 warnings`，`compileall` 与差异检查通过。

> **续做更新（2026-08-27）：** P09 已完成真实确认—INDEX 路由纵向链路、真实 Chroma/BGE canary
> 和取消确认清理验证；P10 清单关联/处理列表/正式目录/审计、P11 正式检索服务、P12 证据问答
> 服务和 P13 物理删除服务的实现切片已完成。P10～P13 相关测试共 `20 passed`；P11 真实单文档
> canary 使用 512 维 cosine，命中 1 条，10 次请求 P95 约 `125.97 ms`。固定问题集的阈值/召回
> 质量、DeepSeek 问答质量、P13 真实跨存储故障恢复和相邻 Vue 工作台完整端到端验收仍待 P14。

## 第一阶段：基础准备（第 1–2 周）

### 第 1 周：Python 与项目环境

学习内容：

- Python 常用语法、函数、类和异常处理
- 文件读写与 JSON
- `pip`、Conda 虚拟环境
- 环境变量和 `.env`
- Git 与 GitHub 基础操作

验收目标：能看懂普通 Python 项目，并独立创建环境、安装依赖和启动项目。

### 第 2 周：FastAPI 与 LLM API

学习内容：

- FastAPI 路由
- 请求参数和响应模型
- Pydantic 参数校验
- 异常处理
- 调用云端 DeepSeek API
- 普通响应与流式响应
- Swagger 接口测试

验收目标：独立完成一个调用 DeepSeek 的聊天接口。

## 第二阶段：小型 RAG 项目（第 3–5 周）

### 第 3 周：RAG 基础流程

学习内容：

- `Document` 对象和文档加载器
- 文本切分与 Chunk
- Embedding 和语义相似度
- Chroma 向量数据库
- Retriever 检索器
- Prompt 与上下文拼接

需要掌握的数据流：

```text
上传文档 → 解析文档 → 切分文本 → Embedding
→ 写入向量库 → 检索相关文本 → 交给大模型回答
```

### 第 4 周：精读 GitHub RAG 项目

参考项目：`YuiGod/py-doc-qa-deepseek-server`

重点理解：

- 项目目录结构
- 文档上传和数据库记录
- 文档加载与切分
- BGE Embedding 模型
- Chroma 持久化
- DeepSeek 问答
- 会话和聊天记录

### 第 5 周：改造 RAG 项目

已完成的主要改造：

- [x] Ollama 改为云端 DeepSeek
- [x] Embedding 模型改为本地加载
- [x] 配置集中到 `.env`
- [x] 路径改为项目相对路径
- [x] 文档和向量库整理到 `data/`
- [x] 全量向量化改为增量同步
- [x] 增加文档来源元数据和回答引用
- [x] 改善聊天记录保存和参数校验
- [x] 增加测试并更新 README

## 第三阶段：企业级 RAG 与 mini RAG（第 6–7 周）

### 第 6 周：分析企业级 RAG 项目

可选择 Dify、MaxKB、RAGFlow、FastGPT 或 QAnything。无需通读全部源码，重点分析：

- 文档上传、解析和 Chunk 策略
- Embedding、混合检索和 Rerank
- 引用溯源和知识库管理
- 用户权限、多租户和对话历史
- 任务队列与异步处理

### 第 7 周：完善自己的 mini RAG

目标功能：

- 文档上传、删除和列表
- 增量向量同步
- 文档删除后清理对应向量
- 相似度分数与检索阈值
- 引用文档名称和文本片段
- 会话管理、统一异常处理和日志记录
- 测试与 README

## 第四阶段：Agent（第 8–9 周）

### 第 8 周：Tool Calling 与 LangGraph

当前实践项目：企业知识库检索 Agent 基座。员工请假领域已从项目删除，历史
完成记录不再代表当前代码能力。

当前正式工具：

```text
search_company_policy      查询当前会话绑定的制度知识库
```

已完成的基础练习：

- [x] 理解 Tool Calling 边界并定义 `@tool`
- [x] 使用 Pydantic/LangChain Schema 限制模型参数
- [x] 让 DeepSeek 判断是否调用工具并生成查询文本
- [x] 使用 LangGraph 实现 State、节点、边和条件路由
- [x] 练习参数错误、工具异常、失败重试和脱敏日志
- [x] 使用独立 SQLite Checkpointer 恢复多轮消息和授权范围
- [x] 将业务数据库从 SQLite 切换为 PostgreSQL 配置与 Alembic

当前流程：

```text
用户输入
→ 服务端从 AgentSession 注入 user_id + kb_id
→ DeepSeek 判断是否调用 search_company_policy
→ Chroma 在固定范围内检索
→ DeepSeek 仅依据工具结果回答
→ PostgreSQL 保存脱敏工具日志
→ Checkpoint SQLite 保存对话状态
```

当前验收目标：能够解释模型、LangGraph、Tool、Application Service、
PostgreSQL、Checkpoint SQLite 和 Chroma 的职责边界，并通过 Swagger 演示制度
问答、无依据拒答、多轮历史、会话越权保护和脱敏调用日志。

**P14-D1 与路径 A 记录（2026-09-02）**：D1 使用 bge-base-zh-v1.5/768 维完成隔离实验，
固定集公开覆盖为 `8/8`、严格候选池命中 `6/8`、无据拒答 `0/2`、项目隔离 `2/2`、
P95 `3859.89 ms`；正式质量门槛未通过，实验 Collection 已清理。用户随后授权开始路径 A：
首轮只将 Reranker 替换为 `BAAI/bge-reranker-large`，Embedding、Top-30、`c4_a`、
`evidence_values`、固定 12 题和门槛保持不变。D2 已完成本地加载/延迟前置验证：默认 P95
`12885.233 ms`，`max_length=256 + batch_size=8` 最佳 P95 `12016.218 ms`，峰值工作集约
`2.30 GiB`，超过 `8000 ms` 风险线。D3 随后按独立授权完成固定候选集联合验证：base/large
公开覆盖均为 `8/8`、严格命中均为 `6/8`、无据均为 `0/2`、隔离均为 `2/2`；完整链路
P95 分别为 `3475.85 ms` 与 `9512.28 ms`，large 未通过质量门且性能风险更高。D4-A 安全快照
生产链路与 D4-B 离线阈值扫描器已按 TDD 完成确定性实现；2026-09-03 又完成 base 真实快照和全边界扫描：
12/12 候选池完整，P95 `3670.83 ms`，基线公开覆盖 `8/8`、严格命中 `6/8`、无据 `0/2`、隔离 `2/2`，
可行阈值数量为 `0`，分离余量 `-0.3226209283`。实验 Collection、临时数据库范围和业务文件均已清理；
large 仅在用户结合延迟风险再次确认后执行，不修改正式 `.env`、默认配置或正式 Collection，C4-C 仍未授权。

历史真实链路曾用本地 BGE、Milvus 和 DeepSeek 验证单文档单问题，但它不能
替代当前 PostgreSQL 版本复验，也不能证明多文档召回质量。

### 第 9 周：面向智慧档案与企业文档智能重新设计可演示业务闭环

状态：需求、架构、数据库与 API 设计基线已确认；AV1-P01～P14 本地功能与验收收口已完成。P14-D6-B 已以有据 `7/8`、无据 `2/2`、隔离 `2/2` 达到 P12 固定质量门；正式默认已切换到 bge-base/768、`evidence_values`，正式 Collection 已完成空库重建。Q-01 仍是安全漏答。2026-09-09 又以 16 份资料（含 4 份、30 页 PDF）、约 26 万字符和 20 题完成企业扩展链路；修正隔离题为项目专属事实后，同一真实 Top-8 捕获连续运行 3 次 DeepSeek，三次均达到有据语义正确并引用 `12/12`、无据拒答 `4/4`、隔离拒答 `4/4` 和响应契约 `20/20`。2026-09-10 在修正运行时 Reranker 路径后重新生成并捕获同规模资料，检索候选与三类问答再次通过；`GROUNDED-08` 用例范围经 TDD 修正后机械门为 `20/20`。当前没有证据要求修改生产回答层。FR-034/035～FR-041 的 Vue 接入与真实链路验收已完成；FR-041 还补齐了字段更新、解析重试和建议重试的成功审计写入，前端全量 `118 passed`；补齐 AC-FR-040-03 直接越权删除回归后，后端全量 `417 passed, 2 skipped`。

已确认当前唯一业务方向为“智慧档案与企业文档智能”，聚焦工程项目资料的
归档、结构化、检索与原文追溯。当前不推进标书投标、标书解析生成或投标合规
审查。目标用户、资料样例、档案字段、分类与缺失规则来源、人工确认点、评测集
和验收指标已在需求文档中确认。实现已按实施计划和逐项 TDD 推进至 P14 本地验收收口；
不能只替换知识库来宣称形成真实归档业务闭环。

## 第五阶段：工程化（第 10 周）

### 第 10 周：系统整合与部署

建议职责划分：

```text
Spring Boot：用户、认证、权限、会话、操作日志
FastAPI：RAG、Embedding、向量检索、DeepSeek、LangGraph Agent
```

学习内容：

- Dockerfile 和 Docker Compose
- 配置分环境管理与 API Key 安全
- 数据持久化
- 健康检查
- 日志和异常追踪
- Java 与 Python 服务联调

**NFR-021 日志分层记录（2026-09-14）**：文件日志已按日志记录的本地时间写入
`<LOG_FILE 父目录>/YYYY/MM/YYYY-MM-DD<扩展名>`，运行中的服务可在日、月和年边界自动切换；
同一天仍使用原有大小轮转参数。`LOG_BACKUP_COUNT` 只限制当日大小轮转备份，不自动清理其他
日期目录。

验收目标：一条命令启动完整项目，并能说明每个服务的职责和调用链。

## 第六阶段：求职准备（第 11–12 周）

### 第 11 周：整理项目

需要完成：

- 项目 README
- 系统架构图、RAG 数据流程图和 Agent 状态图
- API 文档和 Docker 启动说明
- 演示数据、截图或演示视频
- GitHub 提交记录整理

最终演示链路：

```text
上传制度文档 → 建立向量索引 → 提问并返回引用
→ Agent 查询当前会话知识库中的制度 → 查看脱敏工具调用日志
```

### 第 12 周：简历与面试

重点准备：

- Python、FastAPI、LangChain 和 LangGraph
- RAG 流程、Chunk、Embedding 和向量数据库
- Top-K、相似度阈值与 Rerank
- Prompt 与上下文管理
- Tool Calling 与 Agent 状态管理
- 异常重试、权限控制和防止工具误调用
- 项目遇到的问题、判断过程和解决方案

简历描述参考：

> 基于 FastAPI、LangChain、LangGraph、PostgreSQL、Chroma 和 DeepSeek 实现企业知识库与只读 Agent，支持文档增量向量化、语义检索、引用溯源、会话管理、制度检索工具调用、参数校验、异常重试及脱敏调用日志。

## 跨对话继续学习

在新的对话中，可以直接说明：

```text
请先阅读项目根目录的 AGENTS.md 和 LEARNING_PLAN.md，
根据其中的当前进度继续带我学习。一次只进行一个步骤，
我回复 ok 后再进入下一步。
```

## 31. 当前用户资料接口与前端显示名修复（2026-09-14）

- 新增受保护的 GET /auth/me：复用现有 Access Token 身份依赖，返回 UserRead 的公开资料；不新增密码字段、令牌字段或独立服务。
- 相邻 Vue 工作台在登录和刷新恢复会话后读取该接口；后端 name 用于页面显示的用户名，username 保留为账号。
- 前端 TDD：RED 为 API 方法缺失及登录后未请求资料；GREEN 后 API/Store 定向 68 passed、前端完整 124 passed，typecheck 与 build 通过。
- 后端路由测试 10 passed，覆盖当前资料、缺少认证和 Refresh Token 拒绝；运行中 8000 与 Vite 5173 代理的 OpenAPI 均暴露 /auth/me，匿名代理请求为 401。
