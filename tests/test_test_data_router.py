import pytest


def test_import_unv_route_downloads_remote_file_before_import(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import test_data

    captured = {}

    monkeypatch.setattr(
        test_data,
        "_resolve_unv_download_dir",
        lambda project_id=None: "D:/workspace/project_18",
    )
    monkeypatch.setattr(
        test_data,
        "download_if_url",
        lambda file_path, dest_dir=None: captured.update(
            {"download_file_path": file_path, "download_dest_dir": dest_dir}
        ) or "D:/workspace/project_18/source.unv",
    )
    monkeypatch.setattr(
        test_data,
        "import_unv_data",
        lambda file_path, **kwargs: captured.update(
            {"import_file_path": file_path, **kwargs}
        ) or {"project_id": kwargs["project_id"], "imported": True},
    )

    app = FastAPI()
    app.include_router(test_data.router)
    client = TestClient(app)

    response = client.post(
        "/import/unv",
        json={
            "file_path": "http://example.com/source.unv",
            "project_id": 18,
            "file_id": 101,
            "clear_before_insert": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["project_id"] == 18
    assert captured["download_file_path"] == "http://example.com/source.unv"
    assert captured["download_dest_dir"] == "D:/workspace/project_18"
    assert captured["import_file_path"] == "D:/workspace/project_18/source.unv"
    assert captured["project_id"] == 18
    assert captured["file_id"] == 101
