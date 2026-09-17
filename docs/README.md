# 后端文档导航

本目录维护 Mini RAG 后端的产品、架构、数据、接口、实施、评测和验收资料。实际代码与 Git
状态优先于状态文档；稳定决策以 `decisions.md` 为准，当前执行状态以 `stage/handoff.md` 为准。

## 当前事实源

- [需求说明](design/需求说明.md)
- [技术架构](design/技术架构.md)
- [数据库设计](design/数据库设计.md)
- [接口设计](design/接口设计.md)
- [决策台账](decisions.md)
- [当前交接](stage/handoff.md)
- [最终验收报告](review/验收报告.md)

## 实施与质量

- [智慧档案 V1 实施计划](implementation/智慧档案V1实施计划.md)
- [项目档案助手 MVP 实施计划](implementation/FR042-项目档案助手MVP实施计划.md)
- [既有检索与智能体实施计划](implementation/既有检索与智能体实施计划.md)
- [P14 检索质量改进](review/P14-rag检索质量改进/检索质量问题分析与改进策略.md)
- [FR-042 真实模型质量评测](review/FR-042-真实模型质量评测/评测复盘.md)
- [FR-042 本地后端闭环验收](review/FR-042-本地后端闭环验收/验收复盘.md)

## 目录职责

- `design/`：当前需求、架构、数据库和 API 契约。
- `implementation/`：后端实施计划与代码导览。
- `review/`：当前评审、质量评测和验收结论。
- `stage/handoff.md`：短小的当前状态快照。
- `releases/`：版本发布说明。
- `archive/`：历史 stage 和历史评审资料，只用于追溯，不作为当前实现依据。
- `codebase/`：代码结构说明，涉及现状时仍须核对实际代码。

Vue 专属实施与验收资料已迁至
[mini-rag-vue](https://github.com/yabuki-jou/mini-rag-vue/tree/main/docs)；后端只保留服务端契约和后端验收边界。
