import json

from services.model_update.analysis import inp_service


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

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


def test_create_modal_frequency_response_catalog_from_match_writes_modal_frequency_rows(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service,
        "match_modal_modes",
        lambda project_id, **kwargs: {
            "rows": [
                {
                    "fem_mode_no": 2,
                    "test_mode_no": 3,
                    "mac": 95.0,
                    "freq_fem": 31.8,
                    "freq_test": 32.5,
                    "freq_error_ratio": -0.0215,
                }
            ]
        },
    )

    result = inp_service.create_modal_frequency_response_catalog_from_match(
        18,
        overwrite=True,
        mac_threshold=80,
        max_freq_error_ratio=0.15,
    )

    delete_sql, delete_params = fake_conn.cursor_obj.executed[0]
    assert "DELETE FROM t_mt_py_fem_response_catalog" in delete_sql
    assert delete_params == (18, "MODAL_FREQUENCY")

    insert_sql, insert_params = fake_conn.cursor_obj.executed[1]
    assert "INSERT INTO t_mt_py_fem_response_catalog" in insert_sql
    assert insert_params[2] == "FREQ_MODE_2"
    assert insert_params[3] == "MODAL_FREQUENCY"
    assert insert_params[14] == "auto_modal_match"
    assert json.loads(insert_params[15]) == ["SOL200", "BAYESIAN"]
    extra_json = json.loads(insert_params[17])
    assert extra_json["mode_number"] == 2
    assert extra_json["test_mode_no"] == 3
    assert result["response_count"] == 1
    assert result["mac_threshold"] == 80.0
    assert fake_conn.committed is True
