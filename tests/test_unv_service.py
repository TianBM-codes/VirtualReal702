from services.model_update.importers import unv_service


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

    def cursor(self):
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

    assert clear_calls == [101]
    assert fake_conn.committed is True
    assert result["result_kind"] == "static"
    assert result["test_static_result_count"] == 1
    assert "INSERT INTO t_mt_py_test_modal_frequency" not in executed_sql
    assert len(static_inserts) == 1
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
