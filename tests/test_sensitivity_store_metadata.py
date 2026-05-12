from services.model_update.analysis import sensitivity_service


class _FakeCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None
        self.lastrowid = 0
        self.executed = []
        self._analysis_run_rows = []
        self._response_rows = []
        self._parameter_rows = []
        self._result_rows = []

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        self.executed.append((self.last_sql, params))
        if "INSERT INTO t_mt_py_fem_analysis_run" in self.last_sql:
            self.lastrowid = 10
        elif "INSERT INTO t_mt_py_fem_response_def" in self.last_sql:
            self.lastrowid += 1
        elif "INSERT INTO t_mt_py_fem_parameter_def" in self.last_sql:
            self.lastrowid += 1

    def fetchall(self):
        sql = self.last_sql
        if "SELECT id FROM t_mt_py_fem_analysis_run" in sql:
            return list(self._analysis_run_rows)
        if "FROM t_mt_py_fem_response_def" in sql:
            return list(self._response_rows)
        if "FROM t_mt_py_fem_parameter_def" in sql:
            return list(self._parameter_rows)
        if "FROM t_mt_py_fem_sensitivity_result" in sql:
            return list(self._result_rows)
        return []

    def fetchone(self):
        if "FROM t_mt_py_fem_analysis_run" in self.last_sql:
            rows = list(self._analysis_run_rows)
            return rows[0] if rows else None
        return None

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, cursor_obj):
        self.cursor_obj = cursor_obj
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


def test_persist_sensitivity_matrix_stores_metadata_columns(monkeypatch):
    cursor = _FakeCursor()
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(sensitivity_service, "get_connection", lambda: conn)

    result = sensitivity_service._persist_sensitivity_matrix(
        project_id=7,
        batch_no="3",
        case_name="sol200_case",
        matrix_payload={
            "source": {
                "source_kind": "op2",
                "op2_path": "D:/demo/model.op2",
                "bdf_path": "D:/demo/model.bdf",
                "metadata_path": "D:/demo/model.bdf.sol200.json",
            },
            "response_rows": [
                {"response_name": "FREQ_MODE_1", "response_type": "FREQ", "mode_number": 1},
            ],
            "parameter_columns": [
                {
                    "parameter_name": "E1548",
                    "type": "E",
                    "material_id": 901,
                    "property_id": 801,
                    "element_id": 1548,
                    "source_material_id": 701,
                    "source_property_id": 601,
                    "initial": 210000.0,
                    "lower": 168000.0,
                    "upper": 252000.0,
                }
            ],
            "matrix": [[0.125]],
        },
    )

    analysis_run_insert = next(params for sql, params in cursor.executed if "INSERT INTO t_mt_py_fem_analysis_run" in sql)
    response_insert = next(params for sql, params in cursor.executed if "INSERT INTO t_mt_py_fem_response_def" in sql)
    parameter_insert = next(params for sql, params in cursor.executed if "INSERT INTO t_mt_py_fem_parameter_def" in sql)

    assert analysis_run_insert[3] == "op2"
    assert analysis_run_insert[4] == "D:/demo/model.op2"
    assert analysis_run_insert[6] == "D:/demo/model.bdf"
    assert analysis_run_insert[7] == "D:/demo/model.bdf.sol200.json"
    assert response_insert[4] == "FREQ"
    assert response_insert[5] == 1
    assert parameter_insert[4] == "E"
    assert parameter_insert[5:10] == (901, 801, 1548, 701, 601)
    assert parameter_insert[10:13] == (210000.0, 168000.0, 252000.0)
    assert conn.committed is True
    assert result["source"]["source_kind"] == "op2"


def test_load_stored_sensitivity_run_returns_metadata_columns(monkeypatch):
    cursor = _FakeCursor()
    cursor._analysis_run_rows = [{
        "id": 10,
        "project_id": 7,
        "case_name": "sol200_case",
        "run_no": "3",
        "source_kind": "op2",
        "op2_path": "D:/demo/model.op2",
        "matrix_path": None,
        "bdf_path": "D:/demo/model.bdf",
        "metadata_path": "D:/demo/model.bdf.sol200.json",
        "created_at": None,
    }]
    cursor._response_rows = [{
        "id": 11,
        "response_code": "R0001",
        "response_name": "FREQ_MODE_1",
        "response_type": "FREQ",
        "mode_number": 1,
        "unit": None,
        "seq_no": 1,
    }]
    cursor._parameter_rows = [{
        "id": 12,
        "param_code": "P0001",
        "param_name": "E1548",
        "param_type": "E",
        "material_id": 901,
        "property_id": 801,
        "element_id": 1548,
        "source_material_id": 701,
        "source_property_id": 601,
        "initial_value": 210000.0,
        "lower_bound": 168000.0,
        "upper_bound": 252000.0,
        "unit": None,
        "seq_no": 1,
    }]
    cursor._result_rows = [{
        "parameter_id": 12,
        "response_id": 11,
        "sensitivity_value": 0.125,
    }]
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(sensitivity_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(sensitivity_service, "get_connection", lambda: conn)

    result = sensitivity_service._load_stored_sensitivity_run(project_id=7, batch_no="3")

    assert result["source"]["source_kind"] == "op2"
    assert result["response_rows"][0]["response_type"] == "FREQ"
    assert result["response_rows"][0]["mode_number"] == 1
    assert result["parameter_columns"][0]["param_type"] == "E"
    assert result["parameter_columns"][0]["element_id"] == 1548
    assert result["matrix"] == [[0.125]]
