"""统一导出 FastAPI 依赖，保持路由层的导入入口稳定。"""

from app.dependencies.agent import AdminAgentRuntimeDep, get_admin_agent_runtime
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
from app.dependencies.resources import (
    OwnedAgentSessionDep,
    OwnedChatSessionDep,
    OwnedDocumentDep,
    OwnedKnowledgeBaseDep,
    get_document_in_knowledge_base,
    get_owned_chat_session,
    get_owned_knowledge_base,
)


__all__ = [
    "AdminAgentRuntimeDep",
    "ArchiveAgentSessionDep",
    "CurrentAuthenticationDep",
    "CurrentUserDep",
    "OwnedAgentSessionDep",
    "OwnedChatSessionDep",
    "OwnedDocumentDep",
    "OwnedKnowledgeBaseDep",
    "ProjectContext",
    "ProjectContextDep",
    "ProjectDocumentDep",
    "SessionDep",
    "get_current_authentication",
    "get_current_user",
    "get_admin_agent_runtime",
    "get_archive_agent_session",
    "get_document_in_knowledge_base",
    "get_owned_chat_session",
    "get_owned_knowledge_base",
    "get_project_context",
    "get_project_document",
]
