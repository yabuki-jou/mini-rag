"""验证共享 DeepSeek 客户端的固定超时和重试配置边界。"""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.services.infrastructure import ai_models


@pytest.fixture(autouse=True)
def clear_chat_model_cache():
    """隔离不同参数组合的缓存实例，避免测试顺序影响构造断言。"""
    ai_models.get_chat_model.cache_clear()
    yield
    ai_models.get_chat_model.cache_clear()


def _chat_settings(*, timeout_seconds: float) -> SimpleNamespace:
    """构造不读取本地密钥文件的最小聊天模型配置。"""
    return SimpleNamespace(
        deepseek_api_key=SecretStr("test-only-key"),
        deepseek_model="deepseek-chat",
        deepseek_base_url="https://example.invalid/v1",
        deepseek_request_timeout_seconds=timeout_seconds,
    )


def test_get_chat_model_default_call_passes_timeout_without_overriding_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无参兼容入口必须传超时，但不能改变客户端既有隐藏重试语义。"""
    captured: dict[str, object] = {}

    def fake_chat_openai(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ai_models, "settings", _chat_settings(timeout_seconds=17.5))
    monkeypatch.setattr(ai_models, "ChatOpenAI", fake_chat_openai)

    ai_models.get_chat_model()

    assert captured["timeout"] == 17.5
    assert "max_retries" not in captured


def test_get_chat_model_allows_explicit_zero_hidden_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """档案助手可显式关闭客户端隐藏重试，同时继续使用固定超时。"""
    captured: dict[str, object] = {}

    def fake_chat_openai(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ai_models, "settings", _chat_settings(timeout_seconds=30))
    monkeypatch.setattr(ai_models, "ChatOpenAI", fake_chat_openai)

    ai_models.get_chat_model(max_retries=0)

    assert captured["timeout"] == 30
    assert captured["max_retries"] == 0
