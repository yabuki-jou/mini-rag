# Eval Criteria

## Use cases

1. 正式档案检索标定（challenging）：以当前项目有据问题检索已确认资料，结果含人工标注的标准证据且排除无关候选。
2. 带证据问答（challenging）：以当前项目有据问题生成简洁回答，所有业务断言都由返回引用中的原文支持。
3. 无依据与隔离问答（challenging）：对无依据或只在其他项目/未确认文档中有依据的问题，拒答且不泄露内容或伪造引用。
4. 跨存储物理删除（challenging）：删除已确认文档时清理文件、向量与业务关联；外部失败时保持不可见并返回稳定失败。

## Eval criteria

| # | Criterion | Applies to | Data to capture |
| --- | --- | --- | --- |
| 1 | 标准证据可检索性：8 个有据问题中至少 7 个最终检索项含人工标注的标准证据。 | Use case 1, 2 | `archive_retrieval_scope`、`archive_retrieval_result` |
| 2 | 正式范围完整性：候选和最终证据均来自当前用户、当前项目的 `CONFIRMED` 文档。 | Use case 1, 2, 3 | `archive_retrieval_scope`、`archive_retrieval_result` |
| 3 | 回答证据忠实性：回答业务事实不超出已检索摘录，不以常识或模型补全代替原文证据。 | Use case 2 | `archive_question_prompt_evidence`、`archive_question_response` |
| 4 | 引用可追溯性：每个回答引用映射到本次返回的文件、定位和摘录；错误、伪造或跨范围引用均失败。 | Use case 2 | `archive_retrieval_result`、`archive_question_response` |
| 5 | 无依据/隔离拒答：2 个无依据问题均为 `REFUSED_NO_EVIDENCE` 且引用为空；2 个隔离问题不返回或暗示其他项目内容。 | Use case 3 | `archive_question_decision`、`archive_question_response` |
| 6 | 删除失败保守性：Chroma 或文件删除失败时不报告全部成功，文档保持在正式检索与问答范围之外。 | Use case 4 | `archive_delete_external_result`、`archive_delete_outcome` |

## Capability coverage

覆盖：受控检索、带证据问答、物理删除与审计。

未覆盖：项目/清单 CRUD、上传解析、手工草稿和 AI 字段建议继续由确定性单元、服务和 API
测试覆盖。本轮真实质量评测优先覆盖尚未验收的检索阈值、DeepSeek 问答和跨存储恢复。
