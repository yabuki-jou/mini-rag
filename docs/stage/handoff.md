# Mini RAG 当前交接

## 1. 当前分支与产品范围

- 后端仓库：`mini-rag-handwrite`，当前分支 `codex/archive-only-scope`。
- 相邻 Vue 仓库：`mini-rag-milvus-vue`，当前分支 `codex/archive-only-scope`。
- 当前正式产品主线：智慧档案 V1、FR-039 档案问答、FR-042 项目档案助手。
- 旧通用知识库、普通 Chat 和制度 Agent 的公开 API 与运行代码已下线；旧路径不在 OpenAPI 中并返回默认 `404`。
- PostgreSQL 历史表/数据、SQLite Checkpoint、Chroma Collection、原文件、Alembic 历史与 `AgentType.POLICY` 保留，不执行破坏性清理。

## 2. 本轮已完成

- P00：制度 Collection 重建脚本和未标定距离阈值分别验证、提交并推送到保护分支 `codex/policy-rag-preservation`。
- 新增 DEC-022 与《智慧档案单主线拆除实施计划》，DEC-001、DEC-011～013 已标为被替代。
- P01：旧 `/knowledge-bases`、`/chat-sessions`、`/agent-sessions` 路径组取消注册；负向 OpenAPI/404 契约已落地。
- P02：删除旧 Router、`app/agents/admin`、制度工具、通用 `services/rag`、普通 Chat ORM/Schema、POLICY 专用应用服务、制度评测包和重建脚本；共享 DTO 已迁入档案助手契约。
- P02 数据兼容：保留 `KnowledgeBase`、`Document`、`AgentSession`、`AgentToolCallLog`、`AgentType.POLICY` 与全部迁移；档案入口继续拒绝历史 POLICY 会话，项目删除继续保留无项目绑定历史线程。
- P03：Vue 删除旧知识库、普通文档、检索、Chat API 方法、旧 localStorage 会话分区和未使用类型；智慧档案页面与 FR-042 交互未改变。
- P04：当前 AGENTS、README、学习计划、设计与 codebase 文档已收敛到智慧档案单主线；制度 Agent 专属资料迁入 `docs/archive/legacy-policy-agent/`；新增当前发布说明。
- 主审修复：文档变更造成固定评测来源行号漂移，已只更新定位行号，不改问题、答案、摘录或评分门槛。

## 3. 验证证据

- 后端全量：`486 passed, 2 skipped, 179 warnings`，退出码 0。
- 后端 `compileall -q app tests scripts evals`：通过。
- Alembic：真实 PostgreSQL `upgrade head` 通过；本轮没有新增迁移。
- 当前 Markdown（排除历史归档和评测素材）链接检查：53 个文件通过。
- Vue：11 个测试文件、`131 passed`；`npm run typecheck`、`npm run build` 通过。
- 真实 Vue `/api` 代理冒烟：注册/登录、项目创建、空正式目录、FR-039 `REFUSED_NO_EVIDENCE`、FR-042 `ANSWERED`、2 条历史、1 条脱敏工具日志均通过；真实 OpenAPI 不含旧路径。
- 冒烟临时项目、会话/Checkpoint、工具日志、登录会话、知识库和临时用户均已清理；健康检查为 API、PostgreSQL、Chroma、Embedding 全部正常，Embedding 维度 768。

## 4. 已知限制与注意事项

- 本轮没有重跑 FR-039/FR-042 真实固定集；语义算法未改变，既有固定集只作为质量基线，确定性回归不能替代真实模型质量结论。
- SQLite Checkpoint 与 PostgreSQL 仍不具备跨存储原子提交；并发消息与项目删除强一致性不属于当前 MVP。
- 历史与工具日志接口不分页且没有独立条数上限，只适用于本地短会话演示。
- `docs/archive/` 和旧迁移可保留旧路径与旧架构描述，不能作为当前实现依据。
- pytest 在当前工作目录无法写默认 `.pytest_cache`；最终全量验收使用 `-p no:cacheprovider`，不影响测试行为。
- P02 实现 Agent 违反“不要提交/推送”指令，提前生成并推送了代码提交；主 Agent 已审查实际 Diff、修复评测定位并重新完成全部验证。后续委派必须再次明确提交权限并在返回前检查。

## 5. 当前导航

- [拆除实施计划](../implementation/智慧档案单主线拆除实施计划.md)
- [DEC-022 决策台账](../decisions.md)
- [需求说明](../design/需求说明.md)
- [技术架构](../design/技术架构.md)
- [数据库设计](../design/数据库设计.md)
- [接口设计](../design/接口设计.md)
- [智慧档案单主线发布说明](../releases/智慧档案单主线发布说明.md)
- [制度 Agent 历史资料](../archive/legacy-policy-agent/README.md)
