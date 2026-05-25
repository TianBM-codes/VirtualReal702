from services.model_update.analysis import inp_service
import numpy as np


class _WriteCursor:
    def __init__(self):
        self.executed = []
        self.fetchone_result = None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.fetchone_result

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
        if "FROM t_mt_py_fem_transform_operation" in sql:
            return [
                {
                    "transform_type": "fem",
                    "matrix4_json": "[[1,0,0,0],[0,1,0,0],[0,0,0,2],[0,0,0,1]]",
                },
                {
                    "transform_type": "test",
                    "matrix4_json": "[[1,0,0,3],[0,1,0,0],[0,0,1,0],[0,0,0,1]]",
                },
            ]
        if "SELECT id, measuring_point_name, x_position, y_position, z_position FROM t_mt_measuring_point_info" in sql:
            return [
                {"id": 1, "measuring_point_name": "WY1", "x_position": 1.0, "y_position": 2.0, "z_position": 3.0},
                {"id": 2, "measuring_point_name": "WY2", "x_position": 4.0, "y_position": 5.0, "z_position": 6.0},
            ]
        if "SELECT id, test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match" in sql:
            return [
                {"id": 10, "test_node_id": "WY1", "instance_name": "PART-1-1", "fem_node_label": 501},
                {"id": 11, "test_node_id": "WY2", "instance_name": "PART-1-1", "fem_node_label": 502},
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


class _ModalQueryCursor(_QueryCursor):
    def fetchall(self):
        sql = self.last_sql
        if "SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position FROM t_mt_py_test_node" in sql:
            return [
                {"test_node_id": "1001", "x_position": 1.0, "y_position": 2.0, "z_position": 3.0},
                {"test_node_id": "1002", "x_position": 4.0, "y_position": 5.0, "z_position": 6.0},
            ]
        if "SELECT id, test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match" in sql:
            return [
                {"id": 10, "test_node_id": "1001", "instance_name": "PART-1-1", "fem_node_label": 501},
                {"id": 11, "test_node_id": "1002", "instance_name": "PART-1-1", "fem_node_label": 502},
            ]
        return super().fetchall()


class _ModalQueryConnection:
    def __init__(self):
        self.cursor_obj = _ModalQueryCursor()

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


class _NodeMatchCursor(_WriteCursor):
    def __init__(self):
        super().__init__()
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.executed.append((self.last_sql, params))

    def fetchall(self):
        sql = self.last_sql
        if "SELECT measuring_point_name, x_position, y_position, z_position FROM t_mt_measuring_point_info" in sql:
            return [
                {"measuring_point_name": "WY1", "x_position": 1.0, "y_position": 2.0, "z_position": 3.0},
                {"measuring_point_name": "WY2", "x_position": 4.0, "y_position": 5.0, "z_position": 6.0},
            ]
        return []

    def fetchone(self):
        sql = self.last_sql
        if "FROM t_mt_py_fem_node_octree_cache" in sql:
            return {"cache_file_path": "fake_cache.npz"}
        return None


class _NodeMatchConnection(_WriteConnection):
    def __init__(self):
        self.cursor_obj = _NodeMatchCursor()
        self.committed = False
        self.rolled_back = False


class _DofMatchCursor(_WriteCursor):
    def __init__(self):
        super().__init__()
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.executed.append((self.last_sql, params))

    def fetchall(self):
        sql = self.last_sql
        if "SELECT test_node_id, instance_name, fem_node_label, transform_json FROM t_mt_py_fem_node_match" in sql:
            return [
                {
                    "test_node_id": "WY1",
                    "instance_name": "PART-1-1",
                    "fem_node_label": 501,
                    "transform_json": None,
                }
            ]
        if "SELECT id, measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info" in sql:
            return [
                {"id": 1, "measuring_point_name": "WY1", "sensor_type_id": 21},
                {"id": 2, "measuring_point_name": "TEMP1", "sensor_type_id": 99},
            ]
        if "SELECT id, measure_point_id, direction, data_operate FROM t_mt_channel_info" in sql:
            return [
                {"id": 101, "measure_point_id": 1, "direction": 1, "data_operate": "+"},
                {"id": 102, "measure_point_id": 1, "direction": 2, "data_operate": "-"},
            ]
        return []


class _DofMatchConnection(_WriteConnection):
    def __init__(self):
        self.cursor_obj = _DofMatchCursor()
        self.committed = False
        self.rolled_back = False


def test_evaluate_static_correlation_stores_dac_dsf(monkeypatch):
    fake_conn = _WriteConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(inp_service, "_ensure_static_node_matches", lambda project_id: False)
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
        ),
        (
            "UPDATE t_mt_work_condition_project SET consistency_status = %s WHERE project_id = %s",
            (1, 101),
        )
    ]


def test_evaluate_static_correlation_stores_analysis_error_rows(monkeypatch):
    fake_conn = _WriteConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(inp_service, "_ensure_static_node_matches", lambda project_id: False)
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
            "_analysis_error_rows": [
                {
                    "load_case_no": 1,
                    "result_no": 1,
                    "point_no": "WY1",
                    "node_no": "PART-1-1::1001",
                    "component_name": "UX",
                    "point_value": 1.0,
                    "initial_node_value": 1.1,
                    "initial_relative_error": 10.0,
                    "initial_abs_error": 0.1,
                    "sensor_type_id": None,
                }
            ],
        },
    )

    result = inp_service.evaluate_static_correlation(project_id=101, components=["UX"])

    assert result["dac"] == 98.5
    assert "_analysis_error_rows" not in result
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.cursor_obj.executed == [
        (
            "INSERT INTO t_mt_py_fem_dac_dsf (pid, dac, dsf) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE dac = VALUES(dac), dsf = VALUES(dsf)",
            (101, 98.5, 1.02),
        ),
        (
            "INSERT INTO t_mt_py_fem_analysis_error (pid, load_case_no, result_no, point_no, node_no, component_name, point_value, initial_node_value, initial_relative_error, initial_abs_error, sensor_type_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE node_no = VALUES(node_no), point_value = VALUES(point_value), initial_node_value = VALUES(initial_node_value), initial_relative_error = VALUES(initial_relative_error), initial_abs_error = VALUES(initial_abs_error), sensor_type_id = VALUES(sensor_type_id)",
            (101, 1, 1, "WY1", "PART-1-1::1001", "UX", 1.0, 1.1, 10.0, 0.1, None),
        ),
        (
            "UPDATE t_mt_work_condition_project SET consistency_status = %s WHERE project_id = %s",
            (1, 101),
        ),
    ]


def test_match_test_nodes_marks_space_match_status_done(monkeypatch):
    fake_conn = _NodeMatchConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service,
        "get_node_match_parameter_context",
        lambda project_id, cursor=None: {
            "tolerance": 1e-6,
            "maximum_node_point_distance": 0.25,
        },
    )
    monkeypatch.setattr(inp_service, "_load_octree_cache", lambda path: {
        "point_instances": ["PART-1-1", "PART-1-1"],
        "point_labels": [501, 502],
        "point_coords": np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float64),
    })
    monkeypatch.setattr(inp_service, "_cache_part_lookup", lambda cache: {
        ("PART-1-1", 501): "PART-1",
        ("PART-1-1", 502): "PART-1",
    })
    monkeypatch.setattr(
        inp_service,
        "_octree_nearest",
        lambda cache, point: (0, 0.0) if float(point[0]) < 2.0 else (1, 0.0),
    )
    monkeypatch.setattr(inp_service.os.path, "exists", lambda path: True)

    result = inp_service.match_test_nodes(project_id=101, auto_translate=False, overwrite=False)

    assert result["matched_points"] == 2
    assert result["tolerance"] == 1e-6
    assert result["maximum_node_point_distance"] == 0.25
    assert result["max_distance"] == 0.25
    assert fake_conn.committed is True
    assert fake_conn.cursor_obj.executed[-1] == (
        "UPDATE t_mt_work_condition_project SET space_match_status = %s WHERE project_id = %s",
        (1, 101),
    )


def test_match_test_dofs_uses_channel_direction_and_sign(monkeypatch):
    fake_conn = _DofMatchConnection()
    ensure_calls = []
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service,
        "_ensure_node_matches",
        lambda project_id: ensure_calls.append(project_id) or True,
    )
    monkeypatch.setattr(
        inp_service,
        "_get_latest_octree_meta",
        lambda cursor, project_id: {"cache_file_path": "fake_cache.npz"},
    )
    monkeypatch.setattr(inp_service, "_load_octree_cache", lambda path: {"dummy": True})
    monkeypatch.setattr(inp_service, "_cache_part_lookup", lambda cache: {("PART-1-1", 501): "PART-1"})

    result = inp_service.match_test_dofs(101)

    assert ensure_calls == [101]
    assert result["node_match_auto_created"] is True
    assert result["displacement_sensor_count"] == 1
    assert result["channel_count"] == 2
    assert result["dof_match_count"] == 2
    assert result["dof_matches_preview"] == [
        {
            "measure_point_id": 1,
            "channel_id": 101,
            "test_node_id": "WY1",
            "test_dof": "UX",
            "instance_name": "PART-1-1",
            "part_name": "PART-1",
            "fem_node_label": 501,
            "fem_dof": "U1",
            "direction": [1.0, 0.0, 0.0],
            "match_score": None,
            "transform": {},
        },
        {
            "measure_point_id": 1,
            "channel_id": 102,
            "test_node_id": "WY1",
            "test_dof": "UY",
            "instance_name": "PART-1-1",
            "part_name": "PART-1",
            "fem_node_label": 501,
            "fem_dof": "U2",
            "direction": [0.0, -1.0, 0.0],
            "match_score": None,
            "transform": {},
        },
    ]
    assert fake_conn.committed is True
    insert_rows = [
        item for item in fake_conn.cursor_obj.executed
        if item[0].startswith("INSERT INTO t_mt_py_fem_dof_match")
    ]
    assert insert_rows == [
        (
            "INSERT INTO t_mt_py_fem_dof_match (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, transform_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE instance_name = VALUES(instance_name), part_name = VALUES(part_name), fem_node_label = VALUES(fem_node_label), fem_dof = VALUES(fem_dof), direction_x = VALUES(direction_x), direction_y = VALUES(direction_y), direction_z = VALUES(direction_z), transform_json = VALUES(transform_json), created_at = CURRENT_TIMESTAMP",
            (101, "WY1", "UX", "PART-1-1", "PART-1", 501, "U1", 1.0, 0.0, 0.0, "{}"),
        ),
        (
            "INSERT INTO t_mt_py_fem_dof_match (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof, direction_x, direction_y, direction_z, transform_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE instance_name = VALUES(instance_name), part_name = VALUES(part_name), fem_node_label = VALUES(fem_node_label), fem_dof = VALUES(fem_dof), direction_x = VALUES(direction_x), direction_y = VALUES(direction_y), direction_z = VALUES(direction_z), transform_json = VALUES(transform_json), created_at = CURRENT_TIMESTAMP",
            (101, "WY1", "UY", "PART-1-1", "PART-1", 501, "U2", 0.0, -1.0, 0.0, "{}"),
        ),
    ]


def test_match_test_dofs_requires_node_match_for_all_displacement_sensors(monkeypatch):
    class _MissingNodeCursor(_DofMatchCursor):
        def fetchall(self):
            sql = self.last_sql
            if "SELECT test_node_id, instance_name, fem_node_label, transform_json FROM t_mt_py_fem_node_match" in sql:
                return []
            if "SELECT id, measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info" in sql:
                return [{"id": 1, "measuring_point_name": "WY1", "sensor_type_id": 21}]
            if "SELECT id, measure_point_id, direction, data_operate FROM t_mt_channel_info" in sql:
                return [{"id": 101, "measure_point_id": 1, "direction": 1, "data_operate": "+"}]
            return []

    class _MissingNodeConnection(_WriteConnection):
        def __init__(self):
            self.cursor_obj = _MissingNodeCursor()
            self.committed = False
            self.rolled_back = False

    fake_conn = _MissingNodeConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(inp_service, "_ensure_node_matches", lambda project_id: False)

    try:
        inp_service.match_test_dofs(101)
    except inp_service.ValidationError as exc:
        assert "节点匹配" in exc.message
    else:
        raise AssertionError("expected ValidationError")


def test_ensure_octree_cache_file_rebuilds_missing_cache(monkeypatch, tmp_path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    rebuilt_cache = tmp_path / "rebuilt.node_octree.npz"

    class _CacheCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((" ".join(sql.split()), params))

    cursor = _CacheCursor()
    monkeypatch.setattr(inp_service.os.path, "exists", lambda path: str(path) == str(inp_path))
    monkeypatch.setattr(inp_service, "parse_inp", lambda path, resolve_refs=True: {"dummy": True})
    monkeypatch.setattr(
        inp_service,
        "_collect_global_nodes",
        lambda model: {
            "point_labels": np.array([1, 2], dtype=np.int64),
            "entries": [{"instance_name": "INST"}, {"instance_name": "INST2"}],
            "bbox_min": np.array([0.0, 0.0, 0.0], dtype=np.float64),
            "bbox_max": np.array([1.0, 1.0, 1.0], dtype=np.float64),
        },
    )
    monkeypatch.setattr(inp_service, "_save_octree_cache", lambda **kwargs: str(rebuilt_cache))

    path = inp_service._ensure_octree_cache_file(
        cursor,
        24,
        {
            "source_file_path": str(inp_path),
            "cache_file_path": str(tmp_path / "missing.node_octree.npz"),
        },
    )

    assert path == str(rebuilt_cache.resolve())
    assert cursor.executed == [
        (
            "INSERT INTO t_mt_py_fem_node_octree_cache (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) ON DUPLICATE KEY UPDATE cache_file_path = VALUES(cache_file_path), node_count = VALUES(node_count), instance_count = VALUES(instance_count), bbox_min = VALUES(bbox_min), bbox_max = VALUES(bbox_max), updated_at = NOW()",
            (
                24,
                str(inp_path.resolve()),
                str(rebuilt_cache.resolve()),
                2,
                2,
                "[0.0, 0.0, 0.0]",
                "[1.0, 1.0, 1.0]",
            ),
        )
    ]


def test_ensure_static_node_matches_runs_auto_match_when_missing(monkeypatch):
    fake_conn = _WriteConnection()
    fake_conn.cursor_obj.fetchone_result = None
    auto_match_calls = []
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service,
        "match_test_nodes",
        lambda project_id, overwrite=False, **kwargs: auto_match_calls.append((project_id, overwrite, kwargs)) or {},
    )

    created = inp_service._ensure_static_node_matches(101)

    assert created is True
    assert auto_match_calls == [(101, False, {})]


def test_ensure_static_node_matches_skips_auto_match_when_existing(monkeypatch):
    fake_conn = _WriteConnection()
    fake_conn.cursor_obj.fetchone_result = {"test_node_id": "WY32"}
    auto_match_calls = []
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(
        inp_service,
        "match_test_nodes",
        lambda project_id, overwrite=False, **kwargs: auto_match_calls.append((project_id, overwrite, kwargs)) or {},
    )

    created = inp_service._ensure_static_node_matches(101)

    assert created is False
    assert auto_match_calls == []


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


def test_get_pair_node_point_result_uses_test_nodes_for_modal_projects(monkeypatch):
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: _ModalQueryConnection())
    monkeypatch.setattr(inp_service, "get_test_data_mode", lambda project_id, cursor=None: "modal_unv")
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
        "sensor_name": ["1001", "1002"],
        "node_xyz": [[0.1, 0.2, 0.3], [0.5, 0.3, 0.5]],
    }


def test_get_latest_octree_meta_normalizes_quoted_paths():
    class _MetaCursor:
        def execute(self, sql, params=None):
            return None

        def fetchone(self):
            return {
                "source_file_path": '"D:\\import\\code history\\static.inp"',
                "cache_file_path": '"D:\\import\\code history\\static.node_octree.npz"',
                "node_count": 1,
                "instance_count": 1,
                "bbox_min": None,
                "bbox_max": None,
                "updated_at": None,
            }

    meta = inp_service._get_latest_octree_meta(_MetaCursor(), 101)

    assert meta["source_file_path"] == r"D:\import\code history\static.inp"
    assert meta["cache_file_path"] == r"D:\import\code history\static.node_octree.npz"


def test_save_transform_operation_upserts_both_matrix4(monkeypatch):
    fake_conn = _WriteConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.save_transform_operation(
        project_id=101,
        matrix4_fem=[
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 2],
            [0, 0, 0, 1],
        ],
        matrix4_test=[
            [1, 0, 0, 3],
            [0, 1, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ],
    )

    assert result == {
        "project_id": 101,
        "matrix4_fem": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 2.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "matrix4_test": [
            [1.0, 0.0, 0.0, 3.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
    }
    assert fake_conn.committed is True
    assert fake_conn.cursor_obj.executed == [
        (
            "INSERT INTO t_mt_py_fem_transform_operation (pid, transform_type, matrix4_json) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE matrix4_json = VALUES(matrix4_json), updated_at = CURRENT_TIMESTAMP",
            (101, "fem", "[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 2.0], [0.0, 0.0, 0.0, 1.0]]"),
        ),
        (
            "INSERT INTO t_mt_py_fem_transform_operation (pid, transform_type, matrix4_json) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE matrix4_json = VALUES(matrix4_json), updated_at = CURRENT_TIMESTAMP",
            (101, "test", "[[1.0, 0.0, 0.0, 3.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]"),
        ),
    ]


def test_get_transform_auto_info_returns_both_matrix4(monkeypatch):
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: _QueryConnection())

    result = inp_service.get_transform_auto_info(project_id=101)

    assert result == {
        "matrix4_fem": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 2.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "matrix4_test": [
            [1.0, 0.0, 0.0, 3.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
    }
