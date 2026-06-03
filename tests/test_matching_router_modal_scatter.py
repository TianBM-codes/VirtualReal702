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


def test_modal_correlation_all_scatter_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import matching

    captured = {}

    def fake_get_modal_correlation_all_scatter_payload(**kwargs):
        captured.update(kwargs)
        return {
            "project_id": kwargs["project_id"],
            "chart_type": "scatter",
            "value_label": "mac",
            "data": [
                {
                    "label": "frequency_order_pairs",
                    "xaxis": ["31.800"],
                    "data": [["31.800", 32.5]],
                    "points": [{"x": "31.800", "y": 32.5, "value": 97.5, "tooltip": {"mac": 97.5}}],
                }
            ],
            "summary": {"point_count": 1},
        }

    monkeypatch.setattr(
        matching,
        "get_modal_correlation_all_scatter_payload",
        fake_get_modal_correlation_all_scatter_payload,
    )

    app = FastAPI()
    app.include_router(matching.router)
    client = TestClient(app)

    response = client.post(
        "/correlation/modal/all_scatter",
        json={"project_id": 18},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["value_label"] == "mac"
    assert payload["data"]["data"][0]["points"][0]["tooltip"]["mac"] == 97.5
    assert captured == {"project_id": 18}


def test_modal_frequency_consistency_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import matching

    captured = {}

    monkeypatch.setattr(
        matching,
        "get_modal_frequency_consistency_payload",
        lambda project_id: captured.update({"project_id": project_id}) or {
            "project_id": int(project_id),
            "chart_type": "line",
            "data": [{"label": "frequency_consistency_error", "xaxis": ["1"], "data": [2.0]}],
            "rows": [{"freq_error_ratio": 0.02, "freq_error_percent": 2.0}],
            "summary": {"compared_mode_count": 1, "point_count": 1},
        },
    )

    app = FastAPI()
    app.include_router(matching.router)
    client = TestClient(app)

    response = client.post(
        "/correlation/modal/frequency_consistency",
        json={"project_id": 18},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["chart_type"] == "line"
    assert payload["data"]["data"][0]["data"][0] == 2.0
    assert payload["data"]["rows"][0]["freq_error_percent"] == 2.0
    assert captured == {"project_id": 18}


def test_evaluate_correlation_route_uses_modal_branch_for_mtxz(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import matching

    captured = {}

    monkeypatch.setattr(
        matching,
        "get_modal_frequency_consistency_payload",
        lambda project_id: captured.update({"project_id": project_id}) or {
            "project_id": int(project_id),
            "project_type": "MTXZ",
            "rows": [],
            "summary": {"compared_mode_count": 0},
        },
    )
    monkeypatch.setattr(
        matching,
        "evaluate_static_correlation",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("static branch should not be used")),
    )

    app = FastAPI()
    app.include_router(matching.router)
    client = TestClient(app)

    response = client.post(
        "/correlation/evaluate",
        json={"project_id": 18, "project_type": "MTXZ"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["project_type"] == "MTXZ"
    assert captured == {"project_id": 18}
