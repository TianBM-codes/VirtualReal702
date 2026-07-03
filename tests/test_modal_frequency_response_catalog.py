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
    monkeypatch.setattr(inp_service._response, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service._response, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service._response,
        "_match_modal_modes_for_response",
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
    assert "DELETE FROM t_mt_py_fem_dynamic_response_catalog" in delete_sql
    assert delete_params == (18, "MODAL_FREQUENCY")

    insert_sql, insert_params = fake_conn.cursor_obj.executed[1]
    assert "INSERT INTO t_mt_py_fem_dynamic_response_catalog" in insert_sql
    assert insert_params[2] == "FREQ_MODE_2"
    assert insert_params[3] == "MODAL_FREQUENCY"
    assert insert_params[12] == 0.05
    assert insert_params[15] == "auto_modal_match"
    assert json.loads(insert_params[16]) == ["SOL200", "BAYESIAN"]
    extra_json = json.loads(insert_params[18])
    assert extra_json["mode_number"] == 2
    assert extra_json["test_mode_no"] == 3
    assert result["response_count"] == 1
    assert result["mac_threshold"] == 80.0
    assert result["scatter"] == 0.05
    assert fake_conn.committed is True


def test_create_modal_frequency_response_catalog_from_fem_writes_manual_target_values(monkeypatch):
    class _DictCursor(_FakeCursor):
        def __init__(self):
            super().__init__()
            self.fetchone_result = {"max_seq_no": 0}
            self.fetchall_result = []

        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "SELECT mode_no, frequency" in sql:
                self.fetchall_result = [
                    {"mode_no": 1, "frequency": 10.5},
                    {"mode_no": 3, "frequency": 30.5},
                ]
            if "SELECT COALESCE(MAX(seq_no), 0) AS max_seq_no" in sql:
                self.fetchall_result = []

        def fetchall(self):
            return list(self.fetchall_result)

        def fetchone(self):
            return dict(self.fetchone_result)

    class _DictConnection(_FakeConnection):
        def __init__(self):
            super().__init__()
            self.cursor_obj = _DictCursor()

    fake_conn = _DictConnection()
    monkeypatch.setattr(inp_service._response, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service._response, "get_connection", lambda: fake_conn)

    result = inp_service.create_modal_frequency_response_catalog_from_fem(
        18,
        mode_numbers=[1, 3],
        target_frequencies={1: 11.0, 3: 33.0},
        overwrite=True,
    )

    insert_rows = [
        params for sql, params in fake_conn.cursor_obj.executed
        if "INSERT INTO t_mt_py_fem_dynamic_response_catalog" in sql
    ]
    assert len(insert_rows) == 2
    first_extra = json.loads(insert_rows[0][18])
    second_extra = json.loads(insert_rows[1][18])
    assert first_extra["target_source"] == "MANUAL"
    assert first_extra["target_value"] == 11.0
    assert second_extra["target_value"] == 33.0
    assert result["target_frequencies"] == {1: 11.0, 3: 33.0}
    assert fake_conn.committed is True
