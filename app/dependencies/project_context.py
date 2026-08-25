"""为智慧档案项目请求建立不可由客户端替换的授权范围。"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlmodel import select

from app.core.errors import AppError
from app.dependencies.auth import CurrentUserDep
from app.dependencies.database import SessionDep
from app.models import Document, Project


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """携带服务端已验证的用户、项目和知识库隔离边界。"""

    user_id: UUID
    project_id: UUID
    kb_id: UUID


def get_project_context(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ProjectContext:
    """验证项目归属后，构造后续归档服务唯一可用的范围上下文。"""
    project = session.get(Project, project_id)
    if project is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问。")
    if project.owner_id != current_user.id:
        raise AppError(403, "PROJECT_FORBIDDEN", "无权访问该项目。")
    return ProjectContext(
        user_id=current_user.id,
        project_id=project.id,
        kb_id=project.kb_id,
    )


ProjectContextDep = Annotated[ProjectContext, Depends(get_project_context)]


def get_project_document(
    document_id: UUID,
    project_context: ProjectContextDep,
    session: SessionDep,
) -> Document:
    """在已验证项目和知识库范围内注入项目文档。"""
    document = session.exec(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_context.project_id,
            Document.kb_id == project_context.kb_id,
        )
    ).first()
    if document is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "该文档不存在。")
    return document


ProjectDocumentDep = Annotated[Document, Depends(get_project_document)]
