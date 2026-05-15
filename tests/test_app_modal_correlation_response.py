import asyncio
import json

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
