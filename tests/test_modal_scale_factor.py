import numpy as np

from services.model_update.analysis import fem_correlation_service


def test_compute_dac_dsf_includes_modal_scale_factor():
    test_vec = np.array([2.0 + 0.0j, 4.0 + 0.0j], dtype=np.complex128)
    fem_vec = np.array([1.0 + 0.0j, 2.0 + 0.0j], dtype=np.complex128)

    metrics = fem_correlation_service._compute_dac_dsf(test_vec, fem_vec)

    assert metrics["mac"] == 100.0
    assert metrics["dsf"] == 0.5
    assert metrics["msf"] == 2.0


class _MetricCursor:
    def __init__(self):
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = str(sql)

    def fetchall(self):
        if "FROM t_mt_py_fem_modal_correlation" in self._last_sql:
            return [
                {"test_mode_no": 1, "fem_mode_no": 7, "mac": 97.3, "msf": 2.25},
                {"test_mode_no": 2, "fem_mode_no": 7, "mac": 8.1, "msf": 0.5},
                {"test_mode_no": 1, "fem_mode_no": 8, "mac": 10.2, "msf": 1.75},
                {"test_mode_no": 2, "fem_mode_no": 8, "mac": 95.6, "msf": 1.0},
            ]
        return []

    def close(self):
        return None


class _MetricConnection:
    def cursor(self, dictionary=False):
        return _MetricCursor()

    def close(self):
        return None


def test_modal_scale_factor_table_payload_matches_mac_table_shape(monkeypatch):
    monkeypatch.setattr(fem_correlation_service, "get_connection", lambda: _MetricConnection())

    payload = fem_correlation_service.get_modal_scale_factor_table_payload(1002)

    assert payload["project_id"] == 1002
    assert payload["row_mode_order"] == ["7", "8"]
    assert payload["column_mode_order"] == ["1", "2"]
    assert payload["rows"] == ["7", "8"]
    assert payload["column"] == ["1", "2"]
    assert payload["data"] == [
        {"1": 2.25, "2": 0.5},
        {"1": 1.75, "2": 1.0},
    ]
    assert payload["summary"]["point_count"] == 4
