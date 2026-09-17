# 项目档案助手参考 Trace 核验

## 输入与执行结果

- `reference-trace-catalog.jsonl` 使用一条带资料类型、项目阶段和分页条件的目录问题；真实经过 Bearer HTTP、项目、ARCHIVE 会话、LangGraph 和 DeepSeek，消息、历史和工具审计接口均返回 `200`。
- `reference-trace-follow-up.jsonl` 使用两轮事实追问；第一轮依次调用目录和证据工具，第二轮在同一会话中省略主语并再次调用证据工具，两轮消息接口均返回 `200`。
- DeepSeek 实际网络请求分别为 2 次和 7 次；Pixie 当前会为同一个请求记录两条相同 LLM Span，因此 Trace 中的 LLM Span 数量分别为 4 和 14，不能把该数量直接当作模型请求次数。

## Wrap 覆盖

目录 Trace 共 6 个 Wrap：

- `archive_agent_catalog_result`
- `archive_agent_safe_tool_result`
- `archive_agent_tool_calls`
- `archive_agent_routing_decision`
- `archive_agent_response`
- `archive_agent_conversation_state`

两轮 Trace 共 13 个 Wrap，除上述数据点外还包含证据输入；重复数据点通过 `__2`、`__3` 稳定编号，避免 Pixie 的全局名称冲突。

## 行为核验

- 目录问题最终由服务端确定性模板返回第 1 页、本页 1 份、共 1 份以及五字段安全投影。
- 第一轮事实问题最终回答 `North Star Build Lab`，证据来自 `PX-BETA-cover.txt` 第 2 行。
- 第二轮省略主语的追问最终回答“第 3 个周期”，证据来自 `PX-BETA-timeline.txt` 第 8～9 行。
- 两轮历史为 `USER / ASSISTANT / USER / ASSISTANT`，工具审计总数为 3。

## 脱敏审计

对两个 Trace 的全部 Wrap 数据执行字符串和 UUID 形状检查，未发现：

- `user_id`、`project_id`、`kb_id`、`session_id`
- `document_id`、`chunk_id`
- `score`
- `access_token`、`refresh_token`
- UUID 形状值

## 证据边界

参考世界数据复用仓库既有的虚构 PX-BETA 资料，并只在 Runnable 生命周期内替换目录与证据外部输入。该结果证明 Agent 路由、工具预算、证据判定、多轮 Checkpoint、响应投影和审计链路可运行；不证明真实 PostgreSQL/Chroma 的召回与重排质量，后者仍需固定资料集和独立验收门。
