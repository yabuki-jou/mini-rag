## 教学方式

- 学习者的目标岗位是 AI 应用开发工程师和 Agent 开发工程师。
- 讲解时结合本项目中的真实代码，不只讲抽象概念。
- 按步骤教学，一次只发送当前步骤；学习者回复 `ok` 后再进入下一步。
- 企业行政 Agent 的 10 步架构调整阶段曾获用户明确授权自动连续推进，现已全部完成；该授权不自动延续到后续学习阶段。
- 智慧档案 V1 的需求、架构、数据库、API 与实施计划基线已确认；AV1-P01 Parser 可行性验证与规则冻结、AV1-P02 虚构验收资料与人工标注、AV1-P03 数据库/模型/公共授权基础、AV1-P04 前置模型分层调整、P04.1 项目 CRUD/模板复制 API、P04.2 清单项 CRUD/派生状态、P05 项目内上传/重复校验/容量控制、P06 正式解析/快照/重试、AV1-A01 账号密码认证/JWT 会话/Bearer 身份切换的隔离验证，以及 AV1-C01 Chroma 独立可行性验证均已完成。P05 已通过 SQLite/TestClient 契约测试和当前 PostgreSQL 开发库的项目行锁并发验证，且测试数据与临时文件已清理。已确认将全部向量能力从 Milvus 迁移至 Chroma；AV1-C02 已完成代码、离线单测、本机项目命名空间切换/读写隔离删除验证，以及 Docker API + 外部 PostgreSQL + Chroma + 本地 BGE 健康验证。revision `0010_account_auth` 已在当前 PostgreSQL 开发库实际迁移并核对结构；当前以本地开发为准，云端部署、完整栈资源验证和部署拓扑均暂定至 V1 功能完成后。进入代码实践后恢复“一次一个步骤，学习者回复 `ok` 后再继续”的教学节奏，除非学习者再次明确授权连续推进。
- 每一步标题都使用 `（当前步骤/总步骤数）` 标记进度。
- 创建方法和类时，每个字段都需要说明作用，给出代码时添加适量注释，重点说明设计原因、数据流转和容易出错的地方，不必逐行注释。
- 每个实践步骤结束时，说明需要修改哪些文件、如何验证，以及预期结果。
- 后续编码统一采用 TDD：先为当前可观察行为写并运行 RED 测试，再进行最小 GREEN 实现，最后在测试保持通过时 REFACTOR；每一步的学习记录必须分别说明这三阶段的证据。LLM 输出质量另以真实模型评估和固定评估资料验证，不用 Mock 单测替代。
- 遇到错误时先根据报错和代码核实原因，不假定学习者或助手的判断一定正确。
- 如果需求、环境或代码状态存在矛盾或缺失，逐项询问或检查后再继续。

## 进度维护

- 完成一个步骤或一周的学习后，更新 `LEARNING_PLAN.md` 中的“当前进度”和对应检查项。
- 不要因为开始了某一步就将其标记为完成；必须在代码运行并通过该步验证后更新。
- 修改范围默认限定在当前项目，不修改原始参考项目 `py-doc-qa-deepseek-server`。

# AI 应用开发与 Agent 开发学习计划

## 学习目标

通过 12 周学习，掌握 Python、FastAPI、RAG、LangChain、LangGraph、Tool Calling 和基础工程化，最终完成一个可以写入简历并进行演示的企业知识库与业务 Agent 项目。

总体实践方法：

```text
跑通开源项目
→ 理解核心流程
→ 修改现有项目
→ 独立仿写
→ 加入业务 Agent
→ 工程化、部署和求职整理
```

## 当前进度

- 第 1–8 周：已完成
- 第 9 周：智慧档案与企业文档智能的需求、架构、数据库、API 与实施计划基线已确认；AV1-P01 Parser 可行性验证与规则冻结、AV1-P02 虚构验收资料与人工标注、AV1-P03 数据库/模型/公共授权基础、AV1-P04 前置模型分层调整、P04.1 项目 CRUD/模板复制 API、P04.2 清单项 CRUD/派生状态，以及 P05 项目内上传/重复校验/容量控制已完成
- 当前进度：企业行政 Agent 架构调整 10/10 已完成；一条真实 BGE + Milvus + DeepSeek 制度检索链路曾通过；智慧档案 V1 已完成实施计划、隔离 Parser 验证、本地虚构验收资料/人工标注、PostgreSQL `0005_archive_v1_schema`～`0008_legacy_business_comments` 前向迁移、项目/归档/清单/审计四个职责清晰的归档模型模块、项目所有权上下文、项目 CRUD、项目专属知识库范围与五项虚构模板复制 API、清单项 CRUD、项目/清单乐观锁、脱敏审计与 PostgreSQL 派生 `SATISFIED/MISSING/NOT_PROVIDED` 状态，以及 P05 的四格式项目上传、20 MiB/100 份容量限制、同项目哈希去重、跨项目隔离和 PostgreSQL 项目行锁并发控制；AV1-A01 的 Argon2 密码哈希、JWT 会话、Bearer 身份切换、隔离迁移和回归测试已完成；`0009_chroma_vector_comments` 与 revision `0010_account_auth` 已在当前 PostgreSQL 开发库实际前向迁移并核对认证表结构；Chroma 已完成一次目标 2 vCPU / 2 GB 主机的独立实验、运行时代码/离线单测迁移、本机 `mini_rag_tenant / mini_rag_chroma / mini_rag_knowledge_chunks_v1` 命名空间的实际创建、虚构 512 维写入、范围过滤和精确删除清理验证，以及 Docker API → 外部 PostgreSQL → Chroma → 本地 BGE 的 `/health` 全组件验证；当前开发与验证基线为本地，云端部署、完整栈资源验证及实际部署拓扑均暂定到 V1 全部功能完成后再决定；P06 已完成项目文档成功解析、受控失败持久化、专用解析重试、四格式路由验收和重复解析快照保护，正式归档状态机、Chroma Final 索引和端到端验收尚未实现
- 第 10–12 周：未开始
- 当前学习步骤：AV1-P06 已完成，覆盖 `UPLOADED → PARSED` 快照创建、解析失败后 `PARSE_FAILED`/受控错误摘要持久化、`PARSE_FAILED → PARSED` 专用重试、PDF/DOCX/TXT/MD 四格式项目路由、重复解析拒绝及快照不替换。下一步为 AV1-P07 手工草稿、字段证据与人工检查。P05 已通过上传、重复、容量、类型、越权与跨项目隔离的 SQLite/TestClient 契约测试；当前 PostgreSQL 开发库的 99→100 两请求并发竞争验证为一成功、一容量拒绝，UUID 范围测试数据和临时原文件已清理。P04.2 的清单项/项目乐观锁真实 PostgreSQL 并发竞争仍未验证，不得与本次 P05 项目行锁证据混淆。AV1-A01、AV1-C02 的既有完成状态不变；P02 Ground Truth 也仍不代表后续 LLM 质量验收已完成

> 进度只能在完成代码实践和验证后更新。

## 第一阶段：基础准备（第 1–2 周）

### 第 1 周：Python 与项目环境

学习内容：

- Python 常用语法、函数、类和异常处理
- 文件读写与 JSON
- `pip`、Conda 虚拟环境
- 环境变量和 `.env`
- Git 与 GitHub 基础操作

验收目标：能看懂普通 Python 项目，并独立创建环境、安装依赖和启动项目。

### 第 2 周：FastAPI 与 LLM API

学习内容：

- FastAPI 路由
- 请求参数和响应模型
- Pydantic 参数校验
- 异常处理
- 调用云端 DeepSeek API
- 普通响应与流式响应
- Swagger 接口测试

验收目标：独立完成一个调用 DeepSeek 的聊天接口。

## 第二阶段：小型 RAG 项目（第 3–5 周）

### 第 3 周：RAG 基础流程

学习内容：

- `Document` 对象和文档加载器
- 文本切分与 Chunk
- Embedding 和语义相似度
- Chroma 向量数据库
- Retriever 检索器
- Prompt 与上下文拼接

需要掌握的数据流：

```text
上传文档 → 解析文档 → 切分文本 → Embedding
→ 写入向量库 → 检索相关文本 → 交给大模型回答
```

### 第 4 周：精读 GitHub RAG 项目

参考项目：`YuiGod/py-doc-qa-deepseek-server`

重点理解：

- 项目目录结构
- 文档上传和数据库记录
- 文档加载与切分
- BGE Embedding 模型
- Chroma 持久化
- DeepSeek 问答
- 会话和聊天记录

### 第 5 周：改造 RAG 项目

已完成的主要改造：

- [x] Ollama 改为云端 DeepSeek
- [x] Embedding 模型改为本地加载
- [x] 配置集中到 `.env`
- [x] 路径改为项目相对路径
- [x] 文档和向量库整理到 `data/`
- [x] 全量向量化改为增量同步
- [x] 增加文档来源元数据和回答引用
- [x] 改善聊天记录保存和参数校验
- [x] 增加测试并更新 README

## 第三阶段：企业级 RAG 与 mini RAG（第 6–7 周）

### 第 6 周：分析企业级 RAG 项目

可选择 Dify、MaxKB、RAGFlow、FastGPT 或 QAnything。无需通读全部源码，重点分析：

- 文档上传、解析和 Chunk 策略
- Embedding、混合检索和 Rerank
- 引用溯源和知识库管理
- 用户权限、多租户和对话历史
- 任务队列与异步处理

### 第 7 周：完善自己的 mini RAG

目标功能：

- 文档上传、删除和列表
- 增量向量同步
- 文档删除后清理对应向量
- 相似度分数与检索阈值
- 引用文档名称和文本片段
- 会话管理、统一异常处理和日志记录
- 测试与 README

## 第四阶段：Agent（第 8–9 周）

### 第 8 周：Tool Calling 与 LangGraph

当前实践项目：企业知识库检索 Agent 基座。员工请假领域已从项目删除，历史
完成记录不再代表当前代码能力。

当前正式工具：

```text
search_company_policy      查询当前会话绑定的制度知识库
```

已完成的基础练习：

- [x] 理解 Tool Calling 边界并定义 `@tool`
- [x] 使用 Pydantic/LangChain Schema 限制模型参数
- [x] 让 DeepSeek 判断是否调用工具并生成查询文本
- [x] 使用 LangGraph 实现 State、节点、边和条件路由
- [x] 练习参数错误、工具异常、失败重试和脱敏日志
- [x] 使用独立 SQLite Checkpointer 恢复多轮消息和授权范围
- [x] 将业务数据库从 SQLite 切换为 PostgreSQL 配置与 Alembic

当前流程：

```text
用户输入
→ 服务端从 AgentSession 注入 user_id + kb_id
→ DeepSeek 判断是否调用 search_company_policy
→ Chroma 在固定范围内检索
→ DeepSeek 仅依据工具结果回答
→ PostgreSQL 保存脱敏工具日志
→ Checkpoint SQLite 保存对话状态
```

当前验收目标：能够解释模型、LangGraph、Tool、Application Service、
PostgreSQL、Checkpoint SQLite 和 Chroma 的职责边界，并通过 Swagger 演示制度
问答、无依据拒答、多轮历史、会话越权保护和脱敏调用日志。

历史真实链路曾用本地 BGE、Milvus 和 DeepSeek 验证单文档单问题，但它不能
替代当前 PostgreSQL 版本复验，也不能证明多文档召回质量。

### 第 9 周：面向智慧档案与企业文档智能重新设计可演示业务闭环

状态：需求、架构、数据库与 API 设计基线已确认；AV1-P01～P06 已完成并通过对应验证，其中 P06 覆盖项目文档成功解析、受控失败记录、专用重试、四格式路由和快照保护。正式归档和后续业务流程仍未实现。

已确认当前唯一业务方向为“智慧档案与企业文档智能”，聚焦工程项目资料的
归档、结构化、检索与原文追溯。当前不推进标书投标、标书解析生成或投标合规
审查。目标用户、资料样例、档案字段、分类与缺失规则来源、人工确认点、评测集
和验收指标已在需求文档中确认。实施计划生成前，不能只替换知识库来宣称形成真实归档
业务闭环，也不得开始实现。

## 第五阶段：工程化（第 10 周）

### 第 10 周：系统整合与部署

建议职责划分：

```text
Spring Boot：用户、认证、权限、会话、操作日志
FastAPI：RAG、Embedding、向量检索、DeepSeek、LangGraph Agent
```

学习内容：

- Dockerfile 和 Docker Compose
- 配置分环境管理与 API Key 安全
- 数据持久化
- 健康检查
- 日志和异常追踪
- Java 与 Python 服务联调

验收目标：一条命令启动完整项目，并能说明每个服务的职责和调用链。

## 第六阶段：求职准备（第 11–12 周）

### 第 11 周：整理项目

需要完成：

- 项目 README
- 系统架构图、RAG 数据流程图和 Agent 状态图
- API 文档和 Docker 启动说明
- 演示数据、截图或演示视频
- GitHub 提交记录整理

最终演示链路：

```text
上传制度文档 → 建立向量索引 → 提问并返回引用
→ Agent 查询当前会话知识库中的制度 → 查看脱敏工具调用日志
```

### 第 12 周：简历与面试

重点准备：

- Python、FastAPI、LangChain 和 LangGraph
- RAG 流程、Chunk、Embedding 和向量数据库
- Top-K、相似度阈值与 Rerank
- Prompt 与上下文管理
- Tool Calling 与 Agent 状态管理
- 异常重试、权限控制和防止工具误调用
- 项目遇到的问题、判断过程和解决方案

简历描述参考：

> 基于 FastAPI、LangChain、LangGraph、PostgreSQL、Chroma 和 DeepSeek 实现企业知识库与只读 Agent，支持文档增量向量化、语义检索、引用溯源、会话管理、制度检索工具调用、参数校验、异常重试及脱敏调用日志。

## 跨对话继续学习

在新的对话中，可以直接说明：

```text
请先阅读项目根目录的 AGENTS.md 和 LEARNING_PLAN.md，
根据其中的当前进度继续带我学习。一次只进行一个步骤，
我回复 ok 后再进入下一步。
```
