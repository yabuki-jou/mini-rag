"""实现 FR-030 的项目生命周期与项目专属知识库范围。"""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models import (
    ArchiveAuditLog,
    ArchiveDocumentType,
    ChecklistItem,
    Document,
    KnowledgeBase,
    Project,
    ProjectStage,
    User,
    utc_now,
)
from app.schemas.project import ProjectCreate, ProjectPageRead, ProjectUpdate


# 这是虚构演示模板，不代表真实工程项目的法定或行业归档要求。
DEMO_CHECKLIST_TEMPLATE: tuple[
    tuple[str, ArchiveDocumentType, bool, ProjectStage, str], ...
] = (
    ("项目合同", ArchiveDocumentType.CONTRACT, True, ProjectStage.PREPARATION, "至少关联 1 份已确认合同资料"),
    ("设计说明", ArchiveDocumentType.DESIGN, True, ProjectStage.DESIGN, "至少关联 1 份已确认设计说明"),
    ("施工方案", ArchiveDocumentType.CONSTRUCTION, True, ProjectStage.CONSTRUCTION, "至少关联 1 份已确认施工方案"),
    ("项目会议纪要", ArchiveDocumentType.MEETING_MINUTES, False, ProjectStage.CROSS_STAGE, "用于演示“未提供”，不计入缺失"),
    ("验收报告", ArchiveDocumentType.ACCEPTANCE, True, ProjectStage.ACCEPTANCE, "至少关联 1 份已确认验收资料"),
)


def create_project(
    *,
    current_user: User,
    payload: ProjectCreate,
    session: Session,
) -> Project:
    """创建项目、其内部知识库范围及可选的独立演示清单。

    Args:
        current_user: 已验证的项目创建人。
        payload: 已完成 HTTP 参数校验的项目创建输入。
        session: 当前请求的数据库事务会话。

    Returns:
        已提交并刷新后的项目记录。

    Raises:
        AppError: 名称冲突或数据库写入失败。
    """
    _ensure_project_name_available(
        session=session,
        owner_id=current_user.id,
        name=payload.name,
    )

    # 先在内存中生成项目 ID，才能为客户端不可见的内部知识库生成稳定、无业务歧义的名称。
    project_id = uuid4()
    knowledge_base = KnowledgeBase(
        owner_id=current_user.id,
        name=f"archive-project-{project_id}",
    )
    project = Project(
        id=project_id,
        owner_id=current_user.id,
        kb_id=knowledge_base.id,
        name=payload.name,
        description=payload.description,
        uses_demo_checklist=payload.use_demo_checklist,
    )
    try:
        # 这些模型没有 ORM relationship 可供工作单元推导对象依赖，因此显式按
        # knowledge_bases → projects → checklist_items 的外键顺序刷新。flush 仍在
        # 同一事务中，后续任一步失败都会由 rollback 一并撤销。
        session.add(knowledge_base)
        session.flush()
        session.add(project)
        session.flush()

        if payload.use_demo_checklist:
            session.add_all(_build_demo_checklist_items(project_id=project.id))

        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_project_name_conflict(exc):
            raise AppError(409, "PROJECT_NAME_CONFLICT", "当前用户已存在同名项目。") from exc
        raise AppError(500, "PROJECT_CREATE_FAILED", "项目创建失败。") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "PROJECT_CREATE_FAILED", "项目创建失败。") from exc

    session.refresh(project)
    return project


def list_projects(
    *,
    current_user: User,
    page: int,
    page_size: int,
    session: Session,
) -> ProjectPageRead:
    """分页列出当前用户拥有的项目，不暴露他人项目。"""
    total = session.exec(
        select(func.count()).select_from(Project).where(Project.owner_id == current_user.id)
    ).one()
    statement = (
        select(Project)
        .where(Project.owner_id == current_user.id)
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    projects = list(session.exec(statement).all())
    return ProjectPageRead(items=projects, page=page, page_size=page_size, total=total)


def read_project(*, project_id: UUID, session: Session) -> Project:
    """读取已由路由所有权依赖验证过的项目。

    并发删除可能发生在依赖校验之后，因此仍须处理项目不存在的情况。
    """
    project = session.get(Project, project_id)
    if project is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问。")
    return project


def update_project(
    *,
    project_id: UUID,
    payload: ProjectUpdate,
    session: Session,
) -> Project:
    """使用乐观锁更新项目名称或说明。"""
    project = read_project(project_id=project_id, session=session)
    _ensure_expected_version(
        actual_version=project.version,
        expected_version=payload.expected_version,
    )

    if "name" in payload.model_fields_set:
        assert payload.name is not None
        _ensure_project_name_available(
            session=session,
            owner_id=project.owner_id,
            name=payload.name,
            excluded_project_id=project.id,
        )
        project.name = payload.name
    if "description" in payload.model_fields_set:
        project.description = payload.description

    project.version += 1
    project.updated_at = utc_now()
    try:
        session.add(project)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_project_name_conflict(exc):
            raise AppError(409, "PROJECT_NAME_CONFLICT", "当前用户已存在同名项目。") from exc
        raise AppError(500, "PROJECT_UPDATE_FAILED", "项目修改失败。") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "PROJECT_UPDATE_FAILED", "项目修改失败。") from exc

    session.refresh(project)
    return project


def delete_empty_project(*, project_id: UUID, session: Session) -> None:
    """删除没有文档的项目及项目级数据，但保留内部知识库记录。

    绑定知识库可能包含旧系统文档；项目删除不能通过清理知识库破坏既有 RAG/Agent 数据。
    """
    project = read_project(project_id=project_id, session=session)
    document_exists = session.exec(
        select(Document.id).where(Document.project_id == project.id).limit(1)
    ).first()
    if document_exists is not None:
        raise AppError(409, "PROJECT_HAS_DOCUMENTS", "项目存在文档记录，不能删除。")

    try:
        # PostgreSQL 由外键级联删除清单和审计。SQLite 测试未启用外键级联时显式删除清单，
        # 使两种验证方言具有相同的项目级删除语义。
        for checklist_item in _read_project_checklist_items(session=session, project_id=project.id):
            session.delete(checklist_item)
        for audit_log in _read_project_audit_logs(session=session, project_id=project.id):
            session.delete(audit_log)
        session.delete(project)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise AppError(500, "PROJECT_DELETE_FAILED", "项目删除失败。") from exc


def _build_demo_checklist_items(*, project_id: UUID) -> list[ChecklistItem]:
    """为新项目复制内置虚构清单，避免多个项目共用可变模板记录。"""
    return [
        ChecklistItem(
            project_id=project_id,
            name=name,
            document_type=document_type,
            is_required=is_required,
            project_stage=project_stage,
            description=description,
        )
        for name, document_type, is_required, project_stage, description in DEMO_CHECKLIST_TEMPLATE
    ]


def _read_project_checklist_items(*, session: Session, project_id: UUID) -> Sequence[ChecklistItem]:
    """读取一个项目的清单项，供删除时显式清理 SQLite 测试数据。"""
    return session.exec(
        select(ChecklistItem).where(ChecklistItem.project_id == project_id)
    ).all()


def _read_project_audit_logs(*, session: Session, project_id: UUID) -> Sequence[ArchiveAuditLog]:
    """读取项目审计，供删除时显式清理 SQLite 测试数据。"""
    return session.exec(
        select(ArchiveAuditLog).where(ArchiveAuditLog.project_id == project_id)
    ).all()


def _ensure_project_name_available(
    *,
    session: Session,
    owner_id: UUID,
    name: str,
    excluded_project_id: UUID | None = None,
) -> None:
    """在应用层提前发现同用户重名，并为数据库唯一约束保留兜底。"""
    statement = select(Project.id).where(
        Project.owner_id == owner_id,
        Project.name == name,
    )
    if excluded_project_id is not None:
        statement = statement.where(Project.id != excluded_project_id)
    if session.exec(statement.limit(1)).first() is not None:
        raise AppError(409, "PROJECT_NAME_CONFLICT", "当前用户已存在同名项目。")


def _ensure_expected_version(*, actual_version: int, expected_version: int) -> None:
    """拒绝陈旧写入，防止较早页面覆盖已保存的项目修改。"""
    if actual_version != expected_version:
        raise AppError(409, "VERSION_CONFLICT", "项目已被其他操作修改，请刷新后重试。")


def _is_project_name_conflict(exc: IntegrityError) -> bool:
    """识别 PostgreSQL/SQLite 对项目名称唯一约束的不同错误文本。"""
    detail = str(exc.orig).lower()
    return "uq_projects_owner_name" in detail or "projects.owner_id, projects.name" in detail
