# Mini RAG：智慧档案与企业文档智能

这是一个以学习和本地演示为目标的智慧档案项目。后端使用 FastAPI、SQLModel、PostgreSQL、LangChain/LangGraph、本地 BGE、Chroma 和 DeepSeek；[Vue 工作台](https://github.com/yabuki-jou/mini-rag-vue) 位于相邻仓库。Swagger 用于接口契约诊断，Vue 用于展示完整业务流程。本项目尚未作为公开多用户服务部署。

## 能做什么

- 智慧档案 V1：按项目管理文档，解析 TXT、Markdown、PDF、DOCX，抽取并人工确认档案字段，建立正式索引，查看目录、清单和审计记录。
- FR-039 档案问答：在当前项目的已索引档案中检索证据，返回引用；证据不足时拒答。
- FR-042 项目档案助手：通过只读工具查询项目档案、清单与证据，支持跨轮对话、历史消息及脱敏工具记录。页面刷新后自动恢复当前项目最近一次会话。

典型演示路径：注册并登录 → 创建项目 → 上传和确认文档 → 建立索引 → 查看档案目录与清单 → 提问并检查引用或拒答 → 使用档案助手继续追问 → 刷新页面验证最近会话恢复。

## 架构与数据边界

请求依次经过 Router、应用 Service、领域 Service 或 Agent；身份和资源归属在服务端校验。档案检索限定在已验证用户与当前项目对应的知识库范围内，Agent 工具不接受模型自行指定用户或知识库。

PostgreSQL 保存项目、文档、会话基本信息与脱敏工具记录；文件系统保存原文件；Chroma 保存正式索引的向量及引用元数据；独立 SQLite Checkpoint 保存档案助手的消息与执行状态。这些存储各有职责，不能将 SQLite 当作业务数据库。

## 本地运行

需要 Python 3.11、Docker Compose，以及可用的本地 Embedding/Reranker 模型。复制配置模板后，在本机私下配置数据库连接、模型路径、JWT 密钥和 DeepSeek Key；不要提交 `.env` 或真实业务数据。

```powershell
Copy-Item .env.example .env
python -m pip install -r requirements-dev.txt
docker compose up -d postgres chroma
python scripts/provision_chroma_namespace.py
python -m alembic upgrade head
python run.py
```

启动后访问 [Swagger](http://127.0.0.1:8000/docs)。相邻的 Vue 仓库安装依赖并运行 `npm run dev`；前端默认通过 Vite 代理访问本地后端。首次迁移前请确认目标 PostgreSQL 数据库，启动 `run.py` 时也会执行 Alembic 升级。

## 验证

```powershell
python -m pytest -q
python -m compileall -q app tests
```

确定性接口与服务行为由自动化测试覆盖；真实模型的事实性和引用质量须另用固定资料评估，Mock 测试不能替代真实模型验收。当前验收范围、环境和证据见[验收报告](docs/review/验收报告.md)。

## 当前限制

- 刷新只恢复当前项目最近一次助手会话；不提供会话列表、手动切换、重命名或删除。
- 历史消息可以重新读取正文，但旧回答的引用卡片不恢复；未完成的异常轮次不显示。
- PostgreSQL、Checkpoint SQLite、文件和 Chroma 不是同一个原子事务；本地验证不等于生产级一致性或云部署验收。
- 不包含 OCR、通用知识库/普通 Chat 公开 API、历史制度 Agent 或业务运行时多 Agent 编排。

## 文档入口

- [文档导航](docs/README.md)：需求、架构、数据库和接口设计。
- [发布说明](docs/releases/智慧档案单主线发布说明.md)：本地演示版的能力边界与兼容说明。
- [验收报告](docs/review/验收报告.md)：测试与人工验证证据。
- [阶段交接](docs/stage/handoff.md)与[决策台账](docs/decisions.md)：后续开发状态和已确认决策。
