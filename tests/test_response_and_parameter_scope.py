import json

from services.model_update.analysis import inp_service


def test_select_optimization_parameters_for_update_marks_only_requested_rows(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        inp_service,
        "list_optimization_parameters",
        lambda project_id: {
            "parameters": [
                {"parameter_name": "P1", "usage_scope": ["SENSITIVITY"]},
                {"parameter_name": "P2", "usage_scope": ["SENSITIVITY", "UPDATE"]},
                {"parameter_name": "P3", "usage_scope": ["SENSITIVITY"]},
            ]
        },
    )
    monkeypatch.setattr(
        inp_service,
        "update_optimization_parameter_usage",
        lambda project_id, parameters: captured.update({"project_id": project_id, "parameters": parameters})
        or {"project_id": project_id, "updated_parameter_count": len(parameters)},
    )

    result = inp_service.select_optimization_parameters_for_update(
        7,
        ["P1", "P3"],
        replace_update_set=True,
    )

    assert result["updated_parameter_count"] == 3
    updates_by_name = {item["parameter_name"]: item["usage_scope"] for item in captured["parameters"]}
    assert updates_by_name["P1"] == ["SENSITIVITY", "UPDATE"]
    assert updates_by_name["P3"] == ["SENSITIVITY", "UPDATE"]
    assert updates_by_name["P2"] == ["SENSITIVITY"]


class _ModalSelectCursor:
    def __init__(self, select_rows):
        self.select_rows = [dict(row) for row in select_rows]
        self.executed = []
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = " ".join(str(sql).split())
        self.executed.append((self.last_sql, params))

    def fetchall(self):
        if "FROM t_mt_py_fem_modal_correlation" in self.last_sql:
            return [dict(row) for row in self.select_rows]
        return []

    def close(self):
        return None


class _ModalSelectConnection:
    def __init__(self, select_rows):
        self.cursor_obj = _ModalSelectCursor(select_rows)
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


def test_create_modal_match_response_catalog_entries_writes_selected_pairs(monkeypatch):
    fake_conn = _ModalSelectConnection(
        [
            {
                "test_mode_no": 1,
                "fem_mode_no": 2,
                "mac": 97.5,
                "freq_test": 32.5,
                "freq_fem": 31.8,
                "freq_error_ratio": -0.0215,
            }
        ]
    )
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.create_modal_match_response_catalog_entries(
        18,
        selected_pairs=[{"test_mode_no": 1, "fem_mode_no": 2}],
        response_types=["MODAL_FREQUENCY", "MODAL_MAC"],
        solver_scope=["SOL200"],
        overwrite=True,
    )

    delete_sql, delete_params = fake_conn.cursor_obj.executed[1]
    assert "DELETE FROM t_mt_py_fem_response_catalog" in delete_sql
    assert delete_params == (18, "MODAL_FREQUENCY", "MODAL_MAC")

    insert_calls = [item for item in fake_conn.cursor_obj.executed if "INSERT INTO t_mt_py_fem_response_catalog" in item[0]]
    assert len(insert_calls) == 2
    first_insert = insert_calls[0][1]
    second_insert = insert_calls[1][1]
    assert first_insert[2] == "FREQ_MODE_2"
    assert second_insert[2] == "MAC_MODE_2"
    assert json.loads(first_insert[15]) == ["SOL200"]
    assert json.loads(second_insert[17])["mac"] == 97.5
    assert result["response_count"] == 2
    assert fake_conn.committed is True


class _ModalFrequencyOptionsCursor:
    def __init__(self, fem_rows):
        self.fem_rows = [dict(row) for row in fem_rows]
        self.executed = []
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = " ".join(str(sql).split())
        self.executed.append((self.last_sql, params))

    def fetchall(self):
        if "FROM t_mt_py_fem_modal_result" in self.last_sql:
            return [dict(row) for row in self.fem_rows]
        return []

    def close(self):
        return None


class _ModalFrequencyOptionsConnection:
    def __init__(self, fem_rows):
        self.cursor_obj = _ModalFrequencyOptionsCursor(fem_rows)

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_get_modal_frequency_response_options_returns_fem_modes(monkeypatch):
    fake_conn = _ModalFrequencyOptionsConnection(
        [
            {"mode_no": 1, "frequency": 10.2},
            {"mode_no": 2, "frequency": 20.5},
        ]
    )
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.get_modal_frequency_response_options(18, "FEM")

    assert result["response_source"] == "FEM"
    assert result["summary"]["row_count"] == 2
    assert result["columns"][0]["title"] == "阶次"
    assert result["columns"][1]["title"] == "频率(Hz)"
    assert result["rows"][0]["order"] == 1
    assert result["rows"][1]["frequency_hz"] == 20.5


def test_get_modal_frequency_response_options_returns_unique_test_matches(monkeypatch):
    monkeypatch.setattr(
        inp_service,
        "_ensure_modal_correlation_rows",
        lambda project_id: [
            {
                "test_mode_no": 1,
                "fem_mode_no": 2,
                "mac": 95.0,
                "freq_test": 11.0,
                "freq_fem": 10.8,
                "freq_error_ratio": -0.018,
            },
            {
                "test_mode_no": 2,
                "fem_mode_no": 2,
                "mac": 90.0,
                "freq_test": 21.0,
                "freq_fem": 10.8,
                "freq_error_ratio": -0.486,
            },
            {
                "test_mode_no": 2,
                "fem_mode_no": 3,
                "mac": 92.0,
                "freq_test": 21.0,
                "freq_fem": 20.9,
                "freq_error_ratio": -0.005,
            },
        ],
    )

    result = inp_service.get_modal_frequency_response_options(18, "TEST")

    assert result["response_source"] == "TEST"
    assert result["summary"]["row_count"] == 2
    assert [item["title"] for item in result["columns"]] == ["试验频率", "试验阶次", "计算频率", "计算阶次"]
    assert result["rows"][0]["test_order"] == 1
    assert result["rows"][0]["fem_order"] == 2
    assert result["rows"][1]["test_order"] == 2
    assert result["rows"][1]["fem_order"] == 3
