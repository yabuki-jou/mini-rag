"""统一导出 FastAPI 依赖，保持路由层的导入入口稳定。"""

from app.dependencies.archive_agent import (
    ArchiveAgentSessionDep,
    get_archive_agent_session,
)
from app.dependencies.auth import (
    CurrentAuthenticationDep,
    CurrentUserDep,
    get_current_authentication,
    get_current_user,
)
from app.dependencies.database import SessionDep
from app.dependencies.project_context import (
    ProjectContext,
    ProjectContextDep,
    ProjectDocumentDep,
    get_project_context,
    get_project_document,
)


__all__ = [
    "ArchiveAgentSessionDep",
    "CurrentAuthenticationDep",
    "CurrentUserDep",
    "ProjectContext",
    "ProjectContextDep",
    "ProjectDocumentDep",
    "SessionDep",
    "get_current_authentication",
    "get_current_user",
    "get_archive_agent_session",
    "get_project_context",
    "get_project_document",
]
