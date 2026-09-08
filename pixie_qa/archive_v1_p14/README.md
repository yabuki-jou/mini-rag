# Archive V1 P14 Evaluation Materials

本目录保存智慧档案 V1 的 P14 后端质量评测材料。根目录 `pixie_qa/` 的同名文件、
运行器和数据集属于已删除请假领域的历史快照，保留用于追溯，不能覆盖或作为当前
实现依据。

本目录独立保存 Archive V1 的 Runnable、固定虚构/脱敏验收集、评审器和结果。主 Agent
将负责执行真实 DeepSeek smoke，并按既定范围记录结果和清理测试数据。

## D5 最小工程验证

本目录已提供 `run_app.py:ArchiveQuestionRunnable`、`evaluators.py`、
`datasets/archive-question-d5-smoke.json` 和 `03-evaluator-mapping.md`。Runnable 只
接收 `question`，直接调用生产 `answer_archive_question`；数据集通过
`archive_question_retrieval` 注入完整的 `ArchiveRetrievalResponse`，并以
`asyncio.Semaphore(1)` 串行执行，因此离线解析无需 PostgreSQL/Chroma。

四条样本覆盖直接有据、多个候选、相近但缺关键事实、高分无直接证据。确定性评测器
检查回答/引用/决策结构，两个 Agent evaluator 评审证据忠实性和拒答质量。注入候选
使工程检查无需重复准备 PostgreSQL/Chroma。真实 `pixie test` smoke 已完成：`4/4`
条目通过，12 个评分项均为 `1.0`，两个无据问题均拒答；结果已完成 Step 6 分析校验。
本材料仍不能替代 12 题真实固定集验收。
