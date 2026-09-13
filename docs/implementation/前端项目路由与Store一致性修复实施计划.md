# 前端项目路由与 Store 一致性修复实施计划

## 1. 问题与目标

相邻 Vue 工作台同时保存路由中的 `projectId`、Pinia 的 `activeProjectId` 和 localStorage
中的最近项目。当前会话恢复后会调用 `syncRouteProject()`，但页面内退出后重新登录或注册不会再次
同步路由；新账号加载首个项目时，地址栏可能继续显示旧账号的项目 ID，而实际请求使用新 Store 项目。

本切片确保用户可见 URL 与当前认证会话的项目范围一致，不修改后端 API、认证契约、项目所有权规则、
Store 数据模型或任何归档业务状态。

## 2. 已确认行为

1. 登录或注册成功后，Store 已完成当前账号项目列表加载，页面必须立即校验路由项目。
2. 路由项目属于当前账号时，保留当前深链接并选择该项目。
3. 路由项目不存在于当前账号项目列表时，使用 `router.replace('/')` 清除旧项目路径；不得发起旧项目请求。
4. 退出登录完成后使用 `router.replace('/')`，清除地址栏中的项目 ID，且仍由 Store 负责清理令牌和项目状态。
5. 首页没有 `projectId`，可以保留 Store 自动选择的当前项目，用于项目总览和后续导航。

## 3. 文件范围

- `mini-rag-milvus-vue/src/views/ArchiveWorkspaceView.test.ts`
- `mini-rag-milvus-vue/src/views/ArchiveWorkspaceView.vue`

若测试证明必须修改 Store，Luna 停止扩大范围并交回主 Agent；不得顺带修正页面文案、样式或其他已发现问题。

## 4. TDD

### RED

先新增并运行 View 测试，至少覆盖：

- 位于旧项目文档路由时，登录完成并加载另一账号项目后，URL 被 `replace('/')`；
- 注册自动登录完成后的同一行为；
- 当前路由项目仍属于登录账号时，保留深链接并选择路由项目；
- 从项目路由退出登录后，Store 清理完成且 URL 被 `replace('/')`。

新增测试必须在生产实现前实际失败，并记录失败原因。

### GREEN

只增加使上述测试通过的最小页面编排：

- 成功登录/注册后调用既有 `syncRouteProject()`；
- 用页面级退出函数包装 `store.signOut()`，完成后替换到首页；
- 模板退出按钮调用页面级函数。

不增加全局路由守卫，不监听尚未加载完成的空项目列表，不改变 `selectProject()`、localStorage 或
Store 的请求隔离逻辑。

## 5. 验证

1. 运行 `ArchiveWorkspaceView.test.ts`，保存 RED 与 GREEN 结果。
2. 运行前端完整测试、`npm run typecheck` 和 `npm run build`。
3. 主 Agent 审查差异，确认只有计划内两个前端文件和本计划/状态文档发生变化。
4. 在已有 5173/8000 环境可用时，用两个临时账号验证旧项目 URL 在重新登录后被清除；若浏览器控制仍不可用，
   必须明确自动化证据与真实 DOM 证据的边界，不得伪造点击验收。

## 6. 停止条件

若实现需要修改公开路由结构、后端授权、登录响应、核心 Store 状态或引入全局导航守卫，Luna 停止并交回
主 Agent 重新评审。

## 7. 完成结果（2026-09-12）

- Luna 先新增登录、注册、有效深链接和退出登录四项测试；RED 为新增 `4 failed`、既有
  `10 passed`，失败原因分别对应缺失的路由清理或有效项目同步。
- GREEN 只修改 View：登录/注册成功后调用既有 `syncRouteProject()`；退出按钮通过页面函数
  等待 `store.signOut()` 后执行 `router.replace('/')`。Store、Router、API 和后端均未修改。
- 主 Agent 复核目标测试 `14 passed`，前端完整回归 `122 passed`，`typecheck` 与
  `build` 通过，构建转换 68 个模块。
- Codex browser provider 仍返回 `nodeRepl.fetch request failed`，因此没有新增真实 DOM 点击证据。
