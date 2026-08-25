"""验证健康检查把 Chroma 作为独立依赖暴露。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import Response

from app.routers import health


def test_health_check_reports_chroma_heartbeat(
    monkeypatch,
) -> None:
    """健康检查只调用 Chroma 心跳，不能创建或写入 Collection。"""
    connection = MagicMock()
    connection.exec_driver_sql.return_value.scalar_one.return_value = 1
    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False
    engine = MagicMock()
    engine.connect.return_value = context
    heartbeat = MagicMock(return_value=1)

    monkeypatch.setattr(health, "engine", engine)
    monkeypatch.setattr(health, "check_chroma_connection", heartbeat)
    monkeypatch.setattr(
        health,
        "get_embeddings",
        lambda: SimpleNamespace(embed_query=lambda _: [0.0] * 512),
    )
    monkeypatch.setattr(health.settings, "embedding_dimension", 512)
    response = Response()

    result = health.health_check(response)

    assert response.status_code == 200
    assert result.status == "ok"
    assert result.components["chroma"].detail == "heartbeat=ok"
    heartbeat.assert_called_once_with()
