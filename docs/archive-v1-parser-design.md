# 智慧档案 V1 Parser 定位与快照契约

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 对应实施任务 | AV1-P01（FR-033、NFR-016） |
| 状态 | Parser 规则已冻结，并已由 AV1-P06 接入归档数据库、项目路由和状态机 |
| Parser 版本 | `archive-v1-parser-v1` |
| 更新日期 | 2026-08-06 |

本文记录 P01 实测后冻结的解析契约，并作为 AV1-P06 正式解析实现的规则基线。当前
`PARSED`、`PARSE_FAILED`、`ParsedSnapshot` 表和项目解析/重试 API 已实现；P09～P13 的确认、索引、
目录、检索、问答和删除切片已接入后续流程，固定问题集质量和完整端到端验收仍由 P14 负责。

## 2. 统一结果与快照

解析器输出有序 `ParsedFragment` 列表，每个片段包含：

- `location_type`：`PDF_PAGE`、`DOCX_PARAGRAPH` 或 `TEXT_LINE_RANGE`；
- `location_start`、`location_end`：均从 1 开始；
- `content`：按本文件空白规则归一化后的正文；
- `anchor_text`：仅 DOCX 使用，为 `content` 的前 50 个 Python 字符。

`snapshot_hash` 是按片段顺序对 `location_type`、位置起止值、`content` 和
`anchor_text` 进行固定键排序 JSON 序列化后，以 UTF-8 编码计算的 SHA-256。它不包含
`parser_version`；解析器版本作为独立元数据保存，因此内容未变但规则升级时可以区分版本，
而不会把版本字符串误判为原文变化。

## 3. 冻结规则

### 3.1 通用规则

- 仅接受 `.pdf`、`.docx`、`.txt`、`.md`。
- TXT/MD 使用 `utf-8-sig` 解码；不接受其他编码猜测，解码失败返回
  `PARSE_TEXT_UNAVAILABLE`。
- 所有 `CRLF` 与 `CR` 在处理前统一为 `LF`；任意连续 Unicode 空白在片段内容中压缩为
  一个半角空格，再去除首尾空格。
- 有效文本长度为全部片段归一化 `content` 的字符数之和；最小值为 **20**，最大值为
  **1,000,000**。超过上限返回 `PARSE_TEXT_TOO_LARGE`，不截断后继续归档。

### 3.2 PDF

- 每个包含归一化后非空文本的原始页生成一个 `PDF_PAGE` 片段，位置为该页从 1 开始的页码。
- PDF 完全没有可提取文本时返回 `SCANNED_PDF_UNSUPPORTED`，消息为
  “暂不支持扫描件，请上传文本版资料”。
- PDF 有可提取文字但总长度不足 20 字符时返回 `PARSE_TEXT_UNAVAILABLE`，不误报为扫描件。

### 3.3 DOCX

- 从文档正文 XML 的出现顺序处理顶层段落与表格；空段落、空表格行不编号。
- 每个非空段落或列表项产生一个 `DOCX_PARAGRAPH` 片段。列表的正文被保留，但不另行合成
  Word 自动编号文字。
- 每个非空表格行产生一个 `DOCX_PARAGRAPH` 片段；同一底层单元格在横向合并后只保留一次，
  其余单元格按从左到右以 ` | ` 连接。
- 段落序号是“非空逻辑块序号”，而不是外部 Word 编辑器显示的段落/页码；它只承诺在同一
  原文件和本契约规则下可重复生成。

### 3.4 TXT 与 MD

- 每个非空原始行产生一个 `TEXT_LINE_RANGE` 片段，位置起止值等于原始行号。虽然当前
  片段起止值相同，枚举使用范围语义以兼容后续连续多行片段。
- 空行不产生片段，但始终参与原始行号计数；因此换行归一化后，同一原文件的行号稳定。

## 4. 受控失败分类

| 条件 | 稳定错误码 | 说明 |
|---|---|---|
| 不支持扩展名 | `FILE_TYPE_UNSUPPORTED` | 上传层最终映射为 415 |
| TXT/MD 非 UTF-8 或解析器读取失败 | `PARSE_TEXT_UNAVAILABLE` | 不进行编码猜测 |
| PDF 无可提取文字 | `SCANNED_PDF_UNSUPPORTED` | 不标记为解析成功 |
| 其他格式总有效文本少于 20 字符 | `PARSE_TEXT_UNAVAILABLE` | 过滤空壳文档 |
| 归一化文本超过 1,000,000 字符 | `PARSE_TEXT_TOO_LARGE` | 不截断保存快照 |

## 5. 验证证据

验证代码位于 `tests/services/test_archive_parser_service.py`，运行时动态生成虚构 TXT/MD、DOCX、
文本型 PDF 和空白 PDF，不读取真实资料。

执行命令：

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://parser_test:parser_test@localhost:5432/parser_test'
C:\D\venvs\mrh\Scripts\python.exe -m pytest -q tests\services\test_archive_parser_service.py tests\services\test_document_service.py
C:\D\venvs\mrh\Scripts\python.exe -m compileall -q app tests
```

实际结果：`11 passed in 8.75s`；`compileall` 退出成功。覆盖了 PDF 页码、DOCX 段落/表格、
TXT/MD 换行与行号、空白 PDF、短文本 PDF、有效文本上下限和同一原文件的快照哈希重复性。

补充环境验证：P01 验证时，安装 `psycopg[binary]==3.3.4` 后，以当前进程的 PostgreSQL
格式 `DATABASE_URL` 运行全量测试，结果为 `76 passed, 1 skipped, 16 warnings in 12.97s`。
P05 项目内上传完成时的历史全量回归为 `142 passed, 2 skipped, 16 warnings`；当前 P07 完成后的默认全量回归为 `164 passed, 2 skipped, 16 warnings`。两个跳过项分别需要显式 PostgreSQL 并发授权和专用空迁移库，警告属于既有测试环境，不是 Parser 验证失败。

## 6. 已知边界与后续任务

- AV1-P02 已完成 12～18 份虚构验收资料、人工字段/证据 Ground Truth 和固定问答用例；
  它为后续检索/问答验收提供输入，不代表模型或检索质量已经达标。
- 本验证未连接 PostgreSQL、文件持久化目录、Milvus 或 DeepSeek；它不能证明归档状态机、
  向量检索或问答可用。
- AV1-P06 已将本契约接入 `UPLOADED → PARSED/PARSE_FAILED`、不可变快照与解析重试；后续改动必须提升 Parser 版本并重新验证。
- 若未来需要支持其他编码、DOCX 嵌套结构或 OCR，必须新开需求和 Parser 版本，不能静默改变
  `archive-v1-parser-v1` 的定位语义。
