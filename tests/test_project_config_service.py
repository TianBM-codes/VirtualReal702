import json

from services.model_update.analysis import project_config_service


class _ProjectConfigCursor:
    def __init__(self, row=None):
        self.row = row
        self.last_sql = ""
        self.last_params = None
        self.executed = []

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params
        self.executed.append((self.last_sql, params))

    def fetchone(self):
        if "FROM t_mt_py_project_config" in self.last_sql:
            return self.row
        return None

    def fetchall(self):
        return []

    def close(self):
        return None


class _ProjectConfigConnection:
    def __init__(self, row=None):
        self.cursor_obj = _ProjectConfigCursor(row=row)
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


def test_get_project_config_returns_defaults_when_missing(monkeypatch):
    fake_conn = _ProjectConfigConnection(row=None)
    monkeypatch.setattr(project_config_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(project_config_service, "get_connection", lambda: fake_conn)

    result = project_config_service.get_project_config(101)

    assert result == {
        "project_id": 101,
        "test_model_dims": {"x": None, "y": None, "z": None},
        "fem_model_dims": {"x": None, "y": None, "z": None},
        "coefficients": {},
        "extra_json": {},
    }


def test_upsert_project_config_merges_existing_values(monkeypatch):
    existing_row = {
        "pid": 101,
        "test_model_x": 10.0,
        "test_model_y": 20.0,
        "test_model_z": None,
        "fem_model_x": 11.0,
        "fem_model_y": 21.0,
        "fem_model_z": 31.0,
        "coefficients_json": json.dumps({"scale": 1.2}),
        "extra_json": json.dumps({"note": "seed"}),
    }
    fake_conn = _ProjectConfigConnection(row=existing_row)

    monkeypatch.setattr(project_config_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(project_config_service, "get_connection", lambda: fake_conn)

    result = project_config_service.upsert_project_config(
        101,
        test_model_dims={"z": 30.0},
        coefficients={"ratio": 0.95},
        extra_json={"updated_by": "test"},
    )

    assert result == {
        "project_id": 101,
        "test_model_dims": {"x": 10.0, "y": 20.0, "z": 30.0},
        "fem_model_dims": {"x": 11.0, "y": 21.0, "z": 31.0},
        "coefficients": {"scale": 1.2, "ratio": 0.95},
        "extra_json": {"note": "seed", "updated_by": "test"},
    }
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False


def test_save_dimension_helpers_convert_bounds_and_points(monkeypatch):
    calls = []

    monkeypatch.setattr(
        project_config_service,
        "upsert_project_config",
        lambda project_id, **kwargs: calls.append((project_id, kwargs)) or kwargs,
    )

    fem_result = project_config_service.save_fem_model_dimensions(
        101,
        bbox_min=[1.0, 2.0, 3.0],
        bbox_max=[4.5, 6.0, 9.25],
    )
    test_result = project_config_service.save_test_model_dimensions(
        101,
        points=[(0.0, 0.0, 0.0), (2.0, 5.0, 7.0)],
    )

    assert fem_result["fem_model_dims"] == {"x": 3.5, "y": 4.0, "z": 6.25}
    assert test_result["test_model_dims"] == {"x": 2.0, "y": 5.0, "z": 7.0}
    assert calls == [
        (101, {"fem_model_dims": {"x": 3.5, "y": 4.0, "z": 6.25}, "cursor": None}),
        (101, {"test_model_dims": {"x": 2.0, "y": 5.0, "z": 7.0}, "cursor": None}),
    ]


class _NodeMatchParamCursor(_ProjectConfigCursor):
    pass


class _NodeMatchParamConnection(_ProjectConfigConnection):
    def __init__(self, config_row=None):
        self.cursor_obj = _NodeMatchParamCursor(row=config_row)
        self.committed = False
        self.rolled_back = False


def test_get_node_match_parameter_context_reads_project_config_dims(monkeypatch):
    fake_conn = _NodeMatchParamConnection(
        config_row={
            "pid": 101,
            "test_model_x": 4.0,
            "test_model_y": 6.0,
            "test_model_z": 3.0,
            "fem_model_x": 8.0,
            "fem_model_y": 6.0,
            "fem_model_z": 9.0,
            "coefficients_json": None,
            "extra_json": None,
        },
    )
    monkeypatch.setattr(project_config_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(project_config_service, "get_connection", lambda: fake_conn)

    result = project_config_service.get_node_match_parameter_context(101)

    assert result == {
        "project_id": 101,
        "outer_contour_type": "axis_aligned_bbox",
        "test_model": {
            "bbox_min": None,
            "bbox_max": None,
            "dims": {"x": 4.0, "y": 6.0, "z": 3.0},
            "model_size": 6.0,
        },
        "fem_model": {
            "bbox_min": None,
            "bbox_max": None,
            "dims": {"x": 8.0, "y": 6.0, "z": 9.0},
            "model_size": 9.0,
        },
        "tolerance": 6e-06,
        "maximum_node_point_distance": 0.45,
    }


def test_get_node_match_parameter_context_falls_back_to_project_dims(monkeypatch):
    fake_conn = _NodeMatchParamConnection(
        config_row={
            "pid": 101,
            "test_model_x": 10.0,
            "test_model_y": 20.0,
            "test_model_z": 30.0,
            "fem_model_x": 11.0,
            "fem_model_y": 21.0,
            "fem_model_z": 31.0,
            "coefficients_json": None,
            "extra_json": None,
        },
    )
    monkeypatch.setattr(project_config_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(project_config_service, "get_connection", lambda: fake_conn)

    result = project_config_service.get_node_match_parameter_context(101)

    assert result["test_model"]["bbox_min"] is None
    assert result["fem_model"]["bbox_min"] is None
    assert result["test_model"]["dims"] == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert result["fem_model"]["dims"] == {"x": 11.0, "y": 21.0, "z": 31.0}
    assert result["tolerance"] == 3e-05
    assert result["maximum_node_point_distance"] == 1.55
