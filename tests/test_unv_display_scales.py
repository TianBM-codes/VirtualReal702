from services.model_update.importers import unv_service


class _QueryCursor:
    def __init__(self):
        self.last_sql = ""
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.last_params = params

    def fetchall(self):
        if "FROM t_mt_py_test_static_result" in self.last_sql:
            return [{"id": 9, "point": 1001, "ux": 0.5, "uy": -0.25, "uz": 1.0}]
        if "FROM t_mt_py_test_node" in self.last_sql and "ORDER BY nid" in self.last_sql:
            return [(1001, 1.0, 2.0, 3.0)]
        if "FROM t_mt_py_test_element" in self.last_sql:
            return [(1001, 1001)]
        if "FROM t_mt_py_test_modal_frequency" in self.last_sql:
            return [(1, 12.5, '{"1001": {"real": [0.1, 0.2, 0.3], "imag": [0.0, 0.0, 0.0]}}')]
        return []

    def fetchone(self):
        if "FROM t_mt_py_test_node" in self.last_sql and "LIMIT 1" in self.last_sql:
            return {"nid": "1001", "x": 1.0, "y": 2.0, "z": 3.0}
        if "FROM t_mt_measuring_point_info" in self.last_sql and "LIMIT 1" in self.last_sql:
            return {
                "id": 1,
                "measuring_point_name": "WY1",
                "sensor_type_id": 21,
                "x_position": 1.0,
                "y_position": 2.0,
                "z_position": 3.0,
            }
        return None

    def close(self):
        return None


class _QueryConnection:
    def cursor(self, dictionary=False):
        return _QueryCursor()

    def close(self):
        return None


def test_get_deform_sensor_positions_applies_static_display_scale(monkeypatch):
    monkeypatch.setattr(unv_service, "get_connection", lambda: _QueryConnection())
    monkeypatch.setattr(unv_service, "get_test_display_scale_factors", lambda project_id, cursor=None: {"dynamic": 1.0, "static": 1000.0})

    result = unv_service.get_deform_sensor_positions(
        project_id=101,
        static_result_id=9,
        scale=2.0,
    )

    assert result[0]["sensor_label"] == "WY1"
    assert result[0]["sensor_pos"] == [1001.0, -498.0, 2003.0]


def test_get_modal_shape_applies_dynamic_display_scale(monkeypatch):
    monkeypatch.setattr(unv_service, "get_connection", lambda: _QueryConnection())
    monkeypatch.setattr(unv_service, "get_test_display_scale_factors", lambda project_id, cursor=None: {"dynamic": 1000.0, "static": 1.0})

    result = unv_service.get_modal_shape(101)

    assert result["modal_shape"][0]["position"]["real"] == [100.0, 200.0, 300.0]
