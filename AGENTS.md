# 项目协作说明

开始处理本项目之前，阅读本文件后，再依次阅读：`LEARNING_PLAN.md`、`docs/stage/handoff.md`、`docs/decisions.md`。实际代码与 Git 状态优先于状态文档；交接文档提供当前事实，决策台账提供稳定决策。

# Mini RAG 项目协作规则

## 项目定位与长期边界

本项目是个人学习为主、可供朋友小范围使用的企业知识库与只读 Agent 后端，使用 FastAPI、SQLModel、PostgreSQL、LangChain、LangGraph、本地 BGE、Chroma 和 DeepSeek。Swagger 用于 API 契约诊断与验收；相邻 Vue 工作台仅用于本地联调，不以公开多用户网站或公开前端产品为部署目标。当前运行与开发基线为本地环境；是否部署到云端、部署拓扑和资源规格须在 V1 功能完成后另行评估确认。

当前业务方向是“智慧档案与企业文档智能”及既有只读企业制度 Agent。已删除的员工请假领域及其写工具、人工确认/决定接口不恢复；不推进标书投标、标书解析生成、投标合规审查、OCR、表格专用解析、业务运行时多 Agent 编排、BM25/混合检索、Redis 任务队列或生产级分布式部署。正式向量能力使用 Chroma；不得把历史 Milvus、云主机实验或本机健康检查写成完整云端部署结论。未来云端网络与资源方案待部署决策后确定。

## 当前状态与决策入口

实时进度、当前授权、阻塞项、验证证据和下一步读取 `docs/stage/handoff.md`；稳定的产品、架构、数据、安全、质量和范围决策读取 `docs/decisions.md`；详细阶段方案以 handoff 指向的仓库文件为准。AGENTS.md 不维护阶段编号、实验流水或易变化的测试数字。AV1-P14 正式检索质量改动必须遵循 handoff 指向的最新已确认方案、TDD 要求和真实固定集验收门槛。

## 状态与决策文档维护协议

- 任务开始读取 `AGENTS.md`、`LEARNING_PLAN.md`、`docs/stage/handoff.md`、`docs/decisions.md`。
- 用户确认重要的产品、架构、数据、安全、质量或范围决策后，追加到 `docs/decisions.md`；不得把助手推测写成已确认。
- 阶段完成、发生阻塞、完成真实验证或准备切换对话时，更新 `docs/stage/handoff.md`。
- 只有长期规则变化才修改 AGENTS.md；普通实现细节和实验流水不进入 AGENTS.md。
- 更新前核对实际代码、Git 状态和测试结果；冲突以实际代码为准，并在交接或决策文档记录差异。
- decisions.md 原则上只追加；决策改变时将旧项标为“已废弃”，并链接替代决策 ID。
- handoff.md 是可重写的当前快照，不承担长期决策台账职责。
- 三份文档均不得记录 `.env`、密钥、凭据、Token、数据库内容或业务数据。
- 普通代码任务无需机械更新三份文档，只有上述触发事件发生时才更新。

## 企业知识库 Agent 当前范围

目标是完成可通过 Vue 工作台与 Swagger 演示、可写入简历的单 Agent 业务闭环，而不是追求 Tool 或 Agent 数量。

当前唯一工具：

```text
search_company_policy      查询当前会话绑定知识库中的公司制度
```

目标链路：

```text
用户消息 → 校验 Agent 会话授权范围
→ DeepSeek 判断是否调用制度检索工具
→ 工具使用服务端注入的 user_id + kb_id 检索 Chroma
→ DeepSeek 仅依据工具结果回答
→ 返回引用并把脱敏调用记录写入 PostgreSQL
```

既有制度检索 Agent 的实现与验证以 `docs/implementation/既有检索与智能体实施计划.md` 和实际代码为准；智慧档案 V1 的实现与验证以 `docs/implementation/智慧档案V1实施计划.md`、handoff 和实际代码为准。历史测试或单问题真实模型结果不得冒充当前版本或完整质量结论。

详细需求、数据与接口分别以 `docs/design/需求说明.md`、`docs/design/数据库设计.md` 和 `docs/design/接口设计.md` 为准；正式企业知识库 Agent 位于 `app/agents/admin/`。

## 文档驱动顺序

```text
需求澄清 → requirements → architecture → database/api design
→ implementation plan → 单个可验证任务 → 测试 → acceptance report/README
```

- 需求、文档和代码冲突时，不自行猜测；列出冲突并向用户确认。
- 无法从代码或已确认需求证明的内容标记为 `[TODO]`。
- 需要用户决定的产品意图标记为 `[ASK USER]`。
- 功能需求使用稳定编号 `FR-xxx`，实现计划和验收报告引用该编号。

## 当前分层

```text
Router → Application Service → Agent / Domain Service
→ SQLModel、文件系统、Chroma、DeepSeek
```

- `app/routers/`：HTTP 输入输出、状态码和依赖注入。
- `app/dependencies/`：Session、当前用户和资源所有权。
- `app/services/`：应用流程、领域规则、事务、解析、切分、Embedding、检索和外部客户端。
- `app/agents/`：LangGraph 状态、Prompt、只读工具、编排和调用观测；不得直接保存数据库 Session。
- `app/models/`：PostgreSQL 业务模型；`app/schemas/`：HTTP 契约。
- `app/core/`：配置、日志和异常。

当前没有 Repository 层。未经架构确认，不要只为模仿 Spring Boot 增加空转层。

## 数据与安全规则

- PostgreSQL 保存业务实体和状态；Chroma 保存 Chunk、引用元数据和向量；文件系统保存原文件。
- LangGraph Checkpoint 使用独立 SQLite 文件，只保存可序列化的执行状态和消息，不代替业务表。
- `document_id` 必须贯穿 PostgreSQL、文件路径和 Chroma。
- 受保护接口必须先验证 Bearer Access Token、真实存在的用户和资源归属；不得保留 `X-User-ID` 身份后门。
- Chroma 检索必须包含 `user_id + kb_id`；文档删除必须再包含 `document_id`。本地 Compose 内 API 使用内部网络访问 Chroma；宿主机调试仅可使用回环地址 `127.0.0.1:8001`，不得暴露到局域网或公网。业务客户端只能访问 FastAPI。
- 不接受客户端覆盖资源的 `owner_id` 或 `user_id`。
- Agent 工具的 `user_id` 和 `kb_id` 必须由已验证上下文注入，不能由模型生成。
- Agent Graph State 只能保存可序列化数据，不得保存数据库 Session、Engine、模型客户端或向量库客户端。
- Tool 保持薄层；检索和外部访问规则由 Service 负责。
- 只对超时和连接失败重试；参数、权限和拒答不重试。
- 工具日志只保存脱敏摘要，不保存完整制度正文、Token、密码或模型隐藏推理。
- 未知异常只在服务端日志记录细节，不把 `str(exc)` 返回客户端。
- 登录功能实施后只能保存密码哈希，不得保存、记录或返回明文密码。
- JWT 实施后，受保护接口必须从已验证 Token 获取用户身份，不再信任客户端直接提交的 `X-User-ID`。
- Access Token 和 Refresh Token 必须使用不同用途声明和有效期，刷新接口不得接受 Access Token 代替 Refresh Token。
- PostgreSQL 只保存 Refresh Token 的单向哈希和会话状态，不得保存 Token 明文；退出登录必须撤销对应会话。
- 为保持学习项目简单，Refresh Token 在有效且未撤销期间可以重复使用；刷新时只签发新的 Access Token，不轮换 Refresh Token。
- 不读取、打印或提交 `.env`、密钥、数据库、上传文件和运行日志。

## 编码与注释

- 遵循 `pyguide_zh-CN.md` 中适用于本项目的规则。
- 模块、公开类和公开函数写职责明确的文档字符串。
- 函数参数必须添加注释。
- 关键代码段注释“为什么”，不做逐行翻译式注释。
- 所有新增或修改的代码注释与文档字符串必须使用中文；代码标识符、协议名、错误码和必要的技术专有名词除外。
- API Schema、数据库 Model、内部 dataclass 分开定义。
- 已知业务错误使用 `AppError`；数据库提交失败后先 `rollback()`。
- 不修改无关文件，不覆盖用户已有未提交改动。
- 混合工作区只显式暂存获准文件，禁止使用 `git add -A`；提交前必须重新核对工作区范围。
- 测试目录必须镜像被测代码的层目录：`app/agents/`、`app/core/`、`app/dependencies/`、`app/models/`、`app/routers/`、`app/schemas/`、`app/services/` 分别对应 `tests/` 下的同名目录；跨层 HTTP 集成测试归入 `tests/routers/`，测试辅助代码归入 `tests/support/`。移动文件时同步更新相对路径和文档中的测试路径。
- 后续开发编码采用 TDD：每个可观察行为先新增或修改一个会失败的测试并实际运行（RED），随后只写使该测试通过的最小实现（GREEN），最后仅在相关测试持续通过时重构（REFACTOR）。完成一个学习步骤前再运行相关测试、全量测试和 `compileall`；不得把“预期会失败”当作已验证的 RED 证据。
- TDD 的单元、服务和 API 测试验证确定性行为。涉及 DeepSeek 等真实 LLM 输出的事实性、引用完整性或语义质量时，必须另行使用真实模型和固定评估资料完成评估；Mock 只能用于隔离普通单元测试，不能作为 LLM 质量通过的证据。

## 命令

当前验证环境为 Python 3.11：

```powershell
python -m pip install -r requirements-dev.txt
python -m alembic upgrade head
python run.py
python -m pytest -q
python -m compileall -q app tests
```

本机也可使用 `C:\D\venvs\mrh\Scripts\python.exe`。项目目前没有 Ruff、Black、Mypy 或覆盖率阈值配置；未实际运行时不得声称这些检查通过。`python run.py` 启动时也会执行 Alembic upgrade。基线迁移只检查旧表结构，不读取或打印业务数据；发现字段不兼容时必须停止，不得直接 `stamp` 掩盖冲突。

## 完成标准

- 改动对应明确需求编号和可验证结果。
- 新行为覆盖正常、边界、异常和越权测试。
- 运行相关测试、完整测试和 `compileall`。
- API、SQLite、Chroma、README 与 `docs/` 保持一致。
- 明确列出未实现、部分实现和当前环境无法验证的内容。
- 提交前排除 `.env`、`data/`、`logs/`、IDE 临时文件和真实密钥。

# 多 Agent 开发工作流

## 主 Agent

GPT-5.6 Sol 作为主 Agent。

主 Agent 负责：需求分析、文档审查、架构决策、实现方案规划、任务拆分和最终实现审查。在进入代码实现阶段之前，主 Agent 必须先产出清晰、明确、可执行的实现计划（Implementation Plan）。

## 实现 Agent

当实现任务已经足够明确、边界清晰且适合独立执行时，应优先将任务委派给 GPT-5.6 Luna 子 Agent。Luna 负责代码实现、测试编写、常规 Bug 修复、机械性重构、Lint/格式化处理和重复性批量修改，并严格按照既定实现计划执行。

Luna 不得自行修改整体架构、既定需求、任务范围、对外公开 API 或核心数据库结构。如发现必须进行上述变更，应停止扩大修改，并交回 GPT-5.6 Sol 主 Agent 重新分析、决策和更新实现计划。

## 实现审查

代码实现完成后，由 GPT-5.6 Sol 主 Agent 审查 Git Diff、实现计划完成度、需求符合性、测试完整性、API 与数据模型一致性、范围扩张、遗漏、偏离和不必要修改。发现问题时，主 Agent 生成范围清晰的修正任务，再委派 GPT-5.6 Luna 执行。
