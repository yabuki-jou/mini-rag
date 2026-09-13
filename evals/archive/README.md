# 智慧档案 V1 P14 评测材料

本目录保存智慧档案 V1 的 P14 后端质量评测材料。`pixie_qa/` 根目录只保留 Pixie
运行状态和忽略的本地结果；已删除请假领域的旧运行器、数据集与追踪不再保留。当前
制度 Agent 评测位于 `evals/policy_agent/`，两者不得互相覆盖。

本目录独立保存 Archive V1 的 Runnable、固定虚构/脱敏验收集、评审器和结果。主 Agent
将负责执行真实 DeepSeek smoke，并按既定范围记录结果和清理测试数据。

## D5 最小工程验证

本目录已提供 `runnable.py:ArchiveQuestionRunnable`、`evaluators.py`、
`datasets/archive-question-d5-smoke.json` 和 `docs/evaluator-mapping.md`。Runnable 只
接收 `question`，直接调用生产 `answer_archive_question`；数据集通过
`archive_question_retrieval` 注入完整的 `ArchiveRetrievalResponse`，并以
`asyncio.Semaphore(1)` 串行执行，因此离线解析无需 PostgreSQL/Chroma。

四条样本覆盖直接有据、多个候选、相近但缺关键事实、高分无直接证据。确定性评测器
检查回答/引用/决策结构，两个 Agent evaluator 评审证据忠实性和拒答质量。注入候选
使工程检查无需重复准备 PostgreSQL/Chroma。真实 `pixie test` smoke 已完成：`4/4`
条目通过，12 个评分项均为 `1.0`，两个无据问题均拒答；结果已完成 Step 6 分析校验。
本材料仍不能替代 12 题真实固定集验收。

## 项目文档有据问答集

`datasets/archive-question-project-grounded.json` 使用当前仓库中已经确认的项目文档事实，
提供 4 条全为 `GROUNDED` 的中文问题。材料覆盖正式
`bge-base-zh-v1.5`/768 维/`evidence_values` 基线、Top-5 扩至 Top-8 的决策、
Q-01 已召回准确证据但模型错误拒答的诊断，以及 Vue E2E 与 Q-01 后续优化应作为独立
任务处理的边界。每题通过 `archive_question_retrieval` 注入完整的 Top-8
`ArchiveRetrievalResponse`，并使用虚构 UUID 和 Chunk ID；其中包含相近但不支持答案的
干扰候选和 challenging 样本。

该集合专门测试生产问答入口拿到既定证据后的回答、引用与证据忠实性。它复用当前 Runnable
和三个既有评测器，并增加 `archive_v1_p02_quality_gate` 对每题的确定答案及文件、定位、摘录
进行机械验证。因为候选由 Pixie 输入边界注入，该集合不访问真实 Chroma，也不能证明真实
检索召回、排序、隔离过滤、数据库状态或线上延迟。真实检索质量仍以独立固定集验收为准。

2026-09-09 真实 DeepSeek 运行结果位于忽略目录 `pixie_qa/results/20260909-002713`：回答状态、
引用契约和有据不误拒答均为 `4/4`；严格整句包含门为 `2/4`。其中两题是 Markdown/连接措辞
导致的确定性评测器假阴性；Q-01 诊断题没有直接给出“已非召回失败”的结论，且出现一处证据
未明确支持的概括。全部 pending 评分、分析文件、行动计划和 Step 6 verifier 已完成。

## 企业规模资料真实链路扩展集

`scripts/generate_enterprise_rag_eval_data.py` 使用当前仓库文档的实质内容生成本地忽略的虚构企业
资料，保留来源文件与 SHA-256 便于回查，不通过重复段落凑体量。本次自然生成 2 个隔离项目、
16 份资料和 20 题；资料约 26 万字符，其中 4 份 PDF 共 30 页，问题分为 12 道有据、4 道无据
和 4 道隔离。`archive_v1_p14_acceptance.py enterprise-capture` 复用公开业务接口完成上传、解析、
字段确认、正式索引、Top-8 检索捕获和精确清理，Ground Truth 不进入生产请求。

2026-09-09 真实链路形成 88 个 Final Chunk，Top-30 候选池完整 `20/20`，有据证据 Top-8 覆盖
`12/12`，检索 P95 为 `7375.92 ms`。真实 DeepSeek 对有据题语义正确并引用 `12/12`，无据题
固定拒答 `4/4`。隔离检索没有跨项目文档 `4/4`，但隔离拒答只有 `1/4`：另外三题的事实在当前
项目也以通用规则出现，模型使用合法的当前项目证据回答，却把规则绑定到了问题中的另一项目。
因此当前扩展集证明检索过滤有效，也暴露隔离题应改用项目专属事实以及回答实体绑定需要单独评估。

严格连续子串/引用门为 `13/20`，包含 4 个自然表达造成的假阴性。40 个 Agent evaluator 已完成，
证据忠实性均值 `0.925`、拒答质量均值 `0.85`；分析、行动计划和 Step 6 verifier 均已完成。
检索与捕获结果位于忽略目录 `pixie_qa/results/enterprise-rag-20260909`，Pixie 结果位于
`pixie_qa/results/20260909-021610`。

P0/P1 修正：隔离题已使用每个项目独有的编号、日期、责任单位或审批结论，隐藏证据仍只保留在
另一项目；GROUNDED-04/06/10/11/12 支持可选 `expected_answer_fragments`，要求所有必要事实片段
匹配。该兼容标注只存在于离线评测元数据，不改变生产请求或 AV1-P02 固定集规则。

修正后的真实捕获保持 16 份资料、88 个 Final Chunk 和 20 题，候选池 `20/20` 完整，有据
Top-8 覆盖 `12/12`，检索 P95 `7871.52 ms`。同一捕获连续运行三次 DeepSeek，三次均为
有据语义正确并引用 `12/12`、无据拒答 `4/4`、隔离拒答 `4/4`、响应契约 `20/20`。
原始机械门均为 `19/20`；GROUNDED-10 因回答插入括号解释或引用标记形成新的整句匹配假阴性，
随后已补充五个必要事实片段的 TDD 覆盖，原始运行记录未回写。三次 Step 6 verifier 均通过，
汇总见 `pixie_qa/results/enterprise-rag-p0p1-20260909/stability-report.md`。

2026-09-10 在运行时 Reranker 路径修正后重新生成同规模资料并执行真实捕获：16 份资料形成
88 个 Final Chunk，Top-30 候选池 `20/20` 完整，有据目标证据进入 Top-8 为 `12/12`，检索
P95 为 `7993.01 ms`。第一次 DeepSeek 运行机械门为 `19/20`，逐题分析确认
`GROUNDED-08` 的问题只问解析第一步，而 expected_answer 多要求第二步；回答和引用本身正确。
该企业扩展标注经 TDD 最小修正后，复用同一捕获再次运行达到有据 `12/12`、无据拒答 `4/4`、
隔离拒答 `4/4`、响应契约与机械门 `20/20`，问答 P95 为 `1328.36 ms`。两个运行的 40 个
Agent evaluator 均已完成，Step 6 校验通过；本地证据位于忽略目录
`pixie_qa/results/enterprise-rag-fr036-20260910`、`pixie_qa/results/20260910-061517` 和
`pixie_qa/results/20260910-085348`。
