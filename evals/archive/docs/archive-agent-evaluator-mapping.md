# FR-042 项目档案助手评测器映射

完整的八项标准映射、适用范围和精确 callable 路径见 `pixie_qa/03-evaluator-mapping.md`；实现位于 `pixie_qa/evaluators.py`。

本轮将语义判断交给三个 Agent evaluator，将工具预算、响应/引用/历史/审计形状、Trace 脱敏和受控失败交给三个确定性 evaluator。内置通用文本 evaluator 不足以判定“当前轮最后一次工具结果”与多轮证据新鲜度，因此未作为冻结门槛。
