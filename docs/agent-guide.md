# Agent 逻辑导览

> **运行时说明（2026-08-07）：** 制度检索代码已改为使用 Chroma HTTP 客户端。它通过服务端
> 注入的 `user_id + kb_id` 执行 metadata 过滤；本地 Chroma 迁移与健康连通已验证，向量重建与
> 阈值标定仍未完成。云端部署是否进行以及具体方案暂定至 V1 功能完成后，详见
> `docs/chroma-migration-decision.md`。

当前 Agent 是单图、只读的企业知识库检索 Agent。

> 本文只描述当前已实现的制度检索 Agent 基座。智慧档案 V1 已完成 Parser 规则冻结、
> 虚构验收资料、数据库/模型/公共授权基础、P04 前置模型分层、项目 CRUD/模板复制 API、
> 清单项 CRUD/派生状态 API、P05 项目内上传、P06 正式解析/快照/重试和 P07 手工草稿/字段检查；AI 建议与正式归档状态机尚未实现。它不复用本 Agent 作为归档状态或档案问答的实现依据。

| 层 | 文件 | 职责 |
|---|---|---|
| HTTP | `app/routers/agent.py`, `app/schemas/agent.py` | 会话、消息、历史、审计契约 |
| 应用服务 | `app/services/agent_service.py` | 所有权、Graph 调用、引用和审计转换 |
| Graph | `app/agents/admin/graph.py` | 上下文校验、模型与 ToolNode 闭环 |
| Runtime | `app/agents/admin/runtime.py` | SQLite Checkpoint 生命周期 |
| Tool | `app/agents/tools/policy_tools.py` | 使用注入范围调用 RAG 检索 |
| 存储 | PostgreSQL、Chroma、Checkpoint SQLite | 业务事实、向量索引、Graph 状态 |

调用过程：服务端从 `AgentSession` 注入 `user_id` 和 `kb_id` → Graph 校验
UUID → DeepSeek 决定是否调用 `search_company_policy(query)` → Tool 使用固定范围
检索 Chroma → 模型根据真实结果回答 → 应用服务返回引用并保存脱敏工具日志。

模型只能生成 `query`，不能生成或覆盖身份。工具日志仅记录是否提供查询、
查询长度、是否命中、结果数量、耗时和错误码，不保存制度正文与内部异常。

PostgreSQL 保存 Agent 会话和审计；Checkpoint SQLite 保存消息与授权范围。两者
不建立外键。当前没有写工具、`interrupt/resume` 或人工决定接口。
