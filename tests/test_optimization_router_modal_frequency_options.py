import pytest


def test_modal_frequency_response_options_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    captured = {}

    def fake_get_modal_frequency_response_options(**kwargs):
        captured.update(kwargs)
        return {
            "project_id": kwargs["project_id"],
            "response_source": kwargs["response_source"],
            "columns": [
                {"key": "order", "title": "阶次", "type": "number"},
                {"key": "frequency_hz", "title": "频率(Hz)", "type": "number"},
            ],
            "rows": [{"order": 1, "frequency_hz": 10.2}],
            "summary": {"row_count": 1, "column_count": 2},
        }

    monkeypatch.setattr(
        optimization,
        "get_modal_frequency_response_options",
        fake_get_modal_frequency_response_options,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/response/modal_frequency/options",
        json={"project_id": 18, "response_source": "FEM"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["response_source"] == "FEM"
    assert payload["data"]["columns"][0]["title"] == "阶次"
    assert payload["data"]["rows"][0]["order"] == 1
    assert captured == {"project_id": 18, "response_source": "FEM"}
