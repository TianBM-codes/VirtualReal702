import pytest


def test_import_unv_route_downloads_remote_file_before_import(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
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


def test_frf_query_routes_proxy_service_payloads(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import test_data

    monkeypatch.setattr(
        test_data,
        "get_frf_names",
        lambda project_id: {"project_id": project_id, "names": ["FRF_A", "FRF_B"]},
    )
    monkeypatch.setattr(
        test_data,
        "get_frf_curve",
        lambda project_id, name=None, names=None, index=1: {
            "project_id": project_id,
            "names": ["TEST FRF 1 (+3UZ : +3UZ)", "TEST FRF 2 (+5UX : +3UZ)"],
            "line_name": "FRF 1" if (names or name) else None,
            "line_names": [f"FRF {idx + 1}" for idx, _ in enumerate(names or ([name] if name else []))],
            "x_name": "Frequency[Hz]",
            "y_name": "Phase",
            "x": [1.2346],
            "lines": [
                {
                    "line_name": f"FRF {idx + 1}",
                    "x": [1.2346],
                    "series": [[1.2346, float(index)]],
                }
                for idx, item in enumerate(names or ([name] if name else []))
            ],
            "series": [[1.2346, float(index)]],
        },
    )

    app = FastAPI()
    app.include_router(test_data.router)
    client = TestClient(app)

    names_response = client.post("/frf/names", json={"project_id": 18})
    curve_response = client.post("/frf/curve", json={"project_id": 18, "name": "FRF_B", "index": 4})

    assert names_response.status_code == 200
    assert names_response.json()["data"]["names"] == ["FRF_A", "FRF_B"]
    assert curve_response.status_code == 200
    assert curve_response.json()["data"]["line_name"] == "FRF 1"
    assert curve_response.json()["data"]["x_name"] == "Frequency[Hz]"
    assert curve_response.json()["data"]["y_name"] == "Phase"
    assert curve_response.json()["data"]["x"] == [1.2346]
    assert curve_response.json()["data"]["series"] == [[1.2346, 4.0]]

    multi_curve_response = client.post(
        "/frf/curve",
        json={"project_id": 18, "names": ["FRF_A", "FRF_B"], "index": 3},
    )
    assert multi_curve_response.status_code == 200
    assert multi_curve_response.json()["data"]["line_names"] == ["FRF 1", "FRF 2"]
    assert len(multi_curve_response.json()["data"]["lines"]) == 2
    assert multi_curve_response.json()["data"]["lines"][1]["line_name"] == "FRF 2"
