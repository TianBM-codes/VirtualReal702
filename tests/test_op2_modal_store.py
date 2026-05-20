import os

import numpy as np
import pytest

from services.model_update.importers import op2_service


class _FakeEigenData:
    def __init__(self, *, mode_no: int, frequency: float, node_ids, scale: float):
        self.modes = np.asarray([mode_no], dtype=np.int64)
        self.mode_cycles = np.asarray([frequency], dtype=np.float64)
        self.node_gridtype = np.asarray([[int(node_id), 1] for node_id in node_ids], dtype=np.int64)
        self.data = np.asarray([
            [
                [scale * float(index + 1), 0.0, 0.0, 0.0, 0.0, 0.0]
                for index, _ in enumerate(node_ids)
            ]
        ], dtype=np.float64)


class _FakeOp2:
    def __init__(self):
        self.filename = "fake.op2"
        self.eigenvectors = {
            10: _FakeEigenData(mode_no=1, frequency=10.0, node_ids=[101, 102], scale=1.0),
            20: _FakeEigenData(mode_no=1, frequency=20.0, node_ids=[101, 102], scale=10.0),
        }


def test_build_modal_import_payload_can_import_all_subcases(monkeypatch):
    monkeypatch.setattr(op2_service, "_abs_file", lambda path, field_name: path)
    monkeypatch.setattr(op2_service, "_read_op2", lambda path: _FakeOp2())

    payload = op2_service.build_modal_import_payload(
        op2_path="fake.op2",
        all_subcases=True,
    )

    assert [mode["mode_no"] for mode in payload["modes"]] == [1, 2]
    assert payload["modes"][0]["frequency"] == 10.0
    assert payload["modes"][1]["frequency"] == 20.0
    assert payload["modes"][0]["nodes"][0]["extra_json"]["subcase_id"] == 10
    assert payload["modes"][1]["nodes"][0]["extra_json"]["subcase_id"] == 20
    assert payload["modes"][0]["nodes"][0]["extra_json"]["source_mode_no"] == 1
    assert any(item["code"] == "MODE_NO_RENUMBERED" for item in payload["warnings"])


def test_build_modal_import_payload_keeps_single_subcase_default(monkeypatch):
    monkeypatch.setattr(op2_service, "_abs_file", lambda path, field_name: path)
    monkeypatch.setattr(op2_service, "_read_op2", lambda path: _FakeOp2())

    payload = op2_service.build_modal_import_payload(op2_path="fake.op2")

    assert len(payload["modes"]) == 1
    assert payload["modes"][0]["mode_no"] == 1
    assert payload["modes"][0]["frequency"] == 10.0
    assert payload["modes"][0]["nodes"][0]["extra_json"]["subcase_id"] == 10
    assert all(item["code"] != "MODE_NO_RENUMBERED" for item in payload["warnings"])


def test_store_job_uses_all_subcases_when_subcase_id_is_omitted(monkeypatch):
    pytest.importorskip("fastapi")
    from webapi.routers import solver

    captured = {}

    def fake_build_modal_import_payload(**kwargs):
        captured.update(kwargs)
        return {"modes": [], "warnings": []}

    monkeypatch.setattr(solver, "build_modal_import_payload", fake_build_modal_import_payload)
    monkeypatch.setattr(
        solver,
        "import_fe_modal_results",
        lambda project_id, overwrite, modes: {
            "project_id": project_id,
            "overwrite": overwrite,
            "mode_count": len(modes),
        },
    )

    solver._store_op2_modal_job(
        project_id=1,
        op2_path="fake.op2",
        bdf_path=None,
        subcase_id=None,
        mode_numbers=None,
        overwrite=True,
        instance_name=None,
        part_name=None,
    )

    assert captured["all_subcases"] is True


def test_preview_route_auto_stores_modal_results_when_project_id_is_provided(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapi.routers import solver

    preview_calls = {}
    store_calls = {}

    def fake_preview_op2_modal(**kwargs):
        preview_calls.update(kwargs)
        return {
            "workflow": "op2_modal_preview",
            "source": {"op2_path": kwargs["op2_path"]},
            "subcases": [],
            "warnings": [],
        }

    def fake_store_op2_modal_job(**kwargs):
        store_calls.update(kwargs)
        return {
            "project_id": kwargs["project_id"],
            "mode_count": 8,
            "row_count": 1600,
        }

    monkeypatch.setattr(solver, "preview_op2_modal", fake_preview_op2_modal)
    monkeypatch.setattr(solver, "_store_op2_modal_job", fake_store_op2_modal_job)

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/import/op2/modal/preview",
        json={
            "project_id": 101,
            "op2_path": "D:/demo/model.op2",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    assert payload["data"]["workflow"] == "op2_modal_preview"
    assert payload["data"]["store"] == {
        "project_id": 101,
        "mode_count": 8,
        "row_count": 1600,
    }
    assert preview_calls["op2_path"] == "D:/demo/model.op2"
    assert preview_calls["mode_numbers"] is None
    assert store_calls == {
        "project_id": 101,
        "op2_path": "D:/demo/model.op2",
        "bdf_path": None,
        "subcase_id": None,
        "mode_numbers": None,
        "overwrite": True,
        "instance_name": None,
        "part_name": None,
    }


def test_preview_route_uses_default_op2_under_project_data_root_when_op2_path_is_empty(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapi.routers import solver

    preview_calls = {}
    store_calls = {}

    def fake_preview_op2_modal(**kwargs):
        preview_calls.update(kwargs)
        return {
            "workflow": "op2_modal_preview",
            "source": {"op2_path": kwargs["op2_path"]},
            "subcases": [],
            "warnings": [],
        }

    def fake_store_op2_modal_job(**kwargs):
        store_calls.update(kwargs)
        return {"project_id": kwargs["project_id"]}

    monkeypatch.setattr(solver, "preview_op2_modal", fake_preview_op2_modal)
    monkeypatch.setattr(solver, "_store_op2_modal_job", fake_store_op2_modal_job)
    monkeypatch.setattr(solver.settings, "data_root", "D:/app-data")

    app = FastAPI()
    app.include_router(solver.router)
    client = TestClient(app)

    response = client.post(
        "/import/op2/modal/preview",
        json={
            "project_id": 32,
            "op2_path": "",
        },
    )

    expected_path = os.path.abspath("D:/app-data/32/default_result_source.op2")
    assert response.status_code == 200
    assert preview_calls["op2_path"] == expected_path
    assert store_calls["op2_path"] == expected_path
