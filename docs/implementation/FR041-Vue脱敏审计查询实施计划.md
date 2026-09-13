# FR-041 Vue 脱敏审计查询实施计划

## 1. 目标与事实基线

本步骤完成 FR-041 的相邻 Vue 工作台接入，使已登录用户可以在当前项目中按时间倒序、分页并按受控操作类型查询脱敏业务审计。页面只展示后端返回的审计字段，不生成审计、不提供删除或导出。

实际代码核对发现：数据库模型已有 `actor_id`，`docs/design/需求说明.md` 的 AC-FR-041-01 与 `docs/design/接口设计.md` 的响应示例也要求返回操作人，但当前 `app/schemas/archive_catalog.py::AuditLogRead` 漏掉该字段，导致 FastAPI 响应过滤后前端无法展示操作人。本步骤先以 TDD 补齐这一已确认契约，再接入前端。

## 2. 允许范围

### 后端契约修正

- 修改 `app/schemas/archive_catalog.py`：为 `AuditLogRead` 增加只读 `actor_id: UUID`。
- 修改 `tests/routers/test_project_archive_catalog.py`：先增加失败断言，再证明响应包含操作人、时间、资源标识和脱敏摘要；补充另一用户查询被拒绝且不泄露摘要的测试。
- 不修改路由路径、筛选枚举、数据库模型、迁移、写审计时机或重试语义。

### 相邻 Vue 工作台

- 修改 `src/types/index.ts`：增加 12 个固定操作类型、审计记录及分页 DTO。
- 修改 `src/services/api.ts` 和对应测试：精确调用 `GET /projects/{projectId}/audit-logs?page=...&page_size=...`，仅在选择筛选时发送 `operation_type`。
- 修改 `src/stores/archive-workspace.ts` 和对应测试：保存审计列表、分页和筛选；项目切换时清空并使旧请求失效；刷新保留当前筛选和页码；晚到响应或错误不得污染新项目。
- 新增 `src/components/archive/ArchiveAuditPanel.vue` 及测试：显示操作类型、操作人、时间、资源类型、资源 ID 和脱敏摘要，提供 12 类受控筛选、刷新及前后翻页。
- 修改 `src/views/ArchiveWorkspaceView.vue` 和对应测试：审计路由进入真实面板并加载当前项目数据，移除 FR-041 的占位说明。
- 摘要仅展示后端当前已产生的受控字段：`status`、`version`、`document_type`、`is_required`、`matching_fields_changed`；未知键不直接渲染，避免未来错误字段进入页面。

## 3. TDD 顺序

1. RED：后端测试断言 `actor_id`，并验证另一用户请求为 403 且响应不含审计摘要；实际运行并确认因当前契约缺口失败。
2. GREEN：只为 `AuditLogRead` 增加 `actor_id`，运行后端目标测试。
3. RED：依次增加前端 DTO/API、Store 和组件/视图的行为测试，确认每组测试在实现前失败。
4. GREEN：写最小实现，使每组相关测试通过。
5. REFACTOR：只在相关测试持续通过时整理重复映射和样式。

## 4. 验收证据

- 后端目标测试通过，覆盖 AC-FR-041-01、AC-FR-041-03；既有写操作测试继续证明成功操作只产生一次审计，AC-FR-041-02 不改变。
- 前端 API 测试证明路径、分页、筛选参数精确；Store 测试证明项目隔离和竞态隔离；组件/视图测试证明真实数据、空态、加载态、筛选和翻页。
- 相邻 Vue 运行完整测试、类型检查和生产构建。
- 后端运行相关测试、完整测试和 `compileall`。
- 经 `5173/api` 真实代理链路登录临时账号、创建项目和一项可审计操作，再查询审计列表，核验 `actor_id`、时间、操作类型、分页、筛选与跨项目授权；随后精确清理临时范围。

## 5. 完成边界

本步骤完成后更新 README、LEARNING_PLAN 和 handoff 的当前事实。浏览器人工点击只有在浏览器控制恢复时补充；自动化组件测试与真实代理 API 链路分别作为页面行为和后端真实链路证据，不互相冒充。

## 6. 主审查修订：补齐已声明但未写入的三类审计

主 Agent 在首轮实现审查中以全仓检索核对 12 个枚举的写入点，确认
`ARCHIVE_FIELD_UPDATED`、`PARSE_RETRIED`、`SUGGESTION_RETRIED` 仅存在于筛选枚举，实际成功写流程没有创建审计记录。这与 FR-041 已确认审计范围冲突，必须在宣告 FR-041 完成前补齐。

- 先在既有字段更新、解析重试、建议重试 API 测试中增加失败断言，证明成功操作各写一条审计，`actor_id` 为当前 Bearer 用户，摘要不含字段正文、解析正文、模型输入输出或错误详情。
- 字段更新与字段值、证据、文档版本在同一事务写入审计；摘要只保存 `field_name` 与 `review_status`。
- 解析重试由路由注入当前用户；只有重试成功并保存新快照时，在同一数据库事务写 `PARSE_RETRIED`，摘要只保存结果 `status`。普通首次解析不写该类型，失败重试不伪造成功审计。
- 建议重试只有在模型最终成功且草稿保存时，在同一数据库事务写 `SUGGESTION_RETRIED`；摘要只保存结果 `status` 与新 `version`。首次建议和失败重试不写成功审计，模型客户端内部自动重试最终只落一条成功记录。
- 前端摘要允许键相应增加 `field_name` 与 `review_status`，仍不展示未知键。
- 不新增审计类型，不改变数据库结构、公开路径、请求体、业务状态机或模型重试次数。
