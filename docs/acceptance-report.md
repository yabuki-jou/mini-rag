# 验收报告

> **范围提示（2026-08-06）：** 本报告记录当前企业知识库与制度检索 Agent 基座，以及
> 智慧档案 V1 已完成的 AV1-P01～P03 基础、P04 前置模型分层及 P04.1 FR-030。它不是智慧档案完整
> 业务闭环的验收报告；清单项 API、正式归档、Final Collection、检索问答和删除恢复
> 均尚未实现或验收。
>
> **向量存储更新（2026-08-07）：** 后续目标已确认从 Milvus 迁移至 Chroma，但本报告中的
> 已有运行时和历史验证仍是 Milvus 证据。AV1-C01 已独立验证 Chroma 内网、过滤、精确删除、
> 容器重启持久化和空闲资源，详见 `docs/chroma-migration-decision.md`；本地 Chroma 代码迁移、
> 隔离/删除/距离语义单测和编译已通过，完整应用栈 2 vCPU / 2 GB 资源、向量重建与真实服务
> 迁移回归仍未验收。
>
> **认证更新（2026-08-10）：** AV1-A01 的注册、登录、刷新、注销、旧身份头拒绝与既有
> 受保护路由回归已在隔离 SQLite 中通过；`0010_account_password_authentication` 的真实
> PostgreSQL 开发库迁移尚未执行，因此不构成真实库迁移验收。

## 验收范围

本轮覆盖以下已完成事项：

- 员工请假领域已删除，业务库迁移至 PostgreSQL，制度检索 Agent 保持只读。
- AV1-P01 冻结 PDF/DOCX/TXT/MD Parser、有效文本与定位规则。
- AV1-P02 生成本地虚构验收资料、人工字段/证据 Ground Truth 和固定问答用例。
- AV1-P03 创建归档迁移、SQLModel、`ProjectContext` 所有权基础及 PostgreSQL 表/字段注释。
- P04 前置模型分层：项目、归档、清单、操作/审计模型拆为四个模块，公共导入入口不变。
- P04.1 实现项目 CRUD、内部知识库范围、五项虚构模板复制、乐观锁和受限删除。
- AV1-A01 实现 Argon2 密码哈希、JWT 会话、Bearer 身份切换与本地旧用户凭据初始化脚本。

旧 SQLite 业务数据不迁移；LangGraph Checkpoint SQLite 保留且不替代 PostgreSQL 业务表。

## 需求追踪

| 验收项 | 实现证据 | 验证结果 |
|---|---|---|
| AC-001 PostgreSQL 前向迁移 | Alembic `0001`～`0008` | 目标 PostgreSQL 空库已实际前向迁移至 `0008_legacy_business_comments`；专用 `POSTGRES_TEST_URL` 自动化测试仍未配置 |
| AC-002 无请假表/Enum | `0004_remove_leave_domain.py` | 新空库完整迁移链不保留请假领域；历史迁移仅用于前向兼容 |
| AC-003 Agent 只注册制度工具 | `app/agents/admin/graph.py` | 当前 Graph/Router 测试覆盖只读制度检索范围 |
| AC-004 Parser 规则冻结 | `docs/archive-v1-parser-design.md`、Parser 测试 | 四格式定位、有效文本阈值和扫描件失败规则已冻结；尚未接入 V1 状态机 |
| AC-005 V1 资料与 Ground Truth | `scripts/generate_archive_v1_eval_data.py`、相关测试 | 本地虚构资料和标注可重建；不代表 AI、检索或问答质量已达标 |
| AC-006 V1 数据/授权基础 | `0005`～`0008`、模型、`ProjectContext`、注释契约测试 | 16 张业务表、143 个字段注释可由 PostgreSQL 元数据读取；项目/清单 API 尚不存在 |
| AC-007 模型分层兼容性 | `project.py`、`archive.py`、`checklist.py`、`archive_audit.py` | 公共 `app.models` 导入、元数据注册、迁移/授权/注释测试均通过；无数据库结构变更 |
| AC-008 项目 CRUD | `projects.py`、`project_service.py`、`schemas/project.py` | 创建、隔离、重名、版本冲突、删除限制与模板复制 API 测试通过 |
| AC-009 账号密码认证 | `auth.py`、`auth_service.py`、`security.py`、`0010` | 隔离注册、登录、刷新、注销、会话撤销和旧身份头拒绝测试通过；真实 PostgreSQL 迁移待验证 |

## 自动化检查

| 检查 | 命令摘要 | 结果 |
|---|---|---|
| 全量测试 | `C:\D\venvs\mrh\Scripts\python.exe -m pytest -q` | `111 passed, 1 skipped, 16 warnings` |
| P03 相关测试 | 迁移、`ProjectContext`、字段注释契约 | `8 passed` |
| Python 编译 | `python -m compileall -q app tests migrations scripts` | 通过 |
| PostgreSQL 空库迁移 | 实际执行 Alembic 前向迁移 | 已到 `0008_legacy_business_comments` |
| PostgreSQL 专用自动化迁移测试 | `tests/test_postgres_migrations.py` | 未配置 `POSTGRES_TEST_URL`，因此全量测试中跳过 |
| Docker Compose 启动与健康检查 | Compose 服务运行 | 不属于本轮验证，未以本报告宣称通过 |
| Lint/类型/覆盖率 | 项目未配置 | 未执行 |

16 条警告为既有 FastAPI TestClient 与 Pixie 依赖弃用警告，不是测试失败。

## 已实现

- 业务 Engine 使用 `postgresql+psycopg://`；SQLite 仅用于隔离测试和 LangGraph Checkpoint。
- Alembic 迁移已包含旧请假领域的前向删除，以及智慧档案 V1 的 `0005`～`0008` 基础结构与注释。
- Agent 仅保留制度检索 Tool、会话、历史、引用、Checkpoint 和脱敏审计。
- 智慧档案已具备 Parser 冻结、虚构验收集、项目所有权上下文、数据库模型和模型模块分层。
- 已提供 `POST/GET/PATCH/DELETE /projects`；模板为虚构演示规则，删除空项目保留内部知识库。

## 未实现或未验证

- AV1-P04.2 的清单项 Router、Schema、Service、版本联动、派生状态和 API 测试。
- 上传、正式解析、手工草稿、AI 建议、人工确认、Final Collection、正式检索、问答、物理删除与跨存储恢复。
- BGE/Chroma Final Collection 行为、相关性阈值标定，以及 DeepSeek 的 AI 建议/问答质量验收。
- `POSTGRES_TEST_URL` 的可重复自动化空库迁移测试、`0010` 在真实 PostgreSQL 开发库的迁移，以及本轮 Compose 启动和健康检查。

## 风险

1. 中：认证已改为 Argon2 密码哈希、JWT 与可撤销会话，但尚未实现多设备会话管理、Refresh Token 轮换和生产级密钥轮换。
2. 中：Checkpoint SQLite 不支持多实例部署。
3. 中：PostgreSQL、Chroma 和文件系统没有分布式事务；恢复策略将在 AV1-P13 实现和验证。
4. 中：智慧档案当前仅完成数据/授权基础，尚未形成可演示的项目归档业务闭环。
5. 中：外部模型仅可处理虚构或脱敏资料；不得把真实商业秘密或敏感信息发送给 DeepSeek。

## 结论

现有 Agent 基座与智慧档案 V1 的 P01～P03 基础和 FR-030 项目 API 可以继续作为后续实现前提，
但不能据此宣称清单项、项目归档、正式检索或问答已经可用。下一步是实现清单项 API 与派生
状态，并分别验证正常、冲突和越权场景。
