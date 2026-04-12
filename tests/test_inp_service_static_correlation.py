from services.model_update.analysis import inp_service


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


def test_compute_static_correlation_defaults_to_translations_only(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)

    result = inp_service.compute_static_correlation(project_id=101)

    assert result["components"] == ["UX", "UY", "UZ"]
    assert result["value_count"] == 2
    assert result["dac"] == 100.0
    assert result["dsf"] == 1.0
    assert result["component_value_counts"] == {"UX": 2, "UY": 0, "UZ": 0}
