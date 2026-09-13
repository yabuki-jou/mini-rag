# 制度 Agent 评测器映射

本表把 `eval-criteria.md` 的制度 Agent 标准映射到评测观测点和评审器。评测器只消费
当前 HTTP/Graph 运行产生的观测，不创建或修改制度数据，也不替代真实检索基础设施验收。

| 标准 | 观测点 | 评审器 | 检查重点 |
| --- | --- | --- | --- |
| P-01 | `agent_tool_calls`、`agent_routing_decision` | `policy_agent_contract`、`policy_clarification_refusal_routing_safety` | 明确制度问题只调用 `search_company_policy`；含糊和非制度请求不误调用 |
| P-02 | `agent_tool_calls`、`authorized_context` | `policy_agent_contract`、`policy_clarification_refusal_routing_safety` | 工具参数只保留查询文本；不得出现用户、知识库或令牌身份字段 |
| P-03 | `policy_retrieval_result`、`agent_response` | `policy_evidence_faithfulness` | 金额、条件、时间、范围和审批结论必须由检索证据支持 |
| P-04 | `policy_retrieval_result`、`agent_response` | `policy_agent_contract`、`policy_evidence_faithfulness` | 文档名、页码和引用只能来自本轮检索结果 |
| P-05 | `policy_retrieval_result`、`agent_response` | `policy_clarification_refusal_routing_safety` | 检索为空时明确说明依据不足，不根据常识补全制度结论 |
| P-06 | `agent_routing_decision`、`agent_response` | `policy_clarification_refusal_routing_safety` | 信息不足时提出最小澄清问题，不猜测缺失条件 |
| P-07 | `agent_response` | `policy_agent_contract` | 状态为 `COMPLETED`，回答和请求 ID 非空，来源结构可验证 |

确定性 `policy_agent_contract` 负责结构和安全边界；`policy_evidence_faithfulness` 与
`policy_clarification_refusal_routing_safety` 使用 Pixie `create_agent_evaluator` 进行语义
评审。语义评审结果不能单独宣称真实 DeepSeek、PostgreSQL、Chroma 或 BGE 验收通过。
