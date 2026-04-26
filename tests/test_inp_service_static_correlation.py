from services.model_update.analysis import inp_service
import json


class _FakeCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params

    def fetchall(self):
        sql = self.last_sql
        if "SELECT DISTINCT load_case_no, result_no FROM t_mt_py_test_static_result" in sql:
            return [
                {"load_case_no": 1, "result_no": 1},
                {"load_case_no": 2, "result_no": 1},
            ]
        if "SELECT DISTINCT load_case_no FROM t_mt_py_fem_static_result" in sql:
            return [{"load_case_no": 1}]
        if "SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json FROM t_mt_py_test_static_result" in sql:
            return [
                {
                    "point": 501,
                    "ux": 1.0,
                    "uy": None,
                    "uz": None,
                    "rx": 99.0,
                    "ry": None,
                    "rz": None,
                    "load_factor": 1.0,
                    "extra_json": None,
                },
                {
                    "point": 502,
                    "ux": 2.0,
                    "uy": None,
                    "uz": None,
                    "rx": 88.0,
                    "ry": None,
                    "rz": None,
                    "load_factor": 1.0,
                    "extra_json": None,
                },
            ]
        if "SELECT load_case_no, instance_name, part_name, fem_node_label," in sql:
            return [
                {
                    "load_case_no": 1,
                    "instance_name": "PART-1-1",
                    "part_name": "PART-1",
                    "fem_node_label": 1001,
                    "u1": 1.0,
                    "u2": None,
                    "u3": None,
                    "ur1": 0.0,
                    "ur2": None,
                    "ur3": None,
                    "extra_json": None,
                },
                {
                    "load_case_no": 1,
                    "instance_name": "PART-1-1",
                    "part_name": "PART-1",
                    "fem_node_label": 1002,
                    "u1": 2.0,
                    "u2": None,
                    "u3": None,
                    "ur1": 0.0,
                    "ur2": None,
                    "ur3": None,
                    "extra_json": None,
                },
            ]
        if "SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match" in sql:
            return [
                {"test_node_id": "501", "instance_name": "PART-1-1", "fem_node_label": 1001},
                {"test_node_id": "502", "instance_name": "PART-1-1", "fem_node_label": 1002},
            ]
        return []

    def close(self):
        return None


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_compute_static_correlation_reuses_dac_dsf_with_node_matches(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.compute_static_correlation(
        project_id=101,
        components=["UX"],
        include_rotations=False,
    )

    assert result["project_id"] == 101
    assert result["load_case_no"] == 1
    assert result["result_no"] == 1
    assert result["components"] == ["UX"]
    assert result["aligned_point_count"] == 2
    assert result["value_count"] == 2
    assert result["dac"] == 100.0
    assert result["dsf"] == 1.0
    assert result["component_value_counts"] == {"UX": 2}
    assert result["analysis_error_row_count"] == 2
    assert result["analysis_error_preview"] == [
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "501",
            "node_no": "PART-1-1::1001",
            "component_name": "UX",
            "point_value": 1.0,
            "initial_node_value": 1.0,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "502",
            "node_no": "PART-1-1::1002",
            "component_name": "UX",
            "point_value": 2.0,
            "initial_node_value": 2.0,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
    ]


def test_compute_static_correlation_defaults_to_translations_only(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.compute_static_correlation(project_id=101)

    assert result["components"] == ["UX", "UY", "UZ"]
    assert result["value_count"] == 2
    assert result["dac"] == 100.0
    assert result["dsf"] == 1.0
    assert result["component_value_counts"] == {"UX": 2, "UY": 0, "UZ": 0}


class _StaticJsonCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params

    def fetchall(self):
        sql = self.last_sql
        if "SELECT DISTINCT load_case_no, result_no FROM t_mt_py_test_static_result" in sql:
            return []
        if "SELECT sensor_type, data FROM t_mt_static_test_data" in sql:
            return [
                {
                    "sensor_type": "位移",
                    "data": json.dumps(
                        [
                            {"sensor_label": "WY1", "value": 1.5},
                            {"sensor_label": "WY2", "value": 2.5},
                        ],
                        ensure_ascii=False,
                    ),
                }
            ]
        if "SELECT measuring_point_name, sensor_type_id FROM t_mt_measuring_point_info" in sql:
            return [
                {"measuring_point_name": "WY1", "sensor_type_id": 21},
                {"measuring_point_name": "WY2", "sensor_type_id": 21},
            ]
        if "SELECT DISTINCT load_case_no FROM t_mt_py_fem_static_result" in sql:
            return [{"load_case_no": 1}]
        if "SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json FROM t_mt_py_test_static_result" in sql:
            return []
        if "SELECT load_case_no, instance_name, part_name, fem_node_label," in sql:
            return [
                {
                    "load_case_no": 1,
                    "instance_name": "PART-1-1",
                    "part_name": "PART-1",
                    "fem_node_label": 1001,
                    "u1": 0.0,
                    "u2": 1.5,
                    "u3": 0.0,
                    "ur1": None,
                    "ur2": None,
                    "ur3": None,
                    "extra_json": None,
                },
                {
                    "load_case_no": 1,
                    "instance_name": "PART-1-1",
                    "part_name": "PART-1",
                    "fem_node_label": 1002,
                    "u1": 0.0,
                    "u2": 2.5,
                    "u3": 0.0,
                    "ur1": None,
                    "ur2": None,
                    "ur3": None,
                    "extra_json": None,
                },
            ]
        if "SELECT test_node_id, instance_name, fem_node_label FROM t_mt_py_fem_node_match" in sql:
            return [
                {"test_node_id": "WY1", "instance_name": "PART-1-1", "fem_node_label": 1001},
                {"test_node_id": "WY2", "instance_name": "PART-1-1", "fem_node_label": 1002},
            ]
        return []

    def close(self):
        return None


class _StaticJsonConnection:
    def __init__(self):
        self.cursor_obj = _StaticJsonCursor()

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_compute_static_correlation_reads_latest_static_test_data_json(monkeypatch):
    fake_conn = _StaticJsonConnection()
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.compute_static_correlation(project_id=202)

    assert result["project_id"] == 202
    assert result["load_case_no"] == 1
    assert result["result_no"] == 1
    assert result["components"] == ["UX", "UY", "UZ"]
    assert result["aligned_point_count"] == 2
    assert result["value_count"] == 6
    assert result["dac"] == 100.0
    assert result["dsf"] == 1.0
    assert result["component_value_counts"] == {"UX": 2, "UY": 2, "UZ": 2}
    assert result["analysis_error_preview"][:3] == [
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "WY1",
            "node_no": "PART-1-1::1001",
            "component_name": "UX",
            "point_value": 0.0,
            "initial_node_value": 0.0,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "WY1",
            "node_no": "PART-1-1::1001",
            "component_name": "UY",
            "point_value": 1.5,
            "initial_node_value": 1.5,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "WY1",
            "node_no": "PART-1-1::1001",
            "component_name": "UZ",
            "point_value": 0.0,
            "initial_node_value": 0.0,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
    ]


class _StaticJsonSingleKeyCursor(_StaticJsonCursor):
    def fetchall(self):
        sql = self.last_sql
        if "SELECT sensor_type, data FROM t_mt_static_test_data" in sql:
            return [
                {
                    "sensor_type": "浣嶇Щ",
                    "data": json.dumps(
                        [
                            {"WY1": "1.5"},
                            {"WY2": "2.5"},
                        ],
                        ensure_ascii=False,
                    ),
                }
            ]
        return super().fetchall()


class _StaticJsonSingleKeyConnection:
    def __init__(self):
        self.cursor_obj = _StaticJsonSingleKeyCursor()

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def close(self):
        return None


def test_compute_static_correlation_reads_single_key_sensor_json(monkeypatch):
    fake_conn = _StaticJsonSingleKeyConnection()
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.compute_static_correlation(project_id=202)

    assert result["project_id"] == 202
    assert result["aligned_point_count"] == 2
    assert result["value_count"] == 6
    assert result["dac"] == 100.0
    assert result["dsf"] == 1.0
    assert result["analysis_error_preview"][:3] == [
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "WY1",
            "node_no": "PART-1-1::1001",
            "component_name": "UX",
            "point_value": 0.0,
            "initial_node_value": 0.0,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "WY1",
            "node_no": "PART-1-1::1001",
            "component_name": "UY",
            "point_value": 1.5,
            "initial_node_value": 1.5,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
        {
            "load_case_no": 1,
            "result_no": 1,
            "point_no": "WY1",
            "node_no": "PART-1-1::1001",
            "component_name": "UZ",
            "point_value": 0.0,
            "initial_node_value": 0.0,
            "initial_relative_error": 0.0,
            "initial_abs_error": 0.0,
            "sensor_type_id": None,
        },
    ]
