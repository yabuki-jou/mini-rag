# services 重构后企业评测来源路径修复实施计划

## 问题与证据

`scripts/generate_enterprise_rag_eval_data.py` 生成的项目级 `source_files`
元数据仍指向已在 services 分层重构中删除的根级模块：
`archive_retrieval_service.py`、`archive_question_service.py`、
`archive_parser_service.py` 和 `archive_index_service.py`。

现有测试只校验 `documents[].source_files`，没有校验
`projects[].source_files`，因此未能发现该不一致。

## 实现边界

1. 先扩展现有企业评测资料测试，要求项目级和文档级声明的每个来源文件都在当前仓库存在，并实际运行取得 RED。
2. 只将四个旧路径替换为 `app/services/archive/retrieval.py`、
   `app/services/archive/questions.py`、`app/services/archive/parser.py` 和
   `app/services/archive/indexing.py`。
3. 不修改评测语料内容、Ground Truth、问题分类、PDF 生成、生产 RAG 或对外 API。
4. 不恢复旧根级 service 兼容模块。

## TDD 与验证

1. RED：运行扩展后的
   `tests/scripts/test_generate_enterprise_rag_eval_data.py`，确认因四个项目级旧路径不存在而失败。
2. GREEN：更新四个路径后重跑该测试。
3. 主 Agent 审查差异，再运行 services 包边界测试、完整后端测试、`compileall` 和 `git diff --check`。
