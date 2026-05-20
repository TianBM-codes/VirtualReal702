import logging

import pytest


def test_init_db_logs_success(monkeypatch, caplog):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import system

    monkeypatch.setattr(system, "ensure_tables_exist", lambda: None)

    app = FastAPI()
    app.include_router(system.router)
    client = TestClient(app)

    with caplog.at_level(logging.INFO):
        response = client.post("/init")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["message"] == "数据库表初始化成功"
    assert "开始初始化数据库表" in caplog.text
    assert "数据库表初始化成功" in caplog.text
