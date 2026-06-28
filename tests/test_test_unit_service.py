from services.model_update.analysis import project_config_service, test_unit_service


class _UnitCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None
        self.rowcount = 0
        self.executed = []
        self.config_row = {
            "pid": 101,
            "test_model_x": 1.0,
            "test_model_y": 2.0,
            "test_model_z": 3.0,
            "fem_model_x": None,
            "fem_model_y": None,
            "fem_model_z": None,
            "coefficients_json": "{\"dynamic_displacement_display_scale\": 1.0, \"static_displacement_display_scale\": 1.0}",
            "extra_json": "{\"test_unit_system\": \"MKS\"}",
        }

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        self.executed.append((self.last_sql, params))
        if "UPDATE t_mt_py_test_node" in self.last_sql:
            self.rowcount = 3
        elif "UPDATE t_mt_measuring_point_info" in self.last_sql:
            self.rowcount = 2
        elif "INSERT INTO t_mt_py_project_config" in self.last_sql:
            self.config_row = {
                "pid": int(params[0]),
                "test_model_x": params[1],
                "test_model_y": params[2],
                "test_model_z": params[3],
                "fem_model_x": params[4],
                "fem_model_y": params[5],
                "fem_model_z": params[6],
                "coefficients_json": params[7],
                "extra_json": params[8],
            }
            self.rowcount = 1
        else:
            self.rowcount = 0

    def fetchone(self):
        if "FROM t_mt_py_project_config" in self.last_sql:
            return dict(self.config_row)
        return None

    def fetchall(self):
        if "FROM t_mt_py_test_node" in self.last_sql and "ORDER BY nid" in self.last_sql:
            return [
                {"x": 1000.0, "y": 2000.0, "z": 3000.0},
                {"x": 4000.0, "y": 5000.0, "z": 6000.0},
            ]
        return []

    def close(self):
        return None


class _UnitConnection:
    def __init__(self):
        self.cursor_obj = _UnitCursor()
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


def test_get_test_display_scale_factors_returns_defaults(monkeypatch):
    monkeypatch.setattr(project_config_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(project_config_service, "get_connection", lambda: _UnitConnection())

    result = project_config_service.get_test_display_scale_factors(101)

    assert result == {"dynamic": 1.0, "static": 1.0}


def test_convert_test_unit_system_updates_coordinates_and_display_scales(monkeypatch):
    fake_conn = _UnitConnection()
    monkeypatch.setattr(test_unit_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(test_unit_service, "get_connection", lambda: fake_conn)

    result = test_unit_service.convert_test_unit_system(
        project_id=101,
        from_unit="MKS",
        to_unit="MMKS",
    )

    assert fake_conn.committed is True
    assert result["scale"] == 1000.0
    assert result["test_node_rows_updated"] == 3
    assert result["measuring_point_rows_updated"] == 2
    assert result["display_scales"] == {"dynamic": 1000.0, "static": 1000.0}
    executed_sql = "\n".join(sql for sql, _ in fake_conn.cursor_obj.executed)
    assert "UPDATE t_mt_py_test_node" in executed_sql
    assert "UPDATE t_mt_measuring_point_info" in executed_sql
