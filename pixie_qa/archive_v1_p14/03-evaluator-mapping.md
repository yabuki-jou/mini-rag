# Archive Question Evaluator Mapping

本文件描述 P14 D5 最小工程集与项目文档有据问答集的评测映射。两者均不复用根目录已删除
请假领域的 `pixie_qa/evaluators.py`，也不把回答层评测当作真实检索质量结论。

| 评测维度 | Pixie evaluator | 类型 | 适用样本 | 选择理由 |
| --- | --- | --- | --- | --- |
| 回答状态、引用和证据决策结构 | `pixie_qa/archive_v1_p14/evaluators.py:archive_answer_contract` | 确定性 custom evaluator | 全部 4 条 | 检查 `answer_status`、非空回答、引用字段/数量，以及 `has_evidence` 与服务分支一致。 |
| 回答证据忠实性与引用可追溯性 | `pixie_qa/archive_v1_p14/evaluators.py:archive_evidence_faithfulness` | Agent evaluator | 直接有据、多个候选、两条缺事实样本 | 需要将回答业务断言逐项对照候选摘录，不能用字段存在性代替语义判断。 |
| 缺证据时的拒答质量 | `pixie_qa/archive_v1_p14/evaluators.py:archive_refusal_quality` | Agent evaluator | 相近但缺事实、高分无直接证据；同时检查有据样本不误拒答 | 专门覆盖“高分候选不等于事实充分”的失败模式，要求证据不足时不编造。 |
| 每题确定答案与引用定位 | `pixie_qa/archive_v1_p14/evaluators.py:archive_v1_p02_quality_gate` | 确定性 custom evaluator | 项目文档有据问答集 4 条 | 用 `expected_answer` 检查答案包含关系，并核对 citation 的文件名、行范围和原文摘录。 |

企业规模扩展集的 GROUNDED 标注可以额外提供 `expected_answer_fragments`。当前 GROUNDED-04、
06、10、11、12 使用该标注；提供时评测器要求
所有必要事实片段都经过保守归一化后出现在回答中；未提供时继续使用 `expected_answer` 的整句
包含规则，保证 AV1-P02 固定集和项目文档集保持兼容。`enterprise-capture` 只把该标注写入
离线 `eval_metadata`，不会进入生产请求。

## 项目文档有据问答集

`datasets/archive-question-project-grounded.json` 包含 4 条 `GROUNDED` 问题，来源为当前仓库
的 `docs/decisions.md`、`docs/stage/handoff.md`、`README.md`，并使用 `AGENTS.md` 中的一条
架构规则作为干扰候选。它覆盖正式 bge-base/768/`evidence_values`、Top-5 到 Top-8、
Q-01 的证据充分性误判，以及 Vue E2E 与 Q-01 的独立任务边界。`expected_evidence` 保存
文件、行号和原文摘录，材料契约测试还会回读源文档，防止候选定位随文档变化而静默漂移。

该集合的候选是预先捕获并注入的 `ArchiveRetrievalResponse`。因此评测结果只回答“生产问答层
在已经拿到这些证据时，是否正确作答、引用并忠于证据”，不能证明真实 Chroma 是否能召回
这些片段，也不能证明真实 Reranker 排序、项目隔离或延迟指标。

## 数据与结果边界

`archive-question-d5-smoke.json` 的候选均为 PX-ALPHA/BETA/GAMMA/DELTA 虚构项目的
脱敏文本、虚构 UUID 和文件名。每条 `eval_input` 都包含一个通过 jsonpickle 编码的
完整 `ArchiveRetrievalResponse`，可由 Pixie 0.8.6 反序列化为 Pydantic 对象。

主 Agent 将先验证 `load_dataset`、输入对象反序列化、Runnable 导入和 `compileall`，
再在单独授权和真实模型环境具备时执行 `pixie test`。不得据此宣称 12 题召回、阈值、
拒答或 DeepSeek 质量门通过；D5 与项目文档集都只覆盖各自声明的回答层边界。
