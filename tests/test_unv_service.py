from services.model_update.importers import unv_service


class _FakeCursor:
    def __init__(self):
        self.executed = []
        self.lastrowid = None
        self._measuring_point_seq = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "INSERT INTO t_mt_measuring_point_info" in sql:
            self._measuring_point_seq += 1
            self.lastrowid = self._measuring_point_seq

    def fetchone(self):
        return None

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


def _sample_nodes():
    return [
        {"nid": 1001, "ics": 0, "ocs": 0, "x": 1.0, "y": 2.0, "z": 3.0},
    ]


def _sample_elements():
    return [
        {
            "element_no": 1,
            "element_type": "LINE2",
            "point1": 1001,
            "point2": 1002,
            "point3": None,
            "point4": None,
        }
    ]


def test_import_unv_data_writes_static_results_to_static_table(monkeypatch):
    fake_conn = _FakeConnection()
    clear_calls = []

    def fake_clear(cursor, pid):
        clear_calls.append(pid)

    monkeypatch.setattr(unv_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(unv_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(unv_service, "clear_unv_tables", fake_clear)
    monkeypatch.setattr(
        unv_service,
        "parse_unv_file",
        lambda _: (
            _sample_nodes(),
            _sample_elements(),
            [
                {
                    "analysis_type": 1,
                    "data_type": 2,
                    "ndv": 6,
                    "load_case": 7,
                    "modal_number": 1,
                    "load_factor": 2.5,
                    "frequency": 0.0,
                    "damping": 0.0,
                    "displacements": {
                        1001: {
                            "real": (0.1, 0.2, 0.3),
                            "imag": (0.0, 0.0, 0.0),
                            "rotate": (0.01, 0.02, 0.03),
                        }
                    },
                }
            ],
            {"is_static": True, "is_real": True},
        ),
    )

    result = unv_service.import_unv_data(
        file_path="static.unv",
        project_id=101,
        file_id=202,
        clear_before_insert=True,
    )

    executed_sql = "\n".join(sql for sql, _ in fake_conn.cursor_obj.executed)
    static_inserts = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "INSERT INTO t_mt_py_test_static_result" in sql
    ]
    measuring_point_inserts = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "INSERT INTO t_mt_measuring_point_info" in sql
    ]
    measuring_point_updates = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "UPDATE t_mt_measuring_point_info" in sql
    ]

    assert clear_calls == [101]
    assert fake_conn.committed is True
    assert result["result_kind"] == "static"
    assert result["measuring_point_count"] == 1
    assert result["test_static_result_count"] == 1
    assert "INSERT INTO t_mt_py_test_modal_frequency" not in executed_sql
    assert len(static_inserts) == 1
    assert len(measuring_point_inserts) == 1
    assert measuring_point_inserts[0] == ("WY_PENDING_1001", 101, 21, 1.0, 2.0, 3.0, "LOCAL")
    assert measuring_point_updates == [("WY1", 1)]
    assert static_inserts[0][5:11] == (0.1, 0.2, 0.3, 0.01, 0.02, 0.03)


def test_import_unv_data_keeps_dynamic_modal_tables_for_modal_results(monkeypatch):
    fake_conn = _FakeConnection()

    monkeypatch.setattr(unv_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(unv_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(unv_service, "clear_unv_tables", lambda cursor, pid: None)
    monkeypatch.setattr(
        unv_service,
        "parse_unv_file",
        lambda _: (
            _sample_nodes(),
            _sample_elements(),
            [
                {
                    "analysis_type": 2,
                    "data_type": 2,
                    "ndv": 3,
                    "load_case": 7,
                    "modal_number": 1,
                    "frequency": 12.5,
                    "damping": 0.01,
                    "displacements": {
                        1001: {
                            "real": (0.1, 0.2, 0.3),
                            "imag": (0.0, 0.0, 0.0),
                        }
                    },
                }
            ],
            {"is_static": False, "is_real": True},
        ),
    )

    result = unv_service.import_unv_data(
        file_path="modal.unv",
        project_id=101,
        file_id=202,
        clear_before_insert=False,
    )

    executed_sql = "\n".join(sql for sql, _ in fake_conn.cursor_obj.executed)

    assert fake_conn.committed is True
    assert result["result_kind"] == "dynamic"
    assert result["test_static_result_count"] == 0
    assert "INSERT INTO t_mt_py_test_modal_frequency" in executed_sql
    assert "INSERT INTO t_mt_py_test_modal_shape_real" in executed_sql
    assert "INSERT INTO t_mt_py_test_static_result" not in executed_sql


def test_import_unv_data_saves_test_model_dimensions(monkeypatch):
    fake_conn = _FakeConnection()
    saved = []

    monkeypatch.setattr(unv_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(unv_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(unv_service, "clear_unv_tables", lambda cursor, pid: None)
    monkeypatch.setattr(
        unv_service,
        "parse_unv_file",
        lambda _: (
            [
                {"nid": 1001, "ics": 0, "ocs": 0, "x": 1.0, "y": 2.0, "z": 3.0},
                {"nid": 1002, "ics": 0, "ocs": 0, "x": 4.0, "y": 8.0, "z": 13.0},
            ],
            _sample_elements(),
            [],
            {"is_static": False, "is_real": True},
        ),
    )
    monkeypatch.setattr(unv_service, "_classify_unv_result", lambda message, test_modes: "dynamic")
    monkeypatch.setattr(unv_service, "_insert_dynamic_modal_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        unv_service,
        "save_test_model_dimensions",
        lambda **kwargs: saved.append(kwargs) or {"test_model_dims": {"x": 3.0, "y": 6.0, "z": 10.0}},
    )

    result = unv_service.import_unv_data(
        file_path="modal.unv",
        project_id=101,
        file_id=202,
        clear_before_insert=False,
    )

    assert fake_conn.committed is True
    assert saved == [
        {
            "project_id": 101,
            "points": [(1.0, 2.0, 3.0), (4.0, 8.0, 13.0)],
            "cursor": fake_conn.cursor_obj,
        }
    ]
    assert result["project_config"] == {"test_model_dims": {"x": 3.0, "y": 6.0, "z": 10.0}}


class _QueryCursor:
    def __init__(self, measuring_rows=None, static_rows=None, error_rows=None, test_node_rows=None):
        self.last_sql = ""
        self.last_params = None
        self.measuring_rows = measuring_rows or [
            {
                "id": 1,
                "measuring_point_name": "WY1",
                "sensor_type_id": 21,
                "x_position": 1.0,
                "y_position": 2.0,
                "z_position": 3.0,
            },
            {
                "id": 2,
                "measuring_point_name": "WY2",
                "sensor_type_id": 21,
                "x_position": 4.0,
                "y_position": 5.0,
                "z_position": 6.0,
            },
        ]
        self.static_rows = static_rows or [
            {
                "id": 9,
                "point": 1001,
                "ux": 0.5,
                "uy": -0.25,
                "uz": 1.0,
            }
        ]
        self.error_rows = error_rows or []
        self.test_node_rows = test_node_rows or [
            {"test_node_id": "1001", "x_position": 1.0, "y_position": 2.0, "z_position": 3.0},
            {"test_node_id": "1002", "x_position": 4.0, "y_position": 5.0, "z_position": 6.0},
        ]

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params

    def fetchall(self):
        sql = self.last_sql
        params = self.last_params
        if "FROM t_mt_py_test_node" in sql and "ORDER BY nid" in sql:
            return list(self.test_node_rows)
        if "FROM t_mt_measuring_point_info" in sql and "ORDER BY id" in sql:
            return list(self.measuring_rows)
        if "FROM t_mt_py_test_static_result" in sql and "WHERE pid = %s AND id = %s" in sql:
            assert params == (101, 9)
            return list(self.static_rows)
        if "FROM t_mt_py_fem_analysis_error" in sql:
            return list(self.error_rows)
        return []

    def fetchone(self):
        sql = self.last_sql
        params = self.last_params
        if "FROM t_mt_py_test_node" in sql:
            assert params == (101, "1001")
            return {"nid": "1001", "x": 1.0, "y": 2.0, "z": 3.0}
        if "FROM t_mt_measuring_point_info" in sql and "LIMIT 1" in sql:
            return dict(self.measuring_rows[0])
        return None

    def close(self):
        return None


class _QueryConnection:
    def __init__(self, measuring_rows=None, static_rows=None, error_rows=None, test_node_rows=None):
        self.cursor_obj = _QueryCursor(
            measuring_rows=measuring_rows,
            static_rows=static_rows,
            error_rows=error_rows,
            test_node_rows=test_node_rows,
        )

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_get_sensor_positions_returns_expected_shape(monkeypatch):
    monkeypatch.setattr(unv_service, "get_connection", lambda: _QueryConnection())

    result = unv_service.get_sensor_positions(101)

    assert result == [
        {"sensor_label": "WY1", "sensor_type": "位移", "sensor_pos": [1.0, 2.0, 3.0]},
        {"sensor_label": "WY2", "sensor_type": "位移", "sensor_pos": [4.0, 5.0, 6.0]},
    ]


def test_get_sensor_positions_uses_test_nodes_for_modal_projects(monkeypatch):
    monkeypatch.setattr(unv_service, "get_connection", lambda: _QueryConnection())
    monkeypatch.setattr(unv_service, "get_test_data_mode", lambda project_id, cursor=None: "modal_unv")

    result = unv_service.get_sensor_positions(101)

    assert result == [
        {"sensor_label": "1001", "sensor_type": "浣嶇Щ", "sensor_pos": [1.0, 2.0, 3.0]},
        {"sensor_label": "1002", "sensor_type": "浣嶇Щ", "sensor_pos": [4.0, 5.0, 6.0]},
    ]


def test_get_sensor_relative_error_uses_updated_then_initial(monkeypatch):
    monkeypatch.setattr(
        unv_service,
        "get_connection",
        lambda: _QueryConnection(
            error_rows=[
                {
                    "load_case_no": 1,
                    "result_no": 1,
                    "point_no": "WY1",
                    "component_name": "UX",
                    "initial_relative_error": 12.0,
                    "updated_relative_error": 8.0,
                },
                {
                    "load_case_no": 1,
                    "result_no": 1,
                    "point_no": "WY2",
                    "component_name": "UY",
                    "initial_relative_error": 5.0,
                    "updated_relative_error": None,
                },
            ],
        ),
    )

    result = unv_service.get_sensor_relative_error(101)

    assert result[0]["sensor_label"] == "WY1"
    assert result[0]["sensor_pos"] == [1.0, 2.0, 3.0]
    assert result[0]["e_value"] == 8.0
    assert result[1]["sensor_label"] == "WY2"
    assert result[1]["sensor_pos"] == [4.0, 5.0, 6.0]
    assert result[1]["e_value"] == 5.0


def test_get_sensor_relative_error_prefers_latest_case_and_uy_component(monkeypatch):
    monkeypatch.setattr(
        unv_service,
        "get_connection",
        lambda: _QueryConnection(
            error_rows=[
                {
                    "load_case_no": 1,
                    "result_no": 1,
                    "point_no": "WY1",
                    "component_name": "UY",
                    "initial_relative_error": 9.0,
                    "updated_relative_error": 7.0,
                },
                {
                    "load_case_no": 2,
                    "result_no": 1,
                    "point_no": "WY1",
                    "component_name": "UX",
                    "initial_relative_error": 6.0,
                    "updated_relative_error": 4.0,
                },
                {
                    "load_case_no": 2,
                    "result_no": 1,
                    "point_no": "WY1",
                    "component_name": "UY",
                    "initial_relative_error": 3.0,
                    "updated_relative_error": 2.0,
                },
            ],
        ),
    )

    result = unv_service.get_sensor_relative_error(101)

    assert result[0]["e_value"] == 2.0
    assert result[1]["e_value"] is None


def test_get_sensor_relative_error_matches_point_no_by_sensor_id(monkeypatch):
    monkeypatch.setattr(
        unv_service,
        "get_connection",
        lambda: _QueryConnection(
            error_rows=[
                {
                    "load_case_no": 1,
                    "result_no": 1,
                    "point_no": "1",
                    "component_name": "UY",
                    "initial_relative_error": 11.0,
                    "updated_relative_error": None,
                }
            ],
        ),
    )

    result = unv_service.get_sensor_relative_error(101)

    assert result[0]["e_value"] == 11.0
    assert result[1]["e_value"] is None


def test_get_deform_sensor_positions_scales_static_displacement(monkeypatch):
    monkeypatch.setattr(unv_service, "get_connection", lambda: _QueryConnection())

    result = unv_service.get_deform_sensor_positions(
        project_id=101,
        static_result_id=9,
        scale=2.0,
    )

    assert result == [
        {"sensor_label": "WY1", "sensor_type": "位移", "sensor_pos": [2.0, 1.5, 5.0]},
    ]
