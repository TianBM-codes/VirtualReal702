import asyncio
import json

import numpy as np
import pytest

from services.model_update.analysis import inp_service
from services.model_update.analysis import fem_correlation_service

class _FakeCursor:
    def __init__(self):
        self.executed = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last_sql = sql

    def fetchall(self):
        if "FROM t_mt_py_fem_dof_match" in self._last_sql:
            return [
                {
                    "test_node_id": "T1",
                    "test_dof": "U1",
                    "instance_name": "BDF_MODEL",
                    "fem_node_label": 101,
                    "fem_dof": "U1",
                    "direction_x": 1.0,
                    "direction_y": 0.0,
                    "direction_z": 0.0,
                },
                {
                    "test_node_id": "T2",
                    "test_dof": "U1",
                    "instance_name": "BDF_MODEL",
                    "fem_node_label": 102,
                    "fem_dof": "U1",
                    "direction_x": 1.0,
                    "direction_y": 0.0,
                    "direction_z": 0.0,
                },
            ]
        return []

    def close(self):
        return None


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        return None


def test_compute_modal_correlation_persists_all_rows_and_tracks_threshold_matches(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(fem_correlation_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(fem_correlation_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(fem_correlation_service, "_require_modal_project", lambda project_id, cursor=None: None)
    monkeypatch.setattr(fem_correlation_service, "_resolve_modal_mac_mode", lambda cursor, project_id, test_modes: "real")
    monkeypatch.setattr(fem_correlation_service, "_load_test_modal_frequencies", lambda cursor, project_id: {1: 10.0})
    monkeypatch.setattr(
        fem_correlation_service,
        "_load_test_mode_vectors",
        lambda cursor, project_id: {
            1: {
                "T1": np.array([1.0 + 0.0j, 0.0 + 0.0j, 0.0 + 0.0j], dtype=np.complex128),
                "T2": np.array([1.0 + 0.0j, 0.0 + 0.0j, 0.0 + 0.0j], dtype=np.complex128),
            }
        },
    )
    monkeypatch.setattr(
        fem_correlation_service,
        "_load_fem_mode_vectors",
        lambda cursor, project_id: (
            {
                1: {
                    ("BDF_MODEL", 101): np.array([1.0, 0.0, 0.0], dtype=np.float64),
                    ("BDF_MODEL", 102): np.array([1.0, 0.0, 0.0], dtype=np.float64),
                },
                2: {
                    ("BDF_MODEL", 101): np.array([0.5, 0.0, 0.0], dtype=np.float64),
                    ("BDF_MODEL", 102): np.array([0.5, 0.0, 0.0], dtype=np.float64),
                },
            },
            {1: 10.1, 2: 11.0},
        ),
    )

    mac_by_call = iter([82.0, 65.0])

    def _fake_compute_dac_dsf(test_values, fem_values, mac_mode="real"):
        mac = next(mac_by_call)
        return {
            "dac": mac,
            "mac": mac,
            "dsf": 1.0,
            "scale_real": 1.0,
            "scale_imag": 0.0,
            "scale_phase_deg": 0.0,
            "test_norm": 1.0,
            "fem_norm": 1.0,
            "residual_norm": 0.0,
            "mac_mode": mac_mode,
            "cross_h_abs": 1.0,
            "cross_t_abs": 1.0,
            "self_test_h_abs": 1.0,
            "self_test_t_abs": 1.0,
            "self_fem_h_abs": 1.0,
            "self_fem_t_abs": 1.0,
        }

    monkeypatch.setattr(fem_correlation_service, "_compute_dac_dsf", _fake_compute_dac_dsf)

    result = inp_service.compute_modal_correlation(project_id=18, overwrite=False, mac_threshold=70.0)

    insert_calls = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "INSERT INTO t_mt_py_fem_modal_correlation" in sql
    ]
    assert len(insert_calls) == 2
    assert insert_calls[0][2] == 1
    assert insert_calls[1][2] == 2
    assert result["comparison_count"] == 2
    assert result["qualified_comparison_count"] == 1
    assert result["mac_threshold"] == 70.0
    assert result["results_preview"][0]["mac"] == 82.0
    assert result["results_preview"][1]["mac"] == 65.0
    assert result["qualified_results_preview"][0]["mac"] == 82.0
    assert fake_conn.committed is True


def test_compute_modal_correlation_api_forwards_mac_threshold(monkeypatch):
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

    captured = {}

    def _fake_compute_modal_correlation(**kwargs):
        captured.update(kwargs)
        return {"project_id": kwargs["project_id"], "comparison_count": 0}

    monkeypatch.setattr(legacy_app, "compute_modal_correlation", _fake_compute_modal_correlation)

    response = asyncio.run(
        legacy_app.compute_modal_correlation_api(
            _make_json_request("/correlation/modal/compute", {"project_id": 18, "mac_threshold": 75.0})
        )
    )

    assert response["ok"] is True
    assert captured["project_id"] == 18
    assert captured["overwrite"] is True
    assert captured["mac_threshold"] == 75.0
