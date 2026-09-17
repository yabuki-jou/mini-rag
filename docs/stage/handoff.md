# Mini RAG 当前交接

## 1. 当前分支与环境

- 仓库：`mini-rag-handwrite`
- 当前整理分支：`codex/docs-reorganization`
- 当前运行与验收基线：本地 FastAPI、PostgreSQL、Chroma、本地 BGE/Reranker、DeepSeek 和相邻 Vue 工作台。
- 本轮只整理文档及修复因文档路径移动导致的评测来源路径，不改变业务 API、数据库或 Agent 行为。
- 提交时必须排除 `scripts/policy_collection_embedding_rebuild.py` 及其测试中的既有无关修改。

## 2. 已完成里程碑

- 智慧档案 V1、FR-030～FR-041 Vue 接入和本地真实代理链路已完成。
- FR-042 项目档案助手 MVP 的 P00～P07、PostgreSQL `0011_archive_agent_scope`、第四轮真实 DeepSeek 固定集和本地后端闭环已完成。
- FR-042 固定集最终为 `17/17`；目录、有据、无据、隔离、多轮、隐私和受控失败均通过。
- FR-042 Vue 真实代理闭环已完成；Vue 专属实施计划和验收记录已迁入前端仓库并形成独立提交。
- 历史 stage 与旧设计评审已归档，验收报告已归位到 `docs/review/`。
- 文档路径调整触发的企业评测来源测试已按 TDD 修正；最终后端全量回归为
  `601 passed, 2 skipped, 207 warnings`，`compileall app tests scripts evals` 通过。

## 3. 未完成与待确认

- 本轮文档整理已完成最终差异主审和后端本地提交。
- 文档整理分支已推送至 `origin/codex/docs-reorganization`，尚未合并。
- 是否继续优化 P14 固定集中的 Q-01 安全漏答，仍需另立单变量方案并单独确认。
- 云端部署、容量、拓扑和生产资源规格仍未决定。

## 4. 已知风险

- SQLite Checkpoint 与 PostgreSQL 不具备跨存储原子提交；FR-042 MVP 不承诺并发消息与项目删除的强一致性。
- 历史、工具日志接口不分页且没有独立条数上限，只适用于当前本地短会话演示。
- `docs/archive/` 中的路径、状态和技术选择可能已经过期，不能作为当前实现依据。
- 自动化测试、真实模型固定集和真实 HTTP 代理分别证明不同边界，不能互相替代。
- 本地 `.env`、业务数据、Token、运行日志和忽略的评测结果不得进入提交。

## 5. 文档导航

- [文档总览](../README.md)
- [决策台账](../decisions.md)
- [接口设计](../design/接口设计.md)
- [FR-042 实施计划](../implementation/FR042-项目档案助手MVP实施计划.md)
- [最终验收报告](../review/验收报告.md)
- [FR-042 真实模型质量评测](../review/FR-042-真实模型质量评测/评测复盘.md)
- [FR-042 本地后端闭环验收](../review/FR-042-本地后端闭环验收/验收复盘.md)
- [Vue 工作台文档](https://github.com/yabuki-jou/mini-rag-vue/tree/main/docs)
- [历史交接快照](../archive/stage/handoff-2026-09-17.md)
