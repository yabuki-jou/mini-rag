"""提供制度 Agent 的隔离 HTTP 评测 Runnable。"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

import httpx
import pixie
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlmodel import Session, SQLModel, create_engine


class PolicyAgentArgs(BaseModel):
    """定义单条制度 Agent 评测样本的用户输入。"""

    model_config = ConfigDict(str_strip_whitespace=True)
    user_message: str = Field(min_length=1, max_length=2000)


class PolicyAgentRunnable(pixie.Runnable[PolicyAgentArgs]):
    """通过真实 Bearer HTTP 链路运行制度 Agent 评测样本。"""

    _client: httpx.AsyncClient
    _semaphore: asyncio.Semaphore
    _temporary_directory: TemporaryDirectory[str]
    _business_engine: Any
    _checkpoint_path: Path
    _original_dependency_overrides: dict[Any, Any]
    _original_auth_jwt_secret: SecretStr | None
    last_user_id: str = ""
    last_kb_id: str = ""
    last_session_id: str = ""
    last_message_status: int | None = None

    @classmethod
    def create(cls) -> "PolicyAgentRunnable":
        """创建串行执行的制度 Agent Runnable。"""
        instance = cls()
        instance._semaphore = asyncio.Semaphore(1)
        return instance

    async def setup(self) -> None:
        """建立临时业务库、Checkpoint 和 FastAPI 依赖覆盖。"""
        from app.agents.admin.runtime import build_admin_runtime
        from app.core.config import settings
        from app.db import get_session
        from app.dependencies.agent import get_admin_agent_runtime
        from app.main import app

        self._temporary_directory = TemporaryDirectory(prefix="mini-rag-policy-eval-")
        temporary_path = Path(self._temporary_directory.name)
        self._checkpoint_path = temporary_path / "checkpoint.db"
        database_path = temporary_path / "business.db"
        self._business_engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self._business_engine)

        self._original_dependency_overrides = dict(app.dependency_overrides)
        self._original_auth_jwt_secret = settings.auth_jwt_secret
        # 评测只使用临时签名密钥；不读取、记录或覆盖用户的真实密钥。
        settings.auth_jwt_secret = SecretStr(f"policy-eval-{uuid4().hex}")

        def override_session():
            with Session(self._business_engine) as session:
                yield session

        def override_runtime():
            # 不传入 model，生产默认路径仍由 build_admin_runtime 加载 DeepSeek。
            with build_admin_runtime(checkpoint_path=self._checkpoint_path) as runtime:
                yield runtime

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_admin_agent_runtime] = override_runtime
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://policy-agent-eval.local",
            timeout=120.0,
        )

    async def run(self, args: PolicyAgentArgs) -> None:
        """为每条样本创建独立身份、知识库和会话并发送一条消息。"""
        async with self._semaphore:
            run_id = uuid4().hex
            username = f"eval{run_id[:16]}"
            register_response = await self._client.post(
                "/auth/register",
                json={
                    "username": username,
                    "name": f"制度评测用户 {run_id[:8]}",
                    "password": f"Eval-{run_id}-Password!",
                },
            )
            register_response.raise_for_status()

            login_response = await self._client.post(
                "/auth/login",
                json={"username": username, "password": f"Eval-{run_id}-Password!"},
            )
            login_response.raise_for_status()
            token = login_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            knowledge_base_response = await self._client.post(
                "/knowledge-bases",
                headers=headers,
                json={"name": f"制度评测库-{run_id[:8]}"},
            )
            knowledge_base_response.raise_for_status()
            kb_id = knowledge_base_response.json()["id"]

            session_response = await self._client.post(
                "/agent-sessions",
                headers=headers,
                json={"kb_id": kb_id},
            )
            session_response.raise_for_status()
            session_id = session_response.json()["id"]

            message_response = await self._client.post(
                f"/agent-sessions/{session_id}/messages",
                headers=headers,
                json={"message": args.user_message},
            )
            message_response.raise_for_status()

            self.last_user_id = register_response.json()["id"]
            self.last_kb_id = kb_id
            self.last_session_id = session_id
            self.last_message_status = message_response.status_code

    async def teardown(self) -> None:
        """关闭 HTTP 和临时存储，并恢复原有全局依赖。"""
        from app.core.config import settings
        from app.main import app

        try:
            await self._client.aclose()
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(self._original_dependency_overrides)
            settings.auth_jwt_secret = self._original_auth_jwt_secret
            self._business_engine.dispose()
            self._temporary_directory.cleanup()


__all__ = ["PolicyAgentArgs", "PolicyAgentRunnable"]
