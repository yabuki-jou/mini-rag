"""提供 FR-030 项目管理和 FR-031 清单读取 HTTP 接口。"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.dependencies import (
    CurrentUserDep,
    ProjectContextDep,
    ProjectDocumentDep,
    SessionDep,
)
from app.core.config import settings
from app.core.errors import AppError
from app.schemas import (
    ArchiveDetailRead,
    ArchiveAuditOperationType,
    ArchivePageRead,
    ArchiveRetrievalRequest,
    ArchiveRetrievalResponse,
    ArchiveRetrievalDiagnosticRequest,
    ArchiveRetrievalDiagnosticResponse,
    ArchiveQuestionRequest,
    ArchiveQuestionResponse,
    AuditLogPageRead,
    ChecklistItemCreate,
    ChecklistItemCreateResponse,
    ChecklistItemListRead,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ChecklistLinkCreate,
    ChecklistLinkListRead,
    ChecklistLinkRead,
    ChecklistLinkSuggestionListRead,
    ArchiveConfirmationRequest,
    ArchiveDraftRead,
    ArchiveSuggestionRegenerateRequest,
    ArchiveFieldUpdate,
    ProjectCreate,
    ProcessDocumentRead,
    ProcessDocumentPageRead,
    ProjectPageRead,
    ProjectRead,
    ProjectUpdate,
)
from app.services.checklist_service import (
    create_checklist_item,
    delete_checklist_item,
    list_checklist_items,
    update_checklist_item,
)
from app.services.project_service import (
    create_project,
    delete_empty_project,
    list_projects,
    read_project,
    update_project,
)
from app.services.document_service import (
    create_project_uploaded_document,
    parse_project_document,
    retry_parse_project_document,
)
from app.models import (
    ArchiveDocumentStatus,
    ArchiveDocumentType,
    ArchiveFieldName,
    ProjectStage,
)
from app.services.archive_draft_service import (
    create_manual_draft,
    read_document_draft,
    update_field,
)
from app.services.archive_suggestion_service import (
    create_suggestions,
    regenerate_suggestions,
    retry_suggestions,
)
from app.services.archive_confirmation_service import confirm_document
from app.services.archive_cancel_confirmation_service import cancel_confirmation
from app.services.archive_catalog_service import (
    list_audit_logs,
    list_formal_archives,
    list_process_documents,
    read_formal_archive,
)
from app.services.archive_checklist_service import (
    create_document_link,
    delete_document_link,
    list_document_links,
    list_link_suggestions,
)
from app.services.archive_retrieval_service import (
    retrieve_archive_chunks,
    retrieve_archive_diagnostics,
)
from app.services.archive_question_service import answer_archive_question
from app.services.archive_document_delete_service import delete_archive_document


router = APIRouter(prefix="/projects", tags=["projects"])

# 注释 1：该 Router 保持薄层；依赖负责建立身份和项目归属，Service 负责事务、
# 状态规则和 I/O。

@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project_endpoint(
    payload: ProjectCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ProjectRead:
    """创建当前用户的项目和服务端管理的独立知识库范围。"""
    return create_project(current_user=current_user, payload=payload, session=session)


@router.get("", response_model=ProjectPageRead)
def list_projects_endpoint(
    current_user: CurrentUserDep,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ProjectPageRead:
    """按稳定分页顺序列出当前用户项目。"""
    return list_projects(
        current_user=current_user,
        page=page,
        page_size=page_size,
        session=session,
    )


@router.post(
    "/{project_id}/documents",
    response_model=ProcessDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_document_endpoint(
    file: Annotated[UploadFile, File(description="PDF、DOCX、TXT 或 Markdown 原文件")],
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ProcessDocumentRead:
    """上传项目内原文件；本阶段不自动解析、调用模型或写 Chroma。"""
    return await create_project_uploaded_document(
        upload=file,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        session=session,
    )


@router.delete(
    "/{project_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_document_endpoint(
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Response:
    """物理删除项目文档及其档案、文件、向量和关联。"""
    # 注释 2：ProjectDocumentDep 在删除前同时解析所有权和项目成员关系，
    # 因而客户端不能选择其他项目的文档。
    delete_archive_document(
        document=document,
        actor_id=current_user.id,
        session=session,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/documents", response_model=ProcessDocumentPageRead)
def list_project_documents_endpoint(
    project_context: ProjectContextDep,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: ArchiveDocumentStatus | None = None,
) -> ProcessDocumentPageRead:
    """分页读取项目文档处理状态，包含未确认和失败文档。"""
    return list_process_documents(
        project_id=project_context.project_id,
        page=page,
        page_size=page_size,
        status=status,
        session=session,
    )


@router.get("/{project_id}/archives", response_model=ArchivePageRead)
def list_project_archives_endpoint(
    project_context: ProjectContextDep,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_type: ArchiveDocumentType | None = None,
    project_stage: ProjectStage | None = None,
    document_date_from: date | None = None,
    document_date_to: date | None = None,
    document_date_is_null: bool = False,
    authoring_organization: str | None = None,
) -> ArchivePageRead:
    """分页读取没有删除阻断的正式档案目录。"""
    # 注释 3：由 Service 而非本路由应用 CONFIRMED 与可见性阻断规则，
    # 使所有目录调用方共用同一闸门。
    return list_formal_archives(
        project_id=project_context.project_id,
        page=page,
        page_size=page_size,
        document_type=document_type,
        project_stage=project_stage,
        document_date_from=document_date_from,
        document_date_to=document_date_to,
        document_date_is_null=document_date_is_null,
        authoring_organization=authoring_organization,
        session=session,
    )


@router.get("/{project_id}/archives/{document_id}", response_model=ArchiveDetailRead)
def get_project_archive_endpoint(
    document: ProjectDocumentDep,
    session: SessionDep,
) -> ArchiveDetailRead:
    """读取正式档案详情和七个字段证据；非正式档案统一隐藏。"""
    return read_formal_archive(document=document, session=session)


@router.get("/{project_id}/audit-logs", response_model=AuditLogPageRead)
def list_project_audit_logs_endpoint(
    project_context: ProjectContextDep,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    operation_type: ArchiveAuditOperationType | None = None,
) -> AuditLogPageRead:
    """分页读取当前项目的脱敏业务审计记录。"""
    return list_audit_logs(
        project_id=project_context.project_id,
        page=page,
        page_size=page_size,
        operation_type=operation_type,
        session=session,
    )


@router.post(
    "/{project_id}/archive-retrieval",
    response_model=ArchiveRetrievalResponse,
)
def retrieve_project_archive_endpoint(
    payload: ArchiveRetrievalRequest,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ArchiveRetrievalResponse:
    """仅在当前项目正式档案范围内检索可追溯原文证据。"""
    # 注释 4：范围值来自已验证依赖而非请求体，使向量检索始终与 Bearer 授权一致。
    return retrieve_archive_chunks(
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        query=payload.query,
        top_k=payload.top_k,
        session=session,
    )


@router.post(
    "/{project_id}/archive-retrieval-diagnostic",
    response_model=ArchiveRetrievalDiagnosticResponse,
    include_in_schema=False,
)
def retrieve_project_archive_diagnostic_endpoint(
    payload: ArchiveRetrievalDiagnosticRequest,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ArchiveRetrievalDiagnosticResponse:
    """仅在开发环境返回固定集所需的 Top-20 双排序脱敏诊断。"""
    if settings.app_env != "development":
        raise AppError(404, "NOT_FOUND", "资源不存在。")
    return retrieve_archive_diagnostics(
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        query=payload.query,
        expected_evidence=payload.expected_evidence,
        session=session,
    )


@router.post(
    "/{project_id}/archive-questions",
    response_model=ArchiveQuestionResponse,
)
def ask_project_archive_question_endpoint(
    payload: ArchiveQuestionRequest,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ArchiveQuestionResponse:
    """基于当前项目正式证据生成带引用回答；无依据时直接拒答。"""
    # 注释 5：问题文本是唯一面向模型的客户端输入；身份和知识库范围始终由服务端控制。
    return answer_archive_question(
        user_id=project_context.user_id,
        project_id=project_context.project_id,
        kb_id=project_context.kb_id,
        question=payload.question,
        session=session,
    )


@router.post(
    "/{project_id}/documents/{document_id}/parse",
    response_model=ProcessDocumentRead,
)
def parse_project_document_endpoint(
    document: ProjectDocumentDep,
    session: SessionDep,
) -> ProcessDocumentRead:
    """解析项目文档并保存位置感知快照，不生成字段草稿或向量。"""
    return parse_project_document(document=document, session=session)


@router.post(
    "/{project_id}/documents/{document_id}/parse-retry",
    response_model=ProcessDocumentRead,
)
def retry_parse_project_document_endpoint(
    document: ProjectDocumentDep,
    session: SessionDep,
) -> ProcessDocumentRead:
    """仅对 PARSE_FAILED 文档重试解析，不重新上传原文件。"""
    return retry_parse_project_document(document=document, session=session)


@router.post(
    "/{project_id}/documents/{document_id}/manual-draft",
    response_model=ArchiveDraftRead,
)
def create_manual_draft_endpoint(
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ArchiveDraftRead:
    """为已解析项目文档创建七字段空白人工草稿。"""
    # 注释 6：创建草稿需要已认证操作者，因为 Service 会记录人工归属的状态转换，
    # 与纯读取路由不同。
    return create_manual_draft(
        document=document,
        actor_id=current_user.id,
        session=session,
    )


@router.get(
    "/{project_id}/documents/{document_id}/draft",
    response_model=ArchiveDraftRead,
)
def read_document_draft_endpoint(
    document: ProjectDocumentDep,
    session: SessionDep,
) -> ArchiveDraftRead:
    """读取项目文档的人工字段草稿、快照元数据和下一步动作。"""
    return read_document_draft(document=document, session=session)


@router.put(
    "/{project_id}/documents/{document_id}/fields/{field_name}",
    response_model=ArchiveDraftRead,
)
def update_document_field_endpoint(
    field_name: ArchiveFieldName,
    payload: ArchiveFieldUpdate,
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ArchiveDraftRead:
    """保存单个归档字段、人工检查状态和当前快照证据。"""
    # 注释 7：请求体只携带字段内容；文档归属和字段名分别由可信路径依赖与枚举校验确定。
    return update_field(
        document=document,
        actor_id=current_user.id,
        field_name=field_name,
        payload=payload,
        session=session,
    )


@router.post(
    "/{project_id}/documents/{document_id}/suggestions",
    response_model=ArchiveDraftRead,
)
def create_document_suggestions_endpoint(
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ArchiveDraftRead:
    """基于当前解析快照生成首次 AI 字段建议。"""
    # 注释 8：Service 自行读取当前快照，避免客户端提交任意文本或过期快照标识。
    return create_suggestions(
        document_id=document.id,
        actor_id=current_user.id,
        session=session,
    )


@router.post(
    "/{project_id}/documents/{document_id}/suggestions/retry",
    response_model=ArchiveDraftRead,
)
def retry_document_suggestions_endpoint(
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ArchiveDraftRead:
    """仅对建议失败文档重试 AI 字段建议。"""
    return retry_suggestions(
        document_id=document.id,
        actor_id=current_user.id,
        session=session,
    )


@router.post(
    "/{project_id}/documents/{document_id}/suggestions/regenerate",
    response_model=ArchiveDraftRead,
)
def regenerate_document_suggestions_endpoint(
    payload: ArchiveSuggestionRegenerateRequest,
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ArchiveDraftRead:
    """仅在无人工编辑且版本匹配时安全重新生成 AI 建议。"""
    return regenerate_suggestions(
        document_id=document.id,
        actor_id=current_user.id,
        expected_version=payload.expected_version,
        session=session,
    )


@router.post(
    "/{project_id}/documents/{document_id}/confirm",
    response_model=ProcessDocumentRead,
)
def confirm_project_document_endpoint(
    payload: ArchiveConfirmationRequest,
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ProcessDocumentRead:
    """校验人工确认前置条件、转为 CONFIRMED 并触发内部 INDEX。"""
    # 注释 9：确认属于业务状态转换，因此透传 expected_version 以保护人工检查
    # 不受并发修改影响。
    return confirm_document(
        document=document,
        actor_id=current_user.id,
        payload=payload,
        session=session,
    )


@router.post(
    "/{project_id}/documents/{document_id}/cancel-confirmation",
    response_model=ProcessDocumentRead,
)
def cancel_project_document_confirmation_endpoint(
    payload: ArchiveConfirmationRequest,
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ProcessDocumentRead:
    """取消确认、退出正式范围并清理 Final Chunk。"""
    # 注释 10：取消确认会立即将文档移出正式范围；随后向量清理遵循 Service 中的
    # 保守恢复规则。
    return cancel_confirmation(
        document=document,
        actor_id=current_user.id,
        payload=payload,
        session=session,
    )


@router.get(
    "/{project_id}/documents/{document_id}/checklist-link-suggestions",
    response_model=ChecklistLinkSuggestionListRead,
)
def list_document_checklist_link_suggestions_endpoint(
    document: ProjectDocumentDep,
    session: SessionDep,
) -> ChecklistLinkSuggestionListRead:
    """按人工确认的类型和阶段返回清单关联建议。"""
    return list_link_suggestions(document=document, session=session)


@router.get(
    "/{project_id}/documents/{document_id}/checklist-links",
    response_model=ChecklistLinkListRead,
)
def list_document_checklist_links_endpoint(
    document: ProjectDocumentDep,
    session: SessionDep,
) -> ChecklistLinkListRead:
    """读取档案已有的确认或失效清单关联。"""
    return list_document_links(document=document, session=session)


@router.post(
    "/{project_id}/documents/{document_id}/checklist-links",
    response_model=ChecklistLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def create_document_checklist_link_endpoint(
    payload: ChecklistLinkCreate,
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ChecklistLinkRead:
    """以文档和清单项版本号为前提人工确认档案关联。"""
    # 注释 11：Service 会检查两类资源版本，因为关联会改变派生清单状态，
    # 不能与文档更新竞争。
    return create_document_link(
        document=document,
        actor_id=current_user.id,
        payload=payload,
        session=session,
    )


@router.delete(
    "/{project_id}/documents/{document_id}/checklist-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document_checklist_link_endpoint(
    link_id: UUID,
    document: ProjectDocumentDep,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Response:
    """删除档案清单关联并写入脱敏审计。"""
    delete_document_link(
        document=document,
        link_id=link_id,
        actor_id=current_user.id,
        session=session,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/checklist-items", response_model=ChecklistItemListRead)
def list_checklist_items_endpoint(
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ChecklistItemListRead:
    """读取当前用户项目的清单及实时派生状态。"""
    return list_checklist_items(project_id=project_context.project_id, session=session)


@router.post(
    "/{project_id}/checklist-items",
    response_model=ChecklistItemCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_checklist_item_endpoint(
    payload: ChecklistItemCreate,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ChecklistItemCreateResponse:
    """创建当前用户项目独有的清单项，并以项目版本保护该写入。"""
    return create_checklist_item(
        project_id=project_context.project_id,
        actor_id=project_context.user_id,
        payload=payload,
        session=session,
    )


@router.patch(
    "/{project_id}/checklist-items/{item_id}",
    response_model=ChecklistItemRead,
)
def update_checklist_item_endpoint(
    item_id: UUID,
    payload: ChecklistItemUpdate,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> ChecklistItemRead:
    """以清单项版本为前提修改项目独有清单项。"""
    return update_checklist_item(
        project_id=project_context.project_id,
        actor_id=project_context.user_id,
        item_id=item_id,
        payload=payload,
        session=session,
    )


@router.delete(
    "/{project_id}/checklist-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_checklist_item_endpoint(
    item_id: UUID,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> Response:
    """删除项目清单项及其关联，并保留脱敏审计。"""
    # 注释 12：该接口按契约不返回正文；所有派生状态处理都在响应发送前由 Service
    # 在事务内完成。
    delete_checklist_item(
        project_id=project_context.project_id,
        actor_id=project_context.user_id,
        item_id=item_id,
        session=session,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project_endpoint(
    project_id: UUID,
    _: ProjectContextDep,
    session: SessionDep,
) -> ProjectRead:
    """读取已通过项目所有权校验的项目详情。"""
    return read_project(project_id=project_id, session=session)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project_endpoint(
    project_id: UUID,
    payload: ProjectUpdate,
    _: ProjectContextDep,
    session: SessionDep,
) -> ProjectRead:
    """以客户端版本号为前提修改项目名称或说明。"""
    return update_project(project_id=project_id, payload=payload, session=session)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_endpoint(
    project_id: UUID,
    _: ProjectContextDep,
    session: SessionDep,
) -> Response:
    """删除无文档项目及项目级清单，保留其内部知识库记录。"""
    delete_empty_project(project_id=project_id, session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
