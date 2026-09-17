"""以最小输入驱动智慧档案生产问答服务的 Pixie Runnable。"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import httpx
import pixie
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlmodel import Session, SQLModel, create_engine

from app.core.evaluation import eval_wrap, evaluation_name_scope
from app.services.archive.questions import answer_archive_question


_D5_USER_ID = UUID("00000000-0000-4000-8000-0000000000d5")
_D5_PROJECT_ID = UUID("00000000-0000-4000-8000-0000000000d6")
_D5_KB_ID = UUID("00000000-0000-4000-8000-0000000000d7")


def _build_failure_observation(
    *,
    status_code: int,
    error_payload: object,
    tool_calls: object,
    history: object,
) -> dict[str, object]:
    """把失败响应和工具审计投影为不含标识、参数值或正文的诊断。"""
    error_code: str | None = None
    if isinstance(error_payload, dict):
        error = error_payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            error_code = error["code"]
    safe_audit: list[dict[str, object]] = []
    if isinstance(tool_calls, list):
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            safe_audit.append(
                {
                    "tool_name": item.get("tool_name"),
                    "status": item.get("status"),
                    "error_code": item.get("error_code"),
                }
            )
    history_roles = [
        item.get("role")
        for item in history
        if isinstance(item, dict)
    ] if isinstance(history, list) else []
    return {
        "http_status": status_code,
        "error_code": error_code,
        "tool_audit": safe_audit,
        "history_message_count": len(history_roles),
        "history_roles": history_roles,
    }


class ArchiveQuestionArgs(BaseModel):
    """定义 D5 Runnable 唯一允许改变的用户问题。"""

    question: str = Field(min_length=1, max_length=2000)


class ArchiveQuestionRunnable(pixie.Runnable[ArchiveQuestionArgs]):
    """串行调用生产问答服务，并由 Pixie 输入边界注入候选证据。"""

    _semaphore: asyncio.Semaphore

    @classmethod
    def create(cls) -> "ArchiveQuestionRunnable":
        """创建 D5 Runnable，并固定串行执行以避免共享观测状态交叉。"""
        instance = cls()
        instance._semaphore = asyncio.Semaphore(1)
        return instance

    async def run(self, args: ArchiveQuestionArgs) -> None:
        """把单个问题交给生产服务；检索函数由评测输入边界替换。"""
        async with self._semaphore:
            # 生产入口仍接收已验证上下文所需的三类 UUID；它们不属于用户可控输入，
            # 评测固定为虚构值，并把 session 留空交给 Pixie 注入的检索结果绕过。
            started_at = perf_counter()
            try:
                await asyncio.to_thread(
                    answer_archive_question,
                    user_id=_D5_USER_ID,
                    project_id=_D5_PROJECT_ID,
                    kb_id=_D5_KB_ID,
                    question=args.question,
                    session=None,
                )
            finally:
                # 在 finally 中记录，确保失败请求也能保留完整链路耗时，便于定位慢请求。
                eval_wrap(
                    (perf_counter() - started_at) * 1000.0,
                    purpose="output",
                    name="archive_question_end_to_end_latency_ms",
                    description="单个档案问题从生产问答入口开始到结束的端到端耗时（毫秒）。",
                )


class ArchiveAgentArgs(BaseModel):
    """定义一个真实用户会话中的一轮或两轮消息。"""

    model_config = ConfigDict(extra="forbid")

    messages: list[str] = Field(min_length=1, max_length=2)

    @field_validator("messages")
    @classmethod
    def normalize_messages(cls, value: list[str]) -> list[str]:
        """统一换行与首尾空白，并拒绝空消息或超长消息。"""
        normalized = [
            message.replace("\r\n", "\n").replace("\r", "\n").strip()
            for message in value
        ]
        if any(not message or len(message) > 2000 for message in normalized):
            raise ValueError("每条消息长度必须为 1 到 2000 个码点。")
        return normalized


class ArchiveAgentRunnable(pixie.Runnable[ArchiveAgentArgs]):
    """通过真实 Bearer HTTP 链路运行项目档案助手评测会话。"""

    _client: httpx.AsyncClient
    _semaphore: asyncio.Semaphore
    _temporary_directory: TemporaryDirectory[str]
    _business_engine: Any
    _original_dependency_overrides: dict[Any, Any]
    _original_auth_jwt_secret: SecretStr | None
    _original_checkpoint_file: Path
    _original_catalog_source: Any
    _original_evidence_source: Any

    @classmethod
    def create(cls) -> "ArchiveAgentRunnable":
        """创建串行 Runnable，避免临时数据库和全局依赖覆盖交叉。"""
        instance = cls()
        instance._semaphore = asyncio.Semaphore(1)
        return instance

    async def setup(self) -> None:
        """建立评测专用业务库、Checkpoint、认证密钥和 ASGI 客户端。"""
        from app.agents.tools import archive_tools
        from app.core.config import settings
        from app.db import get_session
        from app.main import app
        from app.services.archive import catalog
        from evals.archive.reference_world import (
            reference_catalog_result,
            reference_evidence_result,
        )

        self._temporary_directory = TemporaryDirectory(prefix="mini-rag-archive-agent-eval-")
        temporary_path = Path(self._temporary_directory.name)
        database_path = temporary_path / "business.db"
        self._business_engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self._business_engine)

        self._original_dependency_overrides = dict(app.dependency_overrides)
        self._original_auth_jwt_secret = settings.auth_jwt_secret
        self._original_checkpoint_file = settings.agent_checkpoint_file
        settings.auth_jwt_secret = SecretStr(f"archive-agent-eval-{uuid4().hex}")
        settings.agent_checkpoint_file = temporary_path / "checkpoint.db"
        self._original_catalog_source = catalog.list_agent_formal_archives
        self._original_evidence_source = archive_tools._retrieve_archive_agent_evidence
        catalog.list_agent_formal_archives = reference_catalog_result
        archive_tools._retrieve_archive_agent_evidence = reference_evidence_result

        def override_session():
            """为每个 HTTP 请求提供同一临时业务库中的独立 Session。"""
            with Session(self._business_engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://archive-agent-eval.local",
            timeout=180.0,
        )

    async def run(self, args: ArchiveAgentArgs) -> None:
        """创建隔离身份、项目和 ARCHIVE 会话，再顺序发送真实用户消息。"""
        with evaluation_name_scope():
            await self._run_scoped(args)

    async def _run_scoped(self, args: ArchiveAgentArgs) -> None:
        """在单一命名作用域内执行完整的一轮或两轮会话。"""
        async with self._semaphore:
            run_id = uuid4().hex
            username = f"archiveeval{run_id[:14]}"
            password = f"Eval-{run_id}-Password!"
            register = await self._client.post(
                "/auth/register",
                json={
                    "username": username,
                    "name": f"档案助手评测用户 {run_id[:8]}",
                    "password": password,
                },
            )
            register.raise_for_status()
            login = await self._client.post(
                "/auth/login",
                json={"username": username, "password": password},
            )
            login.raise_for_status()
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            project = await self._client.post(
                "/projects",
                headers=headers,
                json={
                    "name": f"档案助手评测项目-{run_id[:8]}",
                    "description": "仅用于隔离的项目档案助手质量评测。",
                    "use_demo_checklist": False,
                },
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            agent_session = await self._client.post(
                f"/projects/{project_id}/agent-sessions",
                headers=headers,
                json={},
            )
            agent_session.raise_for_status()
            session_id = agent_session.json()["id"]

            statuses: list[int] = []
            for message in args.messages:
                response = await self._client.post(
                    f"/projects/{project_id}/agent-sessions/{session_id}/messages",
                    headers=headers,
                    json={"message": message},
                )
                statuses.append(response.status_code)
                if response.is_error:
                    tool_calls = await self._client.get(
                        f"/projects/{project_id}/agent-sessions/{session_id}/tool-calls",
                        headers=headers,
                    )
                    history = await self._client.get(
                        f"/projects/{project_id}/agent-sessions/{session_id}/messages",
                        headers=headers,
                    )
                    eval_wrap(
                        _build_failure_observation(
                            status_code=response.status_code,
                            error_payload=response.json(),
                            tool_calls=(
                                tool_calls.json()
                                if tool_calls.status_code == 200
                                else []
                            ),
                            history=(
                                history.json()
                                if history.status_code == 200
                                else []
                            ),
                        ),
                        purpose="state",
                        name="archive_agent_failure_state",
                        description="不含标识、参数值和正文的失败状态与工具审计形状。",
                    )
                    return

            history = await self._client.get(
                f"/projects/{project_id}/agent-sessions/{session_id}/messages",
                headers=headers,
            )
            history.raise_for_status()
            tool_calls = await self._client.get(
                f"/projects/{project_id}/agent-sessions/{session_id}/tool-calls",
                headers=headers,
            )
            tool_calls.raise_for_status()
            history_payload = history.json()
            eval_wrap(
                {
                    "requested_turn_count": len(args.messages),
                    "completed_http_statuses": statuses,
                    "history_message_count": len(history_payload),
                    "history_roles": [item.get("role") for item in history_payload],
                    "audit_record_count": len(tool_calls.json()),
                },
                purpose="state",
                name="archive_agent_conversation_state",
                description="不含范围与会话标识的完整轮次、HTTP 完成状态和审计数量。",
            )

    async def teardown(self) -> None:
        """关闭评测资源并恢复应用全局依赖和配置。"""
        from app.agents.tools import archive_tools
        from app.core.config import settings
        from app.main import app
        from app.services.archive import catalog

        try:
            await self._client.aclose()
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(self._original_dependency_overrides)
            settings.auth_jwt_secret = self._original_auth_jwt_secret
            settings.agent_checkpoint_file = self._original_checkpoint_file
            catalog.list_agent_formal_archives = self._original_catalog_source
            archive_tools._retrieve_archive_agent_evidence = self._original_evidence_source
            self._business_engine.dispose()
            self._temporary_directory.cleanup()


__all__ = [
    "ArchiveAgentArgs",
    "ArchiveAgentRunnable",
    "ArchiveQuestionArgs",
    "ArchiveQuestionRunnable",
]
