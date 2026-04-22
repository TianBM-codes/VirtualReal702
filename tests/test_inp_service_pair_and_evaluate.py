from services.model_update.analysis import inp_service


class _WriteCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def close(self):
        return None


class _WriteConnection:
    def __init__(self):
        self.cursor_obj = _WriteCursor()
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


class _QueryCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params

    def fetchall(self):
        sql = self.last_sql
        if "SELECT nid, x, y, z FROM t_mt_py_test_node" in sql:
            return [
                {"nid": "1001", "x": 1.0, "y": 2.0, "z": 3.0},
                {"nid": "1002", "x": 4.0, "y": 5.0, "z": 6.0},
            ]
        if "SELECT id, measuring_point_name, x_position, y_position, z_position FROM t_mt_measuring_point_info" in sql:
            return [
                {"id": 1, "measuring_point_name": "WY1", "x_position": 1.0, "y_position": 2.0, "z_position": 3.0},
                {"id": 2, "measuring_point_name": "WY2", "x_position": 4.0, "y_position": 5.0, "z_position": 6.0},
            ]
        if "SELECT id, test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match" in sql:
            return [
                {"id": 10, "test_node_id": "1001", "instance_name": "PART-1-1", "fem_node_label": 501},
                {"id": 11, "test_node_id": "1002", "instance_name": "PART-1-1", "fem_node_label": 502},
            ]
        return []

    def fetchone(self):
        sql = self.last_sql
        if "FROM t_mt_py_fem_transform_operation" in sql:
            return {
                "transform_type": "fem",
                "matrix4_json": "[[1,0,0,0],[0,1,0,0],[0,0,0,2],[0,0,0,1]]",
            }
        return None

    def close(self):
        return None


class _QueryConnection:
    def __init__(self):
        self.cursor_obj = _QueryCursor()

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_evaluate_static_correlation_stores_dac_dsf(monkeypatch):
    fake_conn = _WriteConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service,
        "compute_static_correlation",
        lambda **kwargs: {
            "project_id": 101,
            "load_case_no": 1,
            "result_no": 1,
            "components": ["UX"],
            "dac": 98.5,
            "dsf": 1.02,
        },
    )

    result = inp_service.evaluate_static_correlation(project_id=101, components=["UX"])

    assert result["dac"] == 98.5
    assert result["dsf"] == 1.02
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.cursor_obj.executed == [
        (
            "INSERT INTO t_mt_py_fem_dac_dsf (pid, dac, dsf) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE dac = VALUES(dac), dsf = VALUES(dsf)",
            (101, 98.5, 1.02),
        )
    ]


def test_get_pair_node_point_result_keeps_sensor_and_node_order(monkeypatch):
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: _QueryConnection())
    monkeypatch.setattr(
        inp_service,
        "_get_latest_octree_meta",
        lambda cursor, project_id: {"cache_file_path": "fake_cache.npz"},
    )
    monkeypatch.setattr(
        inp_service,
        "_load_octree_cache",
        lambda path: {
            "point_instances": ["PART-1-1", "PART-1-1"],
            "point_labels": [501, 502],
            "point_coords": [
                [0.1, 0.2, 0.3],
                [0.5, 0.3, 0.5],
            ],
        },
    )

    result = inp_service.get_pair_node_point_result(101)

    assert result == {
        "sensor_name": ["WY1", "WY2"],
        "node_xyz": [[0.1, 0.2, 0.3], [0.5, 0.3, 0.5]],
    }


def test_save_transform_operation_upserts_matrix4(monkeypatch):
    fake_conn = _WriteConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.save_transform_operation(
        project_id=101,
        transform_type="fem",
        matrix4=[
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 2],
            [0, 0, 0, 1],
        ],
    )

    assert result == {
        "project_id": 101,
        "type": "fem",
        "matrix4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 2.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
    }
    assert fake_conn.committed is True
    assert fake_conn.cursor_obj.executed == [
        (
            "INSERT INTO t_mt_py_fem_transform_operation (pid, transform_type, matrix4_json) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE matrix4_json = VALUES(matrix4_json), updated_at = CURRENT_TIMESTAMP",
            (101, "fem", "[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 2.0], [0.0, 0.0, 0.0, 1.0]]"),
        )
    ]


def test_get_transform_auto_info_returns_matrix4(monkeypatch):
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: _QueryConnection())

    result = inp_service.get_transform_auto_info(project_id=101, transform_type="fem")

    assert result == {
        "type": "fem",
        "matrix4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 2.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
    }
