"""验证 FR-042 P05 共享 Checkpoint Runtime 的隔离、序列化和删除。"""

from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.admin.runtime import build_admin_runtime
from app.agents.archive.runtime import build_archive_runtime
from app.agents.checkpoint import delete_checkpoint_thread
from app.services.infrastructure import ai_models
from tests.agents.archive.test_archive_graph import StubChatModel


def test_policy_and_archive_share_file_without_cross_thread_state(tmp_path) -> None:
    """同一严格 SQLite 文件中的 POLICY/ARCHIVE 线程必须互不串读。"""
    checkpoint_path = tmp_path / "shared-agent-checkpoints.db"
    with build_admin_runtime(
        model=StubChatModel([AIMessage(content="制度回答")]),
        checkpoint_path=checkpoint_path,
    ) as policy_runtime:
        policy_runtime.invoke(
            {
                "messages": [HumanMessage(content="制度问题")],
                "user_id": str(uuid4()),
                "kb_id": str(uuid4()),
            },
            thread_id="policy-thread",
        )

    with build_archive_runtime(
        user_id=uuid4(),
        project_id=uuid4(),
        kb_id=uuid4(),
        session_factory=lambda: nullcontext(SimpleNamespace()),
        model=StubChatModel([AIMessage(content="不可信自由回答")]),
        judge_model=StubChatModel([]),
        checkpoint_path=checkpoint_path,
    ) as archive_runtime:
        archive_runtime.invoke(
            {
                "messages": [HumanMessage(content="档案问题")],
                "user_id": str(archive_runtime.user_id),
                "project_id": str(archive_runtime.project_id),
                "kb_id": str(archive_runtime.kb_id),
                "tool_call_count": 0,
            },
            thread_id="archive-thread",
        )
        assert archive_runtime.get_state(thread_id="policy-thread").values["messages"][-1].content == "制度回答"
        assert archive_runtime.get_state(thread_id="archive-thread").values["messages"][-1].content == "正式档案中没有足够依据。"
        serializer = archive_runtime.checkpointer.serde
        assert serializer.pickle_fallback is False
        assert serializer._allowed_json_modules is None
        assert serializer._allowed_msgpack_modules is None


def test_delete_checkpoint_thread_is_idempotent_and_never_builds_model(
    tmp_path,
    monkeypatch,
) -> None:
    """线程删除只操作共享存储，重复调用也不得构造 DeepSeek。"""
    checkpoint_path = tmp_path / "delete-thread.db"
    with build_admin_runtime(
        model=StubChatModel([AIMessage(content="待删除")]),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        runtime.invoke(
            {
                "messages": [HumanMessage(content="问题")],
                "user_id": str(uuid4()),
                "kb_id": str(uuid4()),
            },
            thread_id="delete-me",
        )

    def fail_model() -> None:
        raise AssertionError("删除 Checkpoint 不得构造模型")

    monkeypatch.setattr(ai_models, "get_chat_model", fail_model)
    delete_checkpoint_thread(checkpoint_path=checkpoint_path, thread_id="delete-me")
    delete_checkpoint_thread(checkpoint_path=checkpoint_path, thread_id="delete-me")

    with build_admin_runtime(
        model=StubChatModel([AIMessage(content="未调用")]),
        checkpoint_path=checkpoint_path,
    ) as runtime:
        assert runtime.get_state(thread_id="delete-me").values == {}
