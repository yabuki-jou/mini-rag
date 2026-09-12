# FR-036 Vue 确认与取消确认实施计划

## 1. 目标与范围

本阶段只把后端已经实现的 FR-036“正式确认、重新确认、取消确认”接入相邻 Vue 工作台。用户在七个字段均已检查后，可以携带当前文档版本执行确认；已确认档案可以取消确认并立即退出正式范围；待重新确认档案可以再次确认。

本阶段不实现 FR-037 清单关联、FR-038 正式档案目录、FR-039 有据问答、FR-040 物理删除或 FR-041 审计日志。后端 API、数据库结构、确认事务和索引规则保持不变。

## 2. 已核对的后端契约

- `POST /projects/{project_id}/documents/{document_id}/confirm`
  - 请求体只能包含 `{"expected_version": <当前版本>}`。
  - 允许 `PENDING_CONFIRMATION`、`PENDING_RECONFIRMATION`；同版本 `CONFIRMED` 请求由后端幂等处理。
  - 后端验证七字段检查、必填字段、AI 证据、快照和乐观锁，并在成功时完成 Final Chunk 索引。
  - 返回 `ProcessDocumentRead`；索引失败时不得显示确认成功。
- `POST /projects/{project_id}/documents/{document_id}/cancel-confirmation`
  - 请求体只能包含当前 `expected_version`，且仅允许 `CONFIRMED`。
  - 返回 `PENDING_RECONFIRMATION` 的 `ProcessDocumentRead`，并清理正式索引；清理失败时后端仍保守排除正式可见性并返回稳定错误。
- 两个接口都只使用 Bearer Token 与项目路径授权，客户端不得提交 `owner_id`、`user_id` 或 `kb_id`。
- `ProcessDocumentRead` 的可选 `index_context_chunk_count` 用于展示索引上下文 Chunk 数，不由客户端推断。

## 3. 前端实现设计

### 3.1 类型与 API

- 在 `src/types/index.ts` 为 `ProcessDocument` 补充可选 `index_context_chunk_count`，新增只含 `expected_version` 的确认请求类型。
- 在 `src/services/api.ts` 增加确认和取消确认两个方法，严格使用项目与文档路径，并只发送当前版本。

### 3.2 Store

- 在 `src/stores/archive-workspace.ts` 增加 `confirmArchiveDocument` 与 `cancelArchiveDocumentConfirmation`。
- 成功响应同时替换处理列表中的同一文档和当前草稿中的 `document`，保留已加载的字段与证据。
- 异步返回时再次核对项目 ID 和文档 ID，避免切换项目后旧请求覆盖当前页面。
- 使用独立 loading key；失败时沿用现有稳定错误展示，不提前修改本地状态。

### 3.3 草稿面板与页面接线

- `PENDING_CONFIRMATION`：显示“确认并正式入档”；七字段未全部检查时禁用，并明确剩余数量。最终合法性仍以后端为准。
- `PENDING_RECONFIRMATION`：显示“重新确认并正式入档”。
- `CONFIRMED`：字段保持只读，显示确认时间和后端返回的索引上下文 Chunk 数；显示“取消正式入档”。
- 确认、重新确认和取消确认均携带当前响应版本。按钮只在请求成功后显示新状态和版本；不得伪造正式目录、清单满足或 RAG 成功。
- 页面只负责调用 Store 并滚动/保留当前草稿，不复制后端状态机。

## 4. TDD 执行顺序

Luna 按以下顺序执行，并记录真实 RED 与 GREEN：

1. **API RED**：先新增两个端点的方法、URL、Bearer 和精确请求体测试，确认因方法缺失失败；再做最小实现。
2. **Store RED**：先新增确认/取消后文档与草稿同步、错误不预写状态、切换项目后过期响应隔离测试，确认失败；再做最小实现。
3. **组件 RED**：先新增三种状态按钮、检查未完成禁用、版本事件、确认信息和取消入口测试，确认失败；再实现组件。
4. **页面 RED**：先验证组件事件调用 Store 的接线，确认失败；再完成 View 接线。
5. GREEN 后运行相关测试；完成后运行 `npm run typecheck`、`npm run test`、`npm run build`。

RED 必须由缺失行为产生，不能使用语法错误、人为抛错或错误断言制造。

## 5. 主 Agent 审查与真实验收

主 Agent 审查 URL/请求体、Bearer 边界、乐观锁版本、成功后状态同步、项目切换隔离、错误路径和范围是否越出 FR-036。相邻 Vue 目录不是 Git 仓库，因此继续以修改前后文件哈希、逐文件内容审查和自动化结果作为证据，不能声称前端 Git Diff 已审查。

自动化通过后，使用虚构资料在 `5173/api → 8000 → PostgreSQL/Embedding/Chroma` 真实链路验证：七字段完成检查、确认并生成 Final Chunk、取消确认并清理 Final Chunk、重新确认恢复正式状态。验收只证明 FR-036，不代替 FR-037～FR-041。
