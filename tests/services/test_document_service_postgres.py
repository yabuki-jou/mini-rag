"""在显式授权时验证项目上传的 PostgreSQL 并发容量锁。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import shutil
from threading import Barrier, Condition
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import delete, event, func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import AppError
from app.db import engine
from app.models import ArchiveDocument, Document, KnowledgeBase, Project, User
from app.services.document_service import create_project_uploaded_document


RUN_POSTGRES_CONCURRENCY_TEST = (
    os.getenv("RUN_POSTGRES_CONCURRENCY_TEST") == "1"
)


def _upload_one_document(
    *,
    project_id: UUID,
    kb_id: UUID,
    worker_number: int,
    start_barrier: Barrier,
) -> tuple[str, str]:
    """使用独立 Session 同时竞争同一项目行锁。"""
    upload = UploadFile(
        filename=f"p05-concurrent-{worker_number}.txt",
        file=BytesIO(f"fictional p05 document {worker_number}".encode()),
    )
    with Session(engine) as session:
        start_barrier.wait(timeout=10)
        try:
            result = asyncio.run(
                create_project_uploaded_document(
                    upload=upload,
                    project_id=project_id,
                    kb_id=kb_id,
                    session=session,
                )
            )
        except AppError as exc:
            return "error", exc.code
    return "created", str(result.id)


def _delete_postgres_test_scope(
    *,
    user_id: UUID,
    kb_id: UUID,
    project_id: UUID,
    storage_root: Path,
) -> None:
    """只清理本次 UUID 范围内的数据库记录和临时原文件。"""
    with Session(engine) as session:
        document_ids = list(
            session.exec(
                select(Document.id).where(Document.project_id == project_id)
            ).all()
        )
        if document_ids:
            session.exec(
                delete(ArchiveDocument).where(
                    ArchiveDocument.document_id.in_(document_ids)
                )
            )
        session.exec(delete(Document).where(Document.project_id == project_id))
        session.exec(delete(Project).where(Project.id == project_id))
        session.exec(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        session.exec(delete(User).where(User.id == user_id))
        session.commit()

    # 该目录由 pytest 为本测试单独创建，不会触及应用真实上传目录。
    if storage_root.exists():
        shutil.rmtree(storage_root)


@pytest.mark.skipif(
    not RUN_POSTGRES_CONCURRENCY_TEST,
    reason="未显式授权 RUN_POSTGRES_CONCURRENCY_TEST=1",
)
def test_project_upload_capacity_is_serialized_by_postgres_row_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """99 份时两个并发上传只能成功一个，且测试资源必须完全清理。"""
    assert engine.dialect.name == "postgresql"
    run_id = uuid4()
    user_id = uuid4()
    kb_id = uuid4()
    project_id = uuid4()
    storage_root = tmp_path / f"p05-postgres-{run_id}"
    monkeypatch.setattr(settings, "file_storage_dir", storage_root)

    with Session(engine) as session:
        session.add(User(id=user_id, name=f"p05-postgres-{run_id}"))
        session.flush()
        session.add(
            KnowledgeBase(
                id=kb_id,
                owner_id=user_id,
                name=f"p05-postgres-{run_id}",
            )
        )
        session.flush()
        session.add(
            Project(
                id=project_id,
                owner_id=user_id,
                kb_id=kb_id,
                name=f"P05 PostgreSQL {run_id}",
                active_document_count=99,
            )
        )
        session.commit()

    try:
        lock_attempt_condition = Condition()
        lock_attempt_count = 0

        def observe_project_lock_attempt(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            """记录生产服务真正发出的两个 ``FOR UPDATE``。"""
            nonlocal lock_attempt_count
            if "FOR UPDATE" not in statement.upper():
                return
            with lock_attempt_condition:
                lock_attempt_count += 1
                lock_attempt_condition.notify_all()

        start_barrier = Barrier(2)
        executor = ThreadPoolExecutor(max_workers=2)
        with Session(engine) as lock_session:
            # 控制事务先持锁，使两个工作线程必然同时进入 PostgreSQL 锁等待。
            locked_project = lock_session.exec(
                select(Project)
                .where(Project.id == project_id)
                .with_for_update()
            ).one()
            assert locked_project.active_document_count == 99
            event.listen(engine, "before_cursor_execute", observe_project_lock_attempt)
            futures = [
                executor.submit(
                    _upload_one_document,
                    project_id=project_id,
                    kb_id=kb_id,
                    worker_number=worker_number,
                    start_barrier=start_barrier,
                )
                for worker_number in (1, 2)
            ]
            try:
                with lock_attempt_condition:
                    both_waiting = lock_attempt_condition.wait_for(
                        lambda: lock_attempt_count >= 2,
                        timeout=10,
                    )
                blocked_before_release = not any(future.done() for future in futures)
            finally:
                # 无论观测是否成功，都必须释放控制锁，避免工作线程永久等待。
                lock_session.commit()
                event.remove(
                    engine,
                    "before_cursor_execute",
                    observe_project_lock_attempt,
                )

            outcomes = sorted(future.result(timeout=20) for future in futures)
        executor.shutdown(wait=True, cancel_futures=True)

        assert both_waiting
        assert blocked_before_release
        assert [outcome[0] for outcome in outcomes] == ["created", "error"]
        assert outcomes[1] == ("error", "PROJECT_DOCUMENT_LIMIT_REACHED")

        with Session(engine) as session:
            project = session.get(Project, project_id)
            document_count = session.exec(
                select(func.count())
                .select_from(Document)
                .where(Document.project_id == project_id)
            ).one()
            archive_document_count = session.exec(
                select(func.count())
                .select_from(ArchiveDocument)
                .join(Document, Document.id == ArchiveDocument.document_id)
                .where(Document.project_id == project_id)
            ).one()

        assert project is not None
        assert project.active_document_count == 100
        assert document_count == 1
        assert archive_document_count == 1
        assert len([path for path in storage_root.rglob("*") if path.is_file()]) == 1
    finally:
        _delete_postgres_test_scope(
            user_id=user_id,
            kb_id=kb_id,
            project_id=project_id,
            storage_root=storage_root,
        )

    with Session(engine) as session:
        assert session.get(User, user_id) is None
        assert session.get(KnowledgeBase, kb_id) is None
        assert session.get(Project, project_id) is None
        assert session.exec(
            select(func.count())
            .select_from(Document)
            .where(Document.project_id == project_id)
        ).one() == 0
    assert not storage_root.exists()
