# 智慧档案与企业文档智能 V1 API 设计

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 对应基线 | FR-019～FR-041、BR-008～BR-029、NFR-011～NFR-020 |
| 依赖设计 | `docs/requirements.md`、`docs/architecture.md`、`docs/database-design.md` |
| 文档状态 | API 设计基线已确认；AV1-A01 账号密码认证接口、Bearer 身份切换及隔离测试已实现；`0010` 已在当前 PostgreSQL 开发库实际迁移并核对结构。FR-030 项目 CRUD/模板复制、FR-031 清单项 CRUD/派生状态、FR-032 项目内上传、FR-033 四格式解析/失败记录/专用重试和 FR-035 手工草稿/字段检查已实现；FR-034 AI 建议及 FR-036 起后续正式归档接口尚未实现 |
| 更新日期 | 2026-08-26 |
| 操作界面 | Swagger/OpenAPI 与 API 客户端 |

本文件定义智慧档案 V1 的目标 HTTP 契约。当前实际代码已提供项目 CRUD、清单项
`GET/POST/PATCH/DELETE`、项目内上传、首次解析、解析重试和人工草稿/字段检查；AI 建议、正式归档、正式目录、问答和审计查询接口仍未实现，
不能作为已可调用 API 宣传。

## 2. 范围与路由边界

### 2.1 两套接口共存

| 接口组 | 路径前缀 | 当前状态 | 用途 |
|---|---|---|---|
| 认证与旧知识库/Agent 基座 | `/auth`、`/knowledge-bases`、`/agent-sessions` 等 | 认证接口和 Bearer 保护已实现；其余以当前代码为准 | 身份、制度检索、旧 RAG 与单 Agent 演示 |
| 智慧档案 V1 | `/projects` | 已实现 FR-030/031/032/033 与 FR-035 手工草稿/字段检查；AI 建议、正式目录与问答仍为设计 | 工程项目归档、清单、正式目录与问答 |

智慧档案接口不得复用旧的 `/knowledge-bases/{kb_id}/documents` 作为业务入口；客户端
只提交 `project_id`。服务端从项目所有权校验获取 `user_id + project_id + kb_id` 的
`ProjectContext`，不接受客户端提交或覆盖其中任何身份字段。

### 2.2 路由总览

| 需求 | 方法与路径 | 成功码 | 说明 |
|---|---|---|---|
| FR-030 | `POST /projects` | 201 | 创建项目与项目独立知识库范围 |
| FR-030 | `GET /projects` | 200 | 列出当前用户项目 |
| FR-030 | `GET /projects/{project_id}` | 200 | 项目详情 |
| FR-030 | `PATCH /projects/{project_id}` | 200 | 修改名称、说明与版本 |
| FR-030 | `DELETE /projects/{project_id}` | 204 | 删除无文档项目及其清单 |
| FR-019 | `POST /auth/register` | 201 | 注册账号密码用户 |
| FR-020 | `POST /auth/login` | 200 | 登录并创建认证会话 |
| FR-020 | `POST /auth/refresh` | 200 | 使用 Refresh Token 签发新 Access Token |
| FR-021 | `POST /auth/logout` | 204 | 撤销当前认证会话 |
| FR-031 | `GET /projects/{project_id}/checklist-items` | 200 | 清单项与缺失/未提供结果 |
| FR-031 | `POST /projects/{project_id}/checklist-items` | 201 | 新增项目清单项 |
| FR-031 | `PATCH /projects/{project_id}/checklist-items/{item_id}` | 200 | 修改清单项与版本 |
| FR-031 | `DELETE /projects/{project_id}/checklist-items/{item_id}` | 204 | 删除清单项与关联 |
| FR-032 | `POST /projects/{project_id}/documents` | 201 | 上传原文件，仅创建 `UPLOADED` |
| FR-033 | `POST /projects/{project_id}/documents/{document_id}/parse` | 200 | 首次手动解析 |
| FR-033 | `POST /projects/{project_id}/documents/{document_id}/parse-retry` | 200 | 仅解析失败后重试 |
| FR-034 | `POST /projects/{project_id}/documents/{document_id}/suggestions` | 200 | 首次生成 AI 建议 |
| FR-034 | `POST /projects/{project_id}/documents/{document_id}/suggestions/retry` | 200 | 建议失败后的明确重试 |
| FR-034 | `POST /projects/{project_id}/documents/{document_id}/suggestions/regenerate` | 200 | 未人工编辑草稿的受控重新生成 |
| FR-035 | `POST /projects/{project_id}/documents/{document_id}/manual-draft` | 200 | 创建七字段空白人工草稿 |
| FR-035 | `GET /projects/{project_id}/documents/{document_id}/draft` | 200 | 读取字段草稿与证据 |
| FR-035 | `PUT /projects/{project_id}/documents/{document_id}/fields/{field_name}` | 200 | 修改一个字段草稿与检查状态 |
| FR-036 | `POST /projects/{project_id}/documents/{document_id}/confirm` | 200 | 正式入档或重新确认 |
| FR-036 | `POST /projects/{project_id}/documents/{document_id}/cancel-confirmation` | 200 | 取消确认并退出正式范围 |
| FR-037 | `GET /projects/{project_id}/documents/{document_id}/checklist-link-suggestions` | 200 | 按类型/阶段返回非正式关联建议 |
| FR-037 | `GET /projects/{project_id}/documents/{document_id}/checklist-links` | 200 | 查看已确认或失效关联 |
| FR-037 | `POST /projects/{project_id}/documents/{document_id}/checklist-links` | 201 | 人工确认档案—清单关联 |
| FR-037 | `DELETE /projects/{project_id}/documents/{document_id}/checklist-links/{link_id}` | 204 | 删除关联 |
| FR-038 | `GET /projects/{project_id}/documents` | 200 | 文档处理列表，包含全部未删除归档文档 |
| FR-038 | `GET /projects/{project_id}/archives` | 200 | 正式档案目录，仅 `CONFIRMED` |
| FR-038 | `GET /projects/{project_id}/archives/{document_id}` | 200 | 正式档案详情 |
| FR-039 | `POST /projects/{project_id}/archive-retrieval` | 200 | 返回正式范围内的证据 Chunk |
| FR-039 | `POST /projects/{project_id}/archive-questions` | 200 | 带证据回答或明确拒答 |
| FR-040 | `DELETE /projects/{project_id}/documents/{document_id}` | 204 | 物理删除或恢复未完成删除 |
| FR-041 | `GET /projects/{project_id}/audit-logs` | 200 | 查询本人项目的脱敏审计 |

`GET /projects/{project_id}/documents` 与 `GET /projects/{project_id}/archives` 必须是
不同 Router、Service 与查询条件；禁止用同一列表接口加 `include_pending` 参数混合
两种语义。

## 3. 通用约定

### 3.1 身份、所有权与请求头

| 项目 | 约定 |
|---|---|
| 身份 | 除 `/auth/register`、`/auth/login`、`/auth/refresh` 和 `/health` 外，接口必须携带 `Authorization: Bearer <Access Token>` |
| 所有权 | 服务先验证用户存在，再验证项目归属；资源必须属于路径中的项目 |
| 请求追踪 | 服务返回 `X-Request-ID`；客户端可传入合法 UUID，否则服务生成 |
| 内容类型 | JSON 接口使用 `application/json`；上传接口使用 `multipart/form-data` |
| 时间格式 | RFC 3339 UTC，例如 `2026-08-06T09:30:00Z` |
| UUID | 所有资源 ID 使用 UUID 字符串 |

用户身份仅来自已验证的 Access Token；`X-User-ID` 不再是受支持的身份头。客户端不得提交 `owner_id`、`user_id`、`kb_id`、
`confirmed_by`、`actor_id`、`file_hash`、`snapshot_hash`、`visibility_blocking` 或内部
操作状态；这些值必须由服务端计算或注入。

Chroma 不提供面向客户端的 API 路径或鉴权边界：它只位于部署内部网络，由 FastAPI 服务端
调用。`VECTOR_UNAVAILABLE` 是向量后端不可用的稳定业务错误，不暴露 Chroma 地址、
配置或内部异常。

### 3.1.1 账号密码认证（FR-019～FR-021）

`POST /auth/register` 接受 `username`、`name` 和 `password`，返回不含密码或哈希的用户信息。
用户名规范化为小写，必须全局唯一。

`POST /auth/login` 接受 `username` 和 `password`，成功时返回 Access Token（30 分钟）、
Refresh Token（7 天）及其秒数。`POST /auth/refresh` 的 JSON 请求体仅含 `refresh_token`，
成功时仅返回新的 Access Token。`POST /auth/logout` 必须携带当前会话的 Access Token，
成功时返回 `204` 并撤销对应认证会话。

| 接口 | 是否需要认证 | 请求体 | 成功响应 | 业务边界 |
|---|---|---|---|---|
| `POST /auth/register` | 否 | `username`、`name`、`password` | `201 UserRead` | 用户名规范化为小写；密码、密码哈希不在响应中出现 |
| `POST /auth/login` | 否 | `username`、`password` | `200 {access_token, refresh_token, token_type, access_expires_in, refresh_expires_in}` | 创建一条可撤销认证会话；`token_type` 为 `bearer` |
| `POST /auth/refresh` | 否 | `refresh_token` | `200 {access_token, token_type, access_expires_in}` | 只接受 Refresh Token；只签发新的 Access Token，不轮换 Refresh Token |
| `POST /auth/logout` | Bearer Access Token | 无 | `204` | 撤销当前 Access Token 所属会话；重复注销保持幂等 |

`username` 允许 3～50 位小写字母、数字、下划线或连字符；`name` 为 1～100 位显示名；注册密码为
8～128 个字符。Access Token 的有效期为 30 分钟，Refresh Token 与认证会话的有效期为 7 天。
服务器未配置有效 `AUTH_JWT_SECRET` 时，涉及 Token 签发或验证的请求返回受控的
`503 AUTH_NOT_CONFIGURED`，不得使用示例默认值签发 Token。

### 3.2 并发、幂等与分页

- 修改项目、清单项、字段、确认和关联时，请求体必须传对应资源的 `expected_version`。
  与服务器当前版本不一致时返回 `409 VERSION_CONFLICT`，不覆盖新数据。
- 创建清单项必须携带项目当前 `expected_project_version`；创建成功后项目版本原子加一。
- Parse、suggest、confirm 和 delete 的并行请求返回 `409 DOCUMENT_OPERATION_IN_PROGRESS`。
- 文件上传由同项目 `file_hash` 去重；不同项目同哈希不互相暴露信息。
- 列表使用 `page`（从 1 开始）和 `page_size`，默认 `page=1`、`page_size=20`、最大
  `100`。项目、文档处理、正式目录、清单项和审计列表均以各自主要时间字段倒序、`id DESC`
  作为次级排序，保证稳定分页。
- 第一版不启用 `Idempotency-Key`。版本锁、单 RUNNING 操作锁、重复确认业务幂等、
  `file_hash` 去重和未完成删除恢复共同防止关键副作用重复；不承诺客户端因响应丢失重发
  POST 时返回第一次响应。清单项创建、上传和关联创建分别由项目版本、文件哈希、
  文档/清单项版本约束防止重复成功结果。

### 3.3 统一成功与错误响应

列表响应：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

错误响应保持 `AppError` 契约；预期业务错误仅在已明确约定且可安全返回时包含可选的
`details`：

```json
{
  "error": {
    "code": "PROJECT_NOT_FOUND",
    "message": "项目不存在或无权访问。"
  }
}
```

参数格式或类型错误返回 `422 VALIDATION_ERROR`，并可包含 Pydantic 校验 `details`。业务
`details` 不得包含存储路径、密钥、原文件内容、模型提示词或其他项目资源。
未知异常只在服务端日志记录，客户端固定接收 `500 INTERNAL_ERROR`，不返回堆栈、
数据库地址、文件路径、模型提示词或外部服务原始异常。

### 3.4 固定字典的 API 值

| 字段 | API 值 |
|---|---|
| `document_type` | `CONTRACT`、`DESIGN`、`CONSTRUCTION`、`MEETING_MINUTES`、`ACCEPTANCE`、`OTHER` |
| `project_stage` | `PREPARATION`、`DESIGN`、`CONSTRUCTION`、`ACCEPTANCE`、`CROSS_STAGE`、`OTHER_STAGE` |
| `field_name` | `TITLE`、`DOCUMENT_TYPE`、`DOCUMENT_DATE`、`AUTHORING_ORGANIZATION`、`VERSION_NUMBER`、`PROJECT_STAGE`、`KEYWORDS` |
| `review_status` | `PENDING_CHECK`、`VALUE_CONFIRMED`、`EMPTY_ACCEPTED` |
| `field_source` | `AI`、`MANUAL` |
| `location_type` | `PDF_PAGE`、`DOCX_PARAGRAPH`、`TEXT_LINE_RANGE` |

Swagger 的字段说明同时展示中文名称。资料类型和项目阶段不接受自由字符串；人工更正
也只能从上述字典选择。

### 3.5 免责声明与数据提示

- Swagger 首页描述与 README 必须声明：调用 `suggestions`、`suggestions/retry`、
  `suggestions/regenerate` 或 `archive-questions` 时，文档片段可能发送给外部 DeepSeek；
  仅允许虚构或已脱敏资料。
- Swagger 首页和相关接口的 OpenAPI 描述必须声明：内置清单为虚构演示规则，不代表
  法定或行业归档要求。

## 4. 公共响应对象

### 4.1 Project

```json
{
  "id": "uuid",
  "name": "示例工程",
  "description": "可选项目说明",
  "uses_demo_checklist": true,
  "active_document_count": 0,
  "version": 1,
  "created_at": "2026-08-06T09:30:00Z",
  "updated_at": "2026-08-06T09:30:00Z"
}
```

不返回内部 `kb_id`、所有者 ID 或存储路径。

### 4.2 ProcessDocument

文档处理列表与单文档操作返回下列受控字段：

```json
{
  "id": "uuid",
  "filename": "施工方案.docx",
  "file_hash": "sha256-hex",
  "status": "PENDING_CONFIRMATION",
  "last_error": {
    "code": null,
    "message": null
  },
  "field_summary": {
    "checked_count": 7,
    "total_count": 7
  },
  "confirmed_at": null,
  "version": 4,
  "uploaded_at": "2026-08-06T09:30:00Z",
  "updated_at": "2026-08-06T09:35:00Z"
}
```

它不返回原始文件系统路径、内部 `ArchiveOperation`、模型原始输出、完整快照正文或
其他项目的重复文档信息。

### 4.3 FieldDraft 与 Evidence

```json
{
  "field_name": "DOCUMENT_DATE",
  "text_value": null,
  "date_value": "2026-05-20",
  "json_value": null,
  "review_status": "VALUE_CONFIRMED",
  "source": "AI",
  "no_source_evidence": false,
  "evidences": [
    {
      "id": "uuid",
      "excerpt": "编制日期：2026年5月20日",
      "location_type": "DOCX_PARAGRAPH",
      "location_start": 3,
      "location_end": 3,
      "normalized_anchor": "编制日期：2026年5月20日"
    }
  ]
}
```

人工无证据值必须返回 `source = MANUAL`、`no_source_evidence = true` 且空证据列表。
标题不可由原文件名自动填充；`TITLE` 为空时只能由人工填入后确认。

### 4.4 Citation 与 ArchiveAnswer

```json
{
  "answer_status": "ANSWERED",
  "answer": "施工方案的编制单位为示例建设公司。",
  "citations": [
    {
      "citation_id": "C1",
      "document_id": "uuid",
      "filename": "施工方案.docx",
      "location_type": "DOCX_PARAGRAPH",
      "location_start": 3,
      "location_end": 3,
      "excerpt": "编制单位：示例建设公司"
    }
  ]
}
```

无依据时返回 `200` 与 `answer_status = REFUSED_NO_EVIDENCE`、空 `citations`，而不是
伪造引用。模型关闭或未配置是依赖不可用，返回 `503 ARCHIVE_ANSWER_UNAVAILABLE`，
不降级为编造回答。

### 4.5 ArchiveDetail

`GET /projects/{project_id}/archives/{document_id}` 返回已确认档案及其七个正式字段和
字段证据。人工无原文证据字段可以显示，但必须带 `source = MANUAL` 与
`no_source_evidence = true`，且不作为问答依据。

```json
{
  "id": "uuid",
  "filename": "施工方案.docx",
  "status": "CONFIRMED",
  "confirmed_at": "2026-08-06T09:30:00Z",
  "fields": [{"field_name": "TITLE", "text_value": "施工方案", "review_status": "VALUE_CONFIRMED", "source": "AI", "no_source_evidence": false, "evidences": []}],
  "version": 4
}
```

## 5. 项目接口（FR-030）

### `POST /projects`

请求：

```json
{
  "name": "示例工程",
  "description": "可选",
  "use_demo_checklist": true
}
```

- `name` 必填，服务去除首尾空格后校验同一用户唯一；项目名称区分大小写。
- 客户端不提交 `kb_id`；服务创建项目专属知识库范围。
- 选择演示清单时复制五个虚构规则为该项目独立清单项。
- 成功返回 `201 Project`。
- 同名返回 `409 PROJECT_NAME_CONFLICT`。

### `GET /projects` 与 `GET /projects/{project_id}`

只返回当前用户项目。列表默认按 `updated_at DESC, id DESC`，使用公共分页响应。

### `PATCH /projects/{project_id}`

请求只允许修改 `name`、`description` 与 `expected_version`：

```json
{
  "name": "已更名工程",
  "description": null,
  "expected_version": 1
}
```

空字符串名称返回 `422 VALIDATION_ERROR`；同名和旧版本分别返回稳定 `409` 错误。

### `DELETE /projects/{project_id}`

仅项目没有任何未删除文档时返回 `204`，并级联删除项目清单与项目审计。存在任意
`UPLOADED`、失败、待确认、已确认或待重新确认文档时返回
`409 PROJECT_HAS_DOCUMENTS`；不提供“删项目即删全部文档”接口。

项目删除不得删除其内部 `knowledge_bases` 记录。该记录可能仍被旧 RAG/Agent 资料使用，
并可继续在旧 `/knowledge-bases` 路由中可见；V1 的 `/projects` 不再显示已删除项目。

## 6. 清单接口（FR-031、FR-037）

### `GET /projects/{project_id}/checklist-items`

返回项目清单与实时派生状态：

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "施工方案",
      "document_type": "CONSTRUCTION",
      "is_required": true,
      "project_stage": "CONSTRUCTION",
      "description": "每个施工阶段至少一份",
      "fulfillment_status": "MISSING",
      "confirmed_document_count": 0,
      "version": 1
    }
  ]
}
```

`fulfillment_status` 仅为 `SATISFIED`、`MISSING`、`NOT_PROVIDED`：必需项未满足为
`MISSING`，可选项未满足为 `NOT_PROVIDED`。无清单时返回空数组，不输出项目级
缺失结论。

### `POST /projects/{project_id}/checklist-items`

请求除 `name`、`document_type`、`is_required`、`project_stage`、可选 `description` 外，
必须携带 `expected_project_version`。服务在同一事务中校验项目版本、创建清单项、写审计并
使 `projects.version` 原子加一；旧版本返回 `409 VERSION_CONFLICT`。

成功 `201` 返回新清单项和新的项目版本：

```json
{"item": {"id": "uuid", "name": "施工方案", "document_type": "CONSTRUCTION", "is_required": true, "project_stage": "CONSTRUCTION", "fulfillment_status": "MISSING", "version": 1}, "project_version": 2}
```

### `PATCH` / `DELETE /projects/{project_id}/checklist-items/{item_id}`

`PATCH` 必须携带 `expected_version`。修改名称、资料类型或项目阶段时，服务使既有
关联变为 `INVALIDATED` 并立即重算状态；仅修改说明不使关联失效。删除成功返回 `204`
并删除关联、写审计。上述接口不提供用户自定义模板创建、保存或复用能力。

### 档案—清单关联

`GET /projects/{project_id}/documents/{document_id}/checklist-link-suggestions` 只依据
资料类型和项目阶段返回建议，不会自动创建或满足清单项。

创建关联请求：

```json
{
  "checklist_item_id": "uuid",
  "expected_document_version": 4,
  "expected_checklist_item_version": 1
}
```

`POST .../checklist-links` 只接受 `CONFIRMED` 档案，成功创建 `CONFIRMED` 关联并返回
`201`。一份档案可关联零到多个清单项，一个清单项可由多份档案满足。不同项目资源、
待确认文档、重复关联或旧版本分别返回 `403/404`、`409`、`409`、`409`。

## 7. 文档上传、处理与字段草稿（FR-032～FR-036）

### `POST /projects/{project_id}/documents`

使用 `multipart/form-data`，唯一文件字段为 `file`。不接收客户端声明的资料类型、
阶段、哈希、状态或归属。

上传前校验：

- 仅 PDF（有有效文本）、DOCX、TXT、MD；扩展名不支持返回 `415 FILE_TYPE_UNSUPPORTED`。
- 文件最大 20 MB；超限返回 `413 FILE_TOO_LARGE`。
- 当前项目最多 100 份未删除文档；超限返回 `409 PROJECT_DOCUMENT_LIMIT_REACHED`。
- 同项目原文件 `file_hash` 已存在时返回 `409 DUPLICATE_FILE`，仅返回当前项目已有
  文档的 `id`、`filename`、`status`，位置为 `error.details`；不保存文件、不建记录、不写向量或审计。

成功创建原文件和 `UPLOADED` 归档记录，返回 `201 ProcessDocument`。上传不会自动解析
或调用 DeepSeek。

### Parse 与 parse retry

| 接口 | 允许初始状态 | 成功状态 | 失败行为 |
|---|---|---|---|
| `POST .../parse` | `UPLOADED` | `PARSED` | 无有效文本转 `PARSE_FAILED`，返回 `422 PARSE_TEXT_UNAVAILABLE` |
| `POST .../parse-retry` | `PARSE_FAILED` | `PARSED` | 仍失败保留最新受控失败摘要 |

扫描 PDF 使用稳定错误 `SCANNED_PDF_UNSUPPORTED` 与“暂不支持扫描件，请上传文本版资料”。
已解析文档再次调用普通 `parse` 返回 `409 PARSE_NOT_ALLOWED`，不重复生成快照、向量或
档案数据。连接失败和超时仅自动重试一次；最终失败返回稳定 `503`，不暴露解析器堆栈。

### Suggest 与人工草稿

| 接口 | 允许初始状态 | 成功结果 |
|---|---|---|
| `POST .../suggestions` | `PARSED` | AI 草稿、字段证据，转 `PENDING_CONFIRMATION` |
| `POST .../suggestions/retry` | `SUGGESTION_FAILED` | AI 草稿、字段证据，转 `PENDING_CONFIRMATION` |
| `POST .../suggestions/regenerate` | `PENDING_CONFIRMATION` 且无人工编辑 | 原子替换 AI 草稿，状态保持不变 |
| `POST .../manual-draft` | `PARSED`、`SUGGESTION_FAILED` | 七字段空草稿，全部 `PENDING_CHECK`，转 `PENDING_CONFIRMATION` |

`POST .../suggestions/regenerate` 请求：

```json
{"expected_version": 4}
```

`suggestions/regenerate` 必须携带文档当前 `expected_version`。“无人工编辑”固定为：七个
字段均为 `PENDING_CHECK`，且没有 `source = MANUAL` 的字段或人工修改的字段证据。请求
到达时发现人工内容，立即返回 `409 SUGGESTION_WOULD_OVERWRITE_MANUAL_DRAFT`，不调用模型。
字段保存接口不得允许客户端伪造 `source = AI`；只要用户保存字段或证据修改，服务必须将其
标记为人工修改。

重新生成时服务先在内存中完成模型调用、字典和证据校验；随后在同一事务中再次校验版本和
人工内容。版本变化返回 `409 VERSION_CONFLICT`；人工内容出现返回
`409 SUGGESTION_WOULD_OVERWRITE_MANUAL_DRAFT`；两者均保持原草稿、版本和状态。校验通过
才原子替换现有草稿并递增版本。模型未配置、关闭、连接失败或超时返回
`503 ARCHIVE_SUGGESTION_UNAVAILABLE`；模型输出不合格返回 `422 SUGGESTION_INVALID_OUTPUT`；
两种情况均保留现有草稿与 `PENDING_CONFIRMATION`。首次建议或失败后重试遇到上述错误时，
文档进入或保持 `SUGGESTION_FAILED`；用户仍可调用 `manual-draft` 完成纯手工归档。

### `GET .../draft` 与 `PUT .../fields/{field_name}`

草稿详情返回 `ProcessDocument`、七个 `FieldDraft`、当前快照元数据和允许的下一步动作。
只要文档已解析，就可以读取草稿；编辑只允许 `PENDING_CONFIRMATION` 或
`PENDING_RECONFIRMATION`。

字段更新请求：

```json
{
  "text_value": "施工方案",
  "date_value": null,
  "json_value": null,
  "review_status": "VALUE_CONFIRMED",
  "source": "MANUAL",
  "no_source_evidence": true,
  "evidences": [],
  "reason": "原文未写标题，依据项目资料目录补充",
  "expected_version": 4
}
```

规则：

- `DOCUMENT_DATE` 只使用 `date_value`；`KEYWORDS` 只使用字符串数组 `json_value`；
  其他字段只使用 `text_value`。
- `EMPTY_ACCEPTED` 时所有值为空；`VALUE_CONFIRMED` 时值必须存在。
- AI 非空字段必须带当前快照、当前文档的至少一条证据；否则 `422 AI_FIELD_EVIDENCE_REQUIRED`。
- 人工无证据值必须设置 `source = MANUAL`、`no_source_evidence = true`，证据列表为空。
- `source = AI` 仅由建议服务写入；客户端更新字段或证据时，服务必须记录人工来源，不能
  让客户端自行声明 AI 来源以绕过重新生成保护。
- 编辑已确认档案的任何正式字段或证据时，服务先转为 `PENDING_RECONFIRMATION` 并从
  正式范围排除，再返回更新后的草稿。
- 每次字段或证据保存、首次建议成功、建议重试成功、人工草稿创建、重新生成替换、确认或
  取消确认成功，均使 `archive_documents.version` 原子加一；解析和解析重试不递增。
  所有声明 `expected_version` 的接口必须使用响应返回的最新版本。

### 确认与取消确认

`POST .../confirm` 请求：

```json
{"expected_version": 4}
```

确认前服务校验七个字段均非 `PENDING_CHECK`、标题和资料类型为非空
`VALUE_CONFIRMED`、AI 非空字段有当前文档证据、并发版本一致。成功时生成 Final Chunk、
更新 `CONFIRMED`、写确认审计并返回正式档案；索引/外部依赖失败时不返回成功确认。

文档已为 `CONFIRMED` 且 `expected_version` 等于当前版本时，重复确认以 `200` 幂等返回
当前档案，不重建 Final Chunk、不新增成功审计。该规则是业务幂等而非传输重放幂等；旧版本
（包括响应丢失后的旧请求）返回 `409 VERSION_CONFLICT`。

`POST .../cancel-confirmation` 请求也携带 `expected_version`，仅 `CONFIRMED` 可调用。
它转为 `PENDING_RECONFIRMATION`、立即退出正式目录与问答，并清理 Final Chunk；若外部
清理失败，返回稳定失败结果但仍保守排除该档案。
取消确认不改变 `checklist_links.status`；清单满足性由档案与关联均为 `CONFIRMED` 派生，
因此取消后立即变为“缺失/未提供”，再次确认后自动恢复满足。

## 8. 处理列表、正式目录与问答（FR-038、FR-039）

### 文档处理列表

`GET /projects/{project_id}/documents` 从 `documents INNER JOIN archive_documents` 查询
当前项目全部未删除归档文档。文件名、原文件哈希和上传时间来自 `documents`；归档状态、
确认时间、字段检查汇总和受控失败摘要来自 `archive_documents`。它不查询 Chroma，也不
返回内部操作状态。

支持可选 `status` 筛选和公共分页，默认 `updated_at DESC, id DESC`。

### 正式档案目录与详情

`GET /projects/{project_id}/archives` 只查询：

```text
archive_document.status == CONFIRMED
AND no visibility-blocking operation exists
```

支持以下可选筛选：

| 参数 | 说明 |
|---|---|
| `document_type` | 固定资料类型 |
| `project_stage` | 固定项目阶段 |
| `document_date_from` / `document_date_to` | 日期闭区间 |
| `document_date_is_null` | `true` 时仅返回日期为空资料，不能与日期区间同时使用 |
| `authoring_organization` | 编制单位精确匹配；大小写语义由数据库排序规则决定 |

默认 `confirmed_at DESC, id DESC`。`GET .../archives/{document_id}` 仅允许读取正式档案；待确认、
待重新确认或删除阻断文档返回 `404 ARCHIVE_NOT_FORMAL`，不通过详情泄露草稿字段。

### `POST /projects/{project_id}/archive-retrieval`

请求：

```json
{"query": "施工方案的编制单位是什么？", "top_k": 5}
```

该接口只用于 Swagger 中核对正式检索证据，返回当前项目、当前正式档案范围内的 Chunk
引用；`top_k` 默认 `5`、最大 `10`。该接口与问答共用同一正式范围过滤和
`min_relevance_score`；数值、Chroma 距离度量类型和比较方向由门槛 2 验证后冻结。没有依据
返回 `200` 与空 `items`。不得引用待确认、待重新确认、人工无证据字段或其他项目资料。

```json
{"items": [{"chunk_id": "uuid", "document_id": "uuid", "filename": "施工方案.docx", "location_type": "DOCX_PARAGRAPH", "location_start": 3, "location_end": 3, "excerpt": "编制单位：示例建设公司", "score": 0.62}], "requested_top_k": 5, "returned_count": 1}
```

这是 Top-K 结果，不使用分页；`score` 的度量类型与比较方向必须与冻结后的阈值一致。

### `POST /projects/{project_id}/archive-questions`

请求与 `archive-retrieval` 相同，但不需要 `top_k`：

```json
{"question": "施工方案的编制单位是什么？"}
```

服务先用 PostgreSQL 获取正式 `document_id` 集合（单项目最多 100 个），再以服务器注入的
`user_id + project_id + kb_id + document_id` 过滤 Chroma。内部固定取前 5 个候选，并应用
冻结后的 `min_relevance_score`；过滤后为空即返回 `REFUSED_NO_EVIDENCE`，不调用 DeepSeek。
否则模型只能使用检索 Chunk 的原文回答，每条引用必须返回文件名、定位和摘录。

问题长度为 `1..2000` 个字符；模型关闭、未配置或最终连接失败
返回 `503 ARCHIVE_ANSWER_UNAVAILABLE`，目录、结构化筛选、清单与重复检查不受影响。

## 9. 删除与审计（FR-040、FR-041）

### `DELETE /projects/{project_id}/documents/{document_id}`

删除可从任一稳定用户可见文档状态发起。服务先创建内部
`DELETE + RUNNING + visibility_blocking = true`，使档案立即退出正式目录、检索和问答，
随后依次清理 Final Chunk、原文件、快照、字段、证据和清单关联。

- 全部成功：返回 `204 No Content`，保留脱敏删除审计。
- 解析、建议、确认或另一删除操作运行中：返回 `409 DOCUMENT_OPERATION_IN_PROGRESS`。
- 外部清理失败：返回 `503 DOCUMENT_DELETE_INCOMPLETE`，文档保持正式范围外；再次调用
  同一 `DELETE` 恢复未完成删除，不创建重复文件、记录、向量或成功审计。
- 越权或不同项目文档：返回 `404` 或 `403`，不得泄露文件名、状态或哈希。

### `GET /projects/{project_id}/audit-logs`

只返回当前用户项目的审计，默认 `created_at DESC, id DESC`，支持公共分页和可选
`operation_type` 筛选。响应不包含原文、完整字段、模型提示词、模型输入输出、内部操作
堆栈或文件路径。

```json
{
  "items": [
    {
      "id": "uuid",
      "operation_type": "ARCHIVE_CONFIRMED",
      "actor_id": "uuid",
      "resource_type": "DOCUMENT",
      "resource_id": "uuid",
      "redacted_summary": {"status": "CONFIRMED"},
      "created_at": "2026-08-06T09:30:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

为保持 Swagger 筛选稳定，审计 API 固定接受：

```text
ARCHIVE_CONFIRMED
ARCHIVE_CONFIRMATION_CANCELLED
ARCHIVE_FIELD_UPDATED
CHECKLIST_ITEM_CREATED
CHECKLIST_ITEM_UPDATED
CHECKLIST_ITEM_DELETED
CHECKLIST_LINK_CONFIRMED
CHECKLIST_LINK_DELETED
PARSE_RETRIED
SUGGESTION_RETRIED
SUGGESTION_REGENERATED
DOCUMENT_DELETED
```

这些是业务审计类型，不等同于 `archive_operations.operation_type` 的内部
`PARSE/SUGGEST/INDEX/DELETE`。数据库当前以受控字符串保存审计类型；实现时必须由
Pydantic Enum 约束上述 API 值，不能接受自由字符串。

## 10. 稳定业务错误码

| HTTP | 错误码 | 触发条件 |
|---|---|---|
| 401 | `AUTHENTICATION_REQUIRED` | 缺失或格式错误的 Bearer Token |
| 401 | `INVALID_CREDENTIALS` | 用户名不存在、密码错误或旧用户无凭据 |
| 401 | `TOKEN_EXPIRED` | Access 或 Refresh Token 已过期 |
| 401 | `INVALID_TOKEN` | Token 伪造、声明/用途错误、会话不存在、已撤销或 Refresh Token 摘要不匹配 |
| 409 | `USERNAME_CONFLICT` | 规范化后的用户名已存在 |
| 503 | `AUTH_NOT_CONFIGURED` | 未配置有效 JWT 签名密钥，认证服务拒绝签发或验证 Token |
| 403 | `PROJECT_FORBIDDEN` | 当前用户无项目所有权 |
| 404 | `PROJECT_NOT_FOUND` | 项目不存在 |
| 404 | `DOCUMENT_NOT_FOUND` | 文档不存在或不属于项目 |
| 404 | `ARCHIVE_NOT_FORMAL` | 请求正式详情但档案未正式确认 |
| 404 | `CHECKLIST_ITEM_NOT_FOUND` / `CHECKLIST_LINK_NOT_FOUND` | 目标资源不存在或不属于项目 |
| 409 | `PROJECT_NAME_CONFLICT` | 同一用户项目名重复 |
| 409 | `PROJECT_HAS_DOCUMENTS` | 非空项目请求删除 |
| 409 | `PROJECT_DOCUMENT_LIMIT_REACHED` | 项目已有 100 份未删除文档 |
| 409 | `DUPLICATE_FILE` | 同项目 `file_hash` 重复 |
| 409 | `VERSION_CONFLICT` | 版本陈旧 |
| 409 | `DOCUMENT_OPERATION_IN_PROGRESS` | 文档已有运行中内部操作 |
| 409 | `PARSE_NOT_ALLOWED` / `SUGGESTION_NOT_ALLOWED` | 当前状态不允许该动作 |
| 409 | `SUGGESTION_WOULD_OVERWRITE_MANUAL_DRAFT` | 重新生成建议将覆盖人工草稿 |
| 409 | `CONFIRM_NOT_ALLOWED` / `CHECKLIST_LINK_NOT_ALLOWED` | 状态或前置条件不满足 |
| 413 | `FILE_TOO_LARGE` | 文件超过 20 MB |
| 415 | `FILE_TYPE_UNSUPPORTED` | 扩展名不在允许范围 |
| 422 | `VALIDATION_ERROR` | JSON、路径、枚举或字段值校验失败 |
| 422 | `PARSE_TEXT_UNAVAILABLE` | 支持格式但未提取到有效文本 |
| 422 | `SCANNED_PDF_UNSUPPORTED` | 扫描 PDF 无可提取文本 |
| 422 | `PARSE_TEXT_TOO_LARGE` | 归一化可提取文本超过 1,000,000 字符快照上限 |
| 422 | `AI_FIELD_EVIDENCE_REQUIRED` | AI 非空字段缺少有效证据 |
| 422 | `SUGGESTION_INVALID_OUTPUT` | 模型输出无法解析为固定字段和证据结构，不自动重试 |
| 503 | `ARCHIVE_SUGGESTION_UNAVAILABLE` | DeepSeek 建议不可用 |
| 503 | `ARCHIVE_ANSWER_UNAVAILABLE` | DeepSeek 问答不可用 |
| 503 | `PARSER_UNAVAILABLE` / `VECTOR_UNAVAILABLE` | 外部解析、Embedding 或 Chroma 依赖不可用 |
| 503 | `DOCUMENT_DELETE_INCOMPLETE` | 跨存储删除未完全完成 |
| 500 | `INTERNAL_ERROR` | 未知异常，隐藏内部细节 |

其中 `SCANNED_PDF_UNSUPPORTED` 是 PDF 无可提取文字时的具体原因；有少量可提取文字但
未达到有效文本阈值时返回 `PARSE_TEXT_UNAVAILABLE`。实现时响应只返回一个稳定代码，
不得把短文本 PDF 误报为扫描件。阈值与快照上限见 `docs/archive-v1-parser-design.md`。

## 11. API 验收映射

| 需求 | 必须覆盖的 API 验收 |
|---|---|
| FR-030 | 同用户项目重名 409、非空项目删除 409、跨用户项目不可访问 |
| FR-031 | 模板复制五项、清单字段版本冲突、必需/可选缺失状态 |
| FR-032 | 20 MB、100 份、同项目哈希冲突、跨项目同哈希不泄露 |
| FR-033 | 文本 PDF 成功、扫描 PDF 失败、普通 parse 不重复执行 |
| FR-034/035 | 建议有证据、模型关闭可手工草稿、空白草稿可重新生成、人工草稿重新生成返回 409、七字段检查 |
| FR-036 | 确认后进入目录、修改字段退出正式范围、重复确认不重复审计 |
| FR-037 | 人工确认关联才满足、关联失效与删除重算、跨项目关联拒绝 |
| FR-038 | 处理列表包含待确认，正式目录不包含；目录筛选与日期空值筛选正确 |
| FR-039 | 正确证据、无依据拒答、待确认与跨项目资料均不可引用 |
| FR-040 | 删除清理、失败保守隐藏与恢复、越权删除不泄露 |
| FR-041 | 仅本人项目审计、时间倒序分页、自动重试不重复成功审计 |

## 12. 验证门槛与下一步

接口契约已确认。Parser 定位、有效文本与快照规则已由 AV1-P01 冻结，见
`docs/archive-v1-parser-design.md`；BGE/Chroma 行为、`min_relevance_score` 与跨存储恢复
仍按 `docs/review/verification-freeze-checklist.md` 分阶段验证后冻结。

1. **[DONE – FR-030]** 已创建项目 CRUD 的 Pydantic Schema、Router、Service、稳定错误码
   与 API 测试。项目创建会创建内部知识库范围，并可在同一事务复制五项虚构清单；空项目
   删除只清理项目级数据，保留内部知识库记录以保护旧 RAG/Agent 数据。
2. **[DONE – FR-031]** 已创建清单项 Schema、Router、Service 与 API 测试；`POST` 使用项目版本
   原子保护创建并写脱敏审计，`PATCH` 使用清单项版本，关键匹配字段变更会使确认关联失效；列表
   仅以同项目的确认档案和确认关联派生 `SATISFIED`、`MISSING`、`NOT_PROVIDED`。
3. **[DONE – FR-032]** 已创建项目内 multipart 上传接口；文件类型/20 MiB 在落盘前预检，
   同项目按 SHA-256 去重，项目行锁原子维护 100 份容量，不同项目相同哈希保持隔离。
4. **[DONE – FR-035/P07]** 已创建人工草稿、草稿读取和字段更新接口；服务生成七字段空白
   草稿，证据绑定当前快照，人工更新强制标记 `MANUAL`，以版本号保护并在已确认文档修改后
   转为 `PENDING_RECONFIRMATION`。

实施计划已生成，AV1-P01～P03、P04 前置模型分层、P04.1 FR-030、P04.2 FR-031 与 P05 FR-032 已完成。
**AV1-P07 手工草稿、字段证据与人工检查已完成**；已验证七字段空白草稿、当前快照证据、人工来源、检查状态、版本冲突和重新确认入口。下一步为 AV1-P08 AI 建议；不得提前实现正式归档、检索或问答流程。
