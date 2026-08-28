# 项目协作说明

开始处理本项目之前，请先阅读根目录的 `LEARNING_PLAN.md`，并以其中记录的学习路线和当前进度为准。

# Mini RAG 项目协作规则

## 项目定位

本项目是个人学习为主、可供朋友小范围使用的企业知识库与只读 Agent 后端，使用 FastAPI、SQLModel、PostgreSQL、LangChain、LangGraph、本地 BGE、Chroma 和 DeepSeek。Swagger 用于 API 契约诊断与验收；AV1-P14 将相邻的 `mini-rag-milvus-vue` 作为本地 Vue 联调工作台，但不以公开多用户网站为部署目标。当前运行与开发基线均为本地环境；是否部署到云端、部署拓扑和资源规格均为暂定事项，必须在 V1 功能开发完成后另行评估和确认。Chroma 代码和 Compose 迁移已完成本地验证；不得把历史云主机实验或本地健康检查写成已完成云端部署。

现有能力包括 RAG 后端、PostgreSQL/Alembic 业务库、制度检索工具，以及带 SQLite Checkpointer 的单 Agent Graph。独立 Agent API 支持会话、消息、历史和脱敏工具日志查询。员工请假领域、写工具、人工确认与决定接口已经删除。当前唯一后续业务方向是“智慧档案与企业文档智能”：需求、架构、数据库、API 与实施计划基线已确认；AV1-P01～P08、P09 确认/取消确认与 Final Collection 基础、P10 清单关联/目录/审计、P11 正式检索服务、P12 证据问答服务、P13 物理删除服务，以及 AV1-A01 账号密码认证、JWT 会话与 Bearer 身份切换的隔离验证均已完成对应实现切片。P09 已完成真实确认—INDEX 路由纵向链路、真实 Chroma/BGE canary 和取消确认清理验证；P11 已完成一次真实单文档检索 canary（512 维、cosine、命中 1 条、10 次请求 P95 约 125.97 ms）；P10～P13 的确定性服务/API 测试已通过。固定问题集的检索阈值/召回质量、DeepSeek 问答质量、P13 真实跨存储故障恢复，以及 Vue 工作台的完整端到端验收仍待 P14。测试数据与临时文件已清理。`0005_archive_v1_schema`～`0008_legacy_business_comments` 已在目标 PostgreSQL 空库实际前向迁移；`0009_chroma_vector_comments` 与 revision `0010_account_auth` 也已在当前 PostgreSQL 开发库实际前向迁移并核对结构。当前下一步为 P14 全链路验收、文档收口和最终回归。当前不推进标书投标、标书解析生成或投标合规审查方向。当前不包含 OCR、表格专用解析、混合检索、Rerank、多 Agent、Redis 任务队列和生产级分布式部署；P14 的相邻 Vue 工作台例外仅限本地联调，不扩大为公开前端产品。

全量 Chroma 迁移（旧制度检索和后续智慧档案）已确认；AV1-C01 已完成一次云主机上的 Chroma 独立内网、过滤、精确删除、容器重启持久化与空闲资源实验，AV1-C02 已完成运行时代码、离线单测、本机命名空间和本机 Docker 健康验证。云端完整栈资源验证不再阻塞当前开发，随部署决策一并暂缓至 V1 功能完成后。后续正式索引为 Chroma Final Collection；不得将历史 Milvus 验证、C01 空闲内存或本机健康检查写成完整部署通过。

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

既有制度检索 Agent 的状态以 `docs/implementation-plan.md` 和本次实际验证为准；智慧档案 V1 的状态以 `docs/archive-v1-implementation-plan.md` 和本次实际验证为准。历史 `89 passed` 结果属于已删除请假领域的旧版本，不得作为当前版本测试结论。真实 BGE + Milvus + DeepSeek 单问题结果仍只证明当时的有限链路，不代表当前 PostgreSQL 部署或多文档质量已经复验。

验证边界：真实 BGE + Milvus + DeepSeek 的历史结果只证明当时的单文档、单问题链路；它不能证明 Chroma 或 2 vCPU / 2 GB 云端部署可用。若后续决定云端部署，必须重新完成并记录 Chroma 的内部网络、持久化、隔离、删除和资源可行性验证；本地 BGE 的持久化路径也需在实际配置修改后复验。

详细需求、数据与接口分别以 `docs/requirements.md`、`database-design.md` 和 `api-design.md` 为准；实时进度以 `LEARNING_PLAN.md` 为准。正式企业知识库 Agent 位于 `app/agents/admin/`。

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
- Chroma 检索必须包含 `user_id + kb_id`；文档删除必须再包含 `document_id`。当前本地开发中，Compose 内 API 使用内部网络访问 Chroma；宿主机调试仅可使用回环地址 `127.0.0.1:8001`，不得暴露到局域网或公网。业务客户端只能访问 FastAPI；未来云端网络拓扑待部署决策后确定。
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
- 关键代码段注释“为什么”，不做逐行翻译式注释。
- API Schema、数据库 Model、内部 dataclass 分开定义。
- 已知业务错误使用 `AppError`；数据库提交失败后先 `rollback()`。
- 不修改无关文件，不覆盖用户已有未提交改动。
- 测试目录必须镜像被测代码的层目录：`app/agents/`、`app/core/`、`app/dependencies/`、`app/models/`、`app/routers/`、`app/schemas/`、`app/services/` 分别对应 `tests/` 下的同名目录；跨层 HTTP 集成测试归入 `tests/routers/`，测试辅助代码归入 `tests/support/`。移动文件时必须同步更新相对路径和文档中的测试路径。
- 后续开发编码采用 TDD：每个可观察行为先新增或修改一个会失败的测试并实际运行（RED），随后只写使该测试通过的最小实现（GREEN），最后仅在相关测试持续通过时重构（REFACTOR）。完成一个学习步骤前再运行相关测试、全量测试和 `compileall`；不得把“预期会失败”当作已验证的 RED 证据。
- TDD 的单元、服务和 API 测试验证确定性行为（契约、权限、事务、状态和错误）。涉及 DeepSeek 等真实 LLM 输出的事实性、引用完整性或语义质量时，必须另行使用真实模型和固定评估资料完成评估；Mock 只能用于隔离普通单元测试，不能作为 LLM 质量通过的证据。

## 命令

当前验证环境为 Python 3.11：

```powershell
python -m pip install -r requirements-dev.txt
python -m alembic upgrade head
python run.py
python -m pytest -q
python -m compileall -q app tests
```

本机也可使用 `C:\D\venvs\mrh\Scripts\python.exe`。项目目前没有 Ruff、Black、Mypy 或覆盖率阈值配置；未实际运行时不得声称这些检查通过。

`python run.py` 启动时也会执行 Alembic upgrade。基线迁移只检查旧表结构，不读取或打印业务数据；发现字段不兼容时必须停止，不得直接 `stamp` 掩盖冲突。

## 完成标准

- 改动对应明确需求编号和可验证结果。
- 新行为覆盖正常、边界、异常和越权测试。
- 运行相关测试、完整测试和 `compileall`。
- API、SQLite、Chroma、README 与 `docs/` 保持一致。
- 明确列出未实现、部分实现和当前环境无法验证的内容。
- 提交前排除 `.env`、`data/`、`logs/`、IDE 临时文件和真实密钥。
