import pytest


def test_modal_match_frequency_scatter_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import matching

    captured = {}

    def fake_get_modal_match_frequency_scatter_payload(**kwargs):
        captured.update(kwargs)
        return {
            "project_id": kwargs["project_id"],
            "chart_type": "scatter",
            "data": [
                {
                    "label": "matched_modes",
                    "xaxis": ["31.8"],
                    "data": [[31.8, 32.5]],
                    "points": [{"x": 31.8, "y": 32.5, "tooltip": {"fem_mode_no": 1}}],
                }
            ],
            "summary": {"point_count": 1},
        }

    monkeypatch.setattr(
        matching,
        "get_modal_match_frequency_scatter_payload",
        fake_get_modal_match_frequency_scatter_payload,
    )

    app = FastAPI()
    app.include_router(matching.router)
    client = TestClient(app)

    response = client.post(
        "/correlation/modal/match/frequency_scatter",
        json={
            "project_id": 18,
            "mac_threshold": 0.75,
            "max_freq_error_ratio": 0.15,
            "method": "greedy",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["chart_type"] == "scatter"
    assert payload["data"]["data"][0]["points"][0]["tooltip"]["fem_mode_no"] == 1
    assert captured == {
        "project_id": 18,
        "mac_threshold": 0.75,
        "max_freq_error_ratio": 0.15,
        "method": "greedy",
    }
