"""提供 FR-030 项目管理和 FR-031 清单读取 HTTP 接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.dependencies import (
    CurrentUserDep,
    ProjectContextDep,
    ProjectDocumentDep,
    SessionDep,
)
from app.schemas import (
    ChecklistItemCreate,
    ChecklistItemCreateResponse,
    ChecklistItemListRead,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ProjectCreate,
    ProcessDocumentRead,
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


router = APIRouter(prefix="/projects", tags=["projects"])


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
