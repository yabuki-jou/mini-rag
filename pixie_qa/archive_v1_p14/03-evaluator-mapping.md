# D5 Evaluator Mapping

本文件只描述 P14 D5 的最小工程验证映射。它不复用根目录已删除请假领域的
`pixie_qa/evaluators.py`，也不把工程契约测试当作真实问答质量结论。

| 评测维度 | Pixie evaluator | 类型 | 适用样本 | 选择理由 |
| --- | --- | --- | --- | --- |
| 回答状态、引用和证据决策结构 | `pixie_qa/archive_v1_p14/evaluators.py:archive_answer_contract` | 确定性 custom evaluator | 全部 4 条 | 检查 `answer_status`、非空回答、引用字段/数量，以及 `has_evidence` 与服务分支一致。 |
| 回答证据忠实性与引用可追溯性 | `pixie_qa/archive_v1_p14/evaluators.py:archive_evidence_faithfulness` | Agent evaluator | 直接有据、多个候选、两条缺事实样本 | 需要将回答业务断言逐项对照候选摘录，不能用字段存在性代替语义判断。 |
| 缺证据时的拒答质量 | `pixie_qa/archive_v1_p14/evaluators.py:archive_refusal_quality` | Agent evaluator | 相近但缺事实、高分无直接证据；同时检查有据样本不误拒答 | 专门覆盖“高分候选不等于事实充分”的失败模式，要求证据不足时不编造。 |

## 数据与结果边界

`archive-question-d5-smoke.json` 的候选均为 PX-ALPHA/BETA/GAMMA/DELTA 虚构项目的
脱敏文本、虚构 UUID 和文件名。每条 `eval_input` 都包含一个通过 jsonpickle 编码的
完整 `ArchiveRetrievalResponse`，可由 Pixie 0.8.6 反序列化为 Pydantic 对象。

主 Agent 将先验证 `load_dataset`、输入对象反序列化、Runnable 导入和 `compileall`，
再执行真实 `pixie test` smoke。不得据此宣称 12 题召回、阈值、拒答或 DeepSeek 质量
门通过；D5 仍只是工程与语义评审链路的最小样本。
