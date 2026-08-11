# 评审器映射

> **历史评测输入快照：** 本文映射了已删除的请假与人工确认能力，只用于解释历史
> Pixie 评测结果。当前项目与智慧档案 V1 的验收范围以现行需求、架构及后续测试计划
> 为准。

## Agent 语义评审器

| 评审器 | 覆盖标准 | 适用场景 |
| --- | --- | --- |
| `pixie_qa/evaluators.py:routing_and_authorization` | 工具选择、缺参不调用、身份与知识库参数隔离 | 全部 |
| `pixie_qa/evaluators.py:grounded_business_answer` | 制度引用、空结果拒答、余额和申请事实一致性 | 制度、余额、申请查询 |
| `pixie_qa/evaluators.py:safe_write_and_failure` | 缺参追问、草稿确认边界、安全失败回答 | 请假、故障场景 |
| `pixie_qa/evaluators.py:response_quality` | 相关性、清晰度、无隐藏实现信息 | 全部 |

## 确定性评审器

| 评审器 | 覆盖标准 | 适用场景 |
| --- | --- | --- |
| `pixie_qa/evaluators.py:agent_contract_check` | 预期工具名、执行状态、工具参数无身份字段 | 全部 |

## 分配规则

- 数据集默认使用工具契约、路由授权和回答质量三个评审器。
- 制度、余额和申请查询额外使用事实依据评审器。
- 请假补参、人工确认和失败场景额外使用安全写入与失败处理评审器。
- 开放式中文回答不使用 `ExactMatch`，语义正确性由 Agent 评审器结合轨迹判断。
