# FR-034～FR-035 Vue 工作台接入实施计划

## 1. 目标与范围

本阶段只接入 FR-034“AI 字段建议”和 FR-035“人工草稿与字段检查”，形成从已解析文档进入七字段草稿、查看证据、逐字段检查和保存的前端闭环。后端 API、状态机和数据库结构保持不变。

本阶段不实现 FR-036 确认/取消确认、FR-037 清单关联、FR-038 正式档案、FR-039 有据问答、FR-040 删除或 FR-041 审计日志。页面不得伪造这些后续状态或成功结果。

## 2. 已核对的真实后端契约

- `POST /projects/{project_id}/documents/{document_id}/suggestions`：仅从 `PARSED` 发起首次 AI 建议，无请求体，返回完整 `ArchiveDraftRead`。
- `POST /projects/{project_id}/documents/{document_id}/suggestions/retry`：仅从 `SUGGESTION_FAILED` 重试，无请求体，返回完整草稿。
- `POST /projects/{project_id}/documents/{document_id}/suggestions/regenerate`：仅在 `PENDING_CONFIRMATION` 且尚无人工编辑时允许；请求体只有当前 `expected_version`。
- `POST /projects/{project_id}/documents/{document_id}/manual-draft`：从 `PARSED` 或 `SUGGESTION_FAILED` 创建七字段空白草稿，无请求体。
- `GET /projects/{project_id}/documents/{document_id}/draft`：读取文档、七字段、解析快照和服务端给出的 `next_actions`。
- `PUT /projects/{project_id}/documents/{document_id}/fields/{field_name}`：提交对应值列、检查状态、来源、无证据标记、证据列表、可选原因及当前 `expected_version`，返回新的完整草稿。
- 七字段固定为 `TITLE`、`DOCUMENT_TYPE`、`DOCUMENT_DATE`、`AUTHORING_ORGANIZATION`、`VERSION_NUMBER`、`PROJECT_STAGE`、`KEYWORDS`。
- 日期只使用 `date_value`，关键词只使用 `json_value`，其余字段只使用 `text_value`；标题和资料类型不能接受为空。
- 所有接口继续只使用 Bearer Token 和项目路径范围，不增加 `X-User-ID`、`owner_id` 或知识库 ID。

## 3. 前端改动设计

### 3.1 类型和 API

在 `src/types/index.ts` 增加草稿、字段、证据、快照、枚举和字段更新请求类型。在 `src/services/api.ts` 增加上述六个项目范围方法，严格保持无请求体动作和 `expected_version` 请求体的差异。

### 3.2 Store

在 `src/stores/archive-workspace.ts` 增加当前草稿状态和六个动作。每次响应同时替换草稿及文档列表中的同一文档；切换项目或清除会话时清空草稿。异步响应返回时再次核对项目 ID 和文档 ID，避免旧项目结果覆盖当前页面。

### 3.3 组件和页面

新增独立的字段草稿面板，并由文档处理列表按后端状态显示合法入口：

- `PARSED`：生成 AI 建议或启动人工草稿。
- `SUGGESTION_FAILED`：重试 AI 建议或降级为人工草稿，并显示后端受控错误。
- `PENDING_CONFIRMATION`、`PENDING_RECONFIRMATION`：打开草稿检查；重新生成按钮只在界面能够证明未人工编辑时显示，最终仍由后端校验。
- 七字段按其真实值类型提供输入控件；保存时始终携带草稿响应中的当前文档版本。
- 展示字段来源、检查状态、无证据标记及原文摘录和位置。人工有值但无证据时明确显示“人工填写、无原文证据”。
- 不提供确认按钮；完成七字段检查后明确提示下一阶段是 FR-036。

## 4. TDD 执行顺序

Luna 必须按以下顺序逐项执行，并保留实际命令结果：

1. **RED：API 契约测试**——先为六个端点补充失败测试，运行并确认因方法缺失而失败；再写最小 API 实现使其通过。
2. **RED：Store 行为测试**——先覆盖草稿保存、项目切换清理、过期响应隔离和文档状态同步，确认失败；再写最小 Store 实现。
3. **RED：组件行为测试**——先覆盖各状态动作、七种字段值映射、版本提交、证据与无证据提示，确认失败；再实现组件。
4. **RED：页面接线测试**——先验证文档行能够进入草稿面板并触发 Store 动作，确认失败；再完成 View 接线。
5. 每个 GREEN 后运行相关测试；全部实现后运行 `npm run typecheck`、`npm run test`、`npm run build`。

RED 证据必须是新增测试对缺失行为的真实失败，不能使用语法错误、错误断言或人为抛错制造失败。

## 5. 审查与验收

主 Agent 审查类型与后端 Schema 一致性、URL 和请求体、Bearer 身份边界、状态动作、乐观锁版本、证据展示、项目切换隔离及是否越出 FR-034/035。

前端目录 `mini-rag-milvus-vue` 当前不是 Git 仓库，无法提供该目录的 `git diff`。本阶段以修改前文件哈希、逐文件审查、测试与构建结果记录变更证据；不能把这种审查描述为 Git Diff 审查。

完成静态和自动化测试后，再启动 FastAPI 与 Vite，分别验证直连后端和 Vite `/api` 代理。人工草稿路径不调用外部模型；AI 建议的真实验收必须调用真实 DeepSeek，不能以 Mock 结果代替，并在调用前核对测试材料与外发授权范围。
