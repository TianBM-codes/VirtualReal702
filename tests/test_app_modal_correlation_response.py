import asyncio
import json

import pytest

pytest.importorskip("fastapi")
import app as legacy_app


def _make_json_request(path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return legacy_app.Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def test_modal_correlation_api_returns_code(monkeypatch):
    monkeypatch.setattr(
        legacy_app,
        "get_modal_correlation_matrix_payload",
        lambda project_id: {"project_id": int(project_id), "data": {"rows": [], "column": [], "data": []}},
    )

    response = asyncio.run(
        legacy_app.get_modal_correlation_api(
            _make_json_request("/correlation/modal", {"project_id": 18})
        )
    )

    assert response["ok"] is True
    assert response["code"] == 200
    assert response["data"]["project_id"] == 18


def test_modal_match_api_includes_step_names_from_src(monkeypatch):
    monkeypatch.setattr(
        legacy_app,
        "match_modal_modes",
        lambda project_id, **kwargs: {"project_id": int(project_id), "rows": [], "summary": {}},
    )
    async def _fake_list_steps(odb_id: str):
        return {
            "code": 200,
            "data": [
                {"step_name": "SUBCASE_1"},
                {"step_name": "SUBCASE_2"},
            ],
            "message": "",
        }

    monkeypatch.setattr(legacy_app, "list_src_steps", _fake_list_steps)

    response = asyncio.run(
        legacy_app.match_modal_api(
            _make_json_request("/correlation/modal/match", {"project_id": 18})
        )
    )

    assert response["ok"] is True
    assert response["message"] == "模态匹配成功"
    assert response["data"]["step_names"] == ["SUBCASE_1", "SUBCASE_2"]
