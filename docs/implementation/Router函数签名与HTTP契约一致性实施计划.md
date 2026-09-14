# Router 函数签名与 HTTP 契约一致性实施计划

## 1. 问题与结论

FastAPI 路由同时存在两个不同层次的类型：

- 函数返回注解描述 Python 函数实际返回的对象类型。
- 路由装饰器的 `response_model` 描述对外 HTTP 响应的校验、过滤和 OpenAPI Schema。

当前审计确认四个端点的函数注解与实际 Service 返回类型不一致：

| 端点 | 当前函数注解 | 实际返回类型 | 对外 `response_model` |
|---|---|---|---|
| 注册用户 | `UserRead` | `User` | `UserRead` |
| 创建项目 | `ProjectRead` | `Project` | `ProjectRead` |
| 读取项目 | `ProjectRead` | `Project` | `ProjectRead` |
| 更新项目 | `ProjectRead` | `Project` | `ProjectRead` |

其他返回 SQLModel 的知识库、文档、聊天会话和 Agent 会话端点已经按上述边界标注；返回由
Service 构造的响应 Schema 或显式 `Response` 的端点也与实际类型一致。

第一轮路径参数审计确认，`projects.py` 有 27 个端点通过 `ProjectContextDep` 或
`ProjectDocumentDep` 间接读取 `project_id`、`document_id`，但端点函数签名没有显式声明路径模板中的
同名参数，共缺少 43 个参数声明。测试扩展到全部 Router 后，还发现 `documents.py` 的 4 个端点
通过 `OwnedKnowledgeBaseDep` 间接读取路由前缀中的 `kb_id`。完整范围为 31 个端点、47 个缺失声明。
FastAPI 运行时会从依赖树取得这些值，因此现有 HTTP 测试可以通过；
函数签名和路径模板仍不完整，IDE 的 FastAPI 检查会逐项报告警告。

补齐签名后，PyCharm 的 `PyUnusedLocal` 检查仍会把 `del project_id` 等写法判定为“参数值未使用”。
全 Router AST 审计确认 36 个端点共有 53 个路径参数只被删除而未被读取，其中还包括原有 Agent
和 Chat 路由的 `session_id`。此外，`list_project_documents_endpoint` 的 Python 参数 `status`
遮蔽了模块导入的 FastAPI `status`。

## 2. 实现边界

1. 只把上述四个函数的返回注解改为 `User` 或 `Project`，并补充对应导入。
2. 保持所有 `response_model` 不变，因此不改变 HTTP JSON、OpenAPI、状态码或字段过滤。
3. `projects.py` 和 `documents.py` 的每个路径模板参数都必须在对应端点签名中以同名 `UUID` 参数
   显式出现；仍由原有依赖完成知识库、项目所有权和文档范围校验。端点不重复查询，也不把显式路径
   参数作为新的可信授权来源。
4. 对只供依赖消费的路径参数赋给 Python 惯例弃值变量 `_`，表明业务逻辑继续使用已验证的
   依赖对象；路径值仍不得作为新的授权来源。
5. 不修改 Service 返回值，不在 Router 重复执行 `model_validate()`，不改变数据库 Model、Schema、
   依赖安全规则或公开路径。
6. 不引入 Mypy、Pyright 或新的依赖；项目当前没有静态类型检查工具配置。
7. Python 内部查询参数改名为 `document_status`，通过 `Query(alias="status")` 保持外部查询参数、
   OpenAPI 名称和现有客户端请求不变。

## 3. TDD 顺序

1. **RED**：在 `tests/routers/` 新增返回类型边界测试，断言四个端点的函数返回注解分别是
   `User` 和 `Project`，同时断言 FastAPI 路由仍使用 `UserRead` 和 `ProjectRead` 作为
   `response_model`；实际运行并确认旧注解导致失败。
2. **GREEN**：只修改 `app/routers/auth.py` 和 `app/routers/projects.py` 的导入及四个返回注解。
3. **第二轮 RED**：扩展同一测试文件，遍历全部 `APIRoute`，断言路径模板中的每个参数都存在于
   端点函数签名；实际运行并确认当前 27 个端点、43 个参数声明缺失。
4. **第二轮 GREEN**：先为 `projects.py` 端点补齐缺失的 `project_id`、`document_id`；全 Router
   测试由此继续发现 `documents.py` 的 4 个 `kb_id` 缺失，再补齐这些参数。所有新增参数都做明确的
   未使用处理，不改变依赖调用或业务参数来源。
5. **REFACTOR**：审计全部 Router，确认不存在其他返回类型或路径参数声明不一致项。
6. **第三轮 RED**：扩展契约测试，断言每个路径参数在端点函数体中以读取方式显式消费，并断言
   文档状态筛选的 Python 参数名为 `document_status`、HTTP 别名仍为 `status`。旧 `del` 写法和同名
   `status` 应产生确定性失败。
7. **第三轮 GREEN**：在 Agent、Chat、旧知识库文档和项目路由中统一使用 `_ = path_parameter`
   显式弃值；把项目文档列表的内部变量改为 `document_status` 并保留 HTTP 别名。
8. 主 Agent 审查 Git Diff，运行新增测试、全部 Router 测试、完整测试、
   `compileall app tests scripts evals` 和 `git diff --check`。

## 4. 完成标准

- 四个函数注解与实际 Service 返回类型一致。
- 所有路由路径模板参数均在端点签名中显式声明，IDE 不再产生截图所示警告。
- 仅供依赖校验的路径参数通过 `_` 明确弃值，不再触发“参数值未使用”；Router 参数不遮蔽模块导入名。
- 外部响应 Schema 和既有 API 行为不变。
- 全部 Router 完成同一规则的静态审计。
- TDD RED、GREEN 和最终回归均有实际运行证据。
