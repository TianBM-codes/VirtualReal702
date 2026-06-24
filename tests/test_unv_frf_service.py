from services.model_update.importers import unv_frf_service


class _FrfCursor:
    def __init__(self):
        self.curves = []
        self.points = []
        self._result = []
        self.lastrowid = None
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        normalized = " ".join(str(sql).split())

        if "SELECT id FROM t_mt_py_test_frf_curve" in normalized:
            pid, curve_name = params
            match = next((row for row in self.curves if row["pid"] == pid and row["curve_name"] == curve_name), None)
            self._result = [match] if match else []
            return

        if "INSERT INTO t_mt_py_test_frf_curve" in normalized:
            curve_id = len(self.curves) + 1
            self.lastrowid = curve_id
            self.curves.append(
                {
                    "id": curve_id,
                    "pid": params[0],
                    "curve_name": params[1],
                    "curve_no": params[2],
                }
            )
            return

        if "UPDATE t_mt_py_test_frf_curve" in normalized:
            curve_id = params[-1]
            row = next(row for row in self.curves if row["id"] == curve_id)
            row["curve_no"] = params[0]
            return

        if "DELETE FROM t_mt_py_test_frf_point" in normalized:
            pid, curve_id = params
            self.points = [row for row in self.points if not (row["pid"] == pid and row["curve_id"] == curve_id)]
            return

        if "INSERT INTO t_mt_py_test_frf_point" in normalized:
            self.points.append(
                {
                    "pid": params[0],
                    "curve_id": params[1],
                    "point_no": params[2],
                    "frequency": params[3],
                    "real_value": params[4],
                    "imag_value": params[5],
                }
            )
            return

        if "SELECT curve_name FROM t_mt_py_test_frf_curve" in normalized:
            pid = params[0]
            rows = sorted(
                [row for row in self.curves if row["pid"] == pid],
                key=lambda item: (item["curve_no"], item["curve_name"]),
            )
            self._result = [{"curve_name": row["curve_name"]} for row in rows]
            return

        if "SELECT id, curve_name FROM t_mt_py_test_frf_curve" in normalized:
            pid, curve_name = params
            match = next((row for row in self.curves if row["pid"] == pid and row["curve_name"] == curve_name), None)
            self._result = [{"id": match["id"], "curve_name": match["curve_name"]}] if match else []
            return

        if "SELECT frequency, real_value, imag_value FROM t_mt_py_test_frf_point" in normalized:
            pid, curve_id = params
            rows = sorted(
                [row for row in self.points if row["pid"] == pid and row["curve_id"] == curve_id],
                key=lambda item: item["point_no"],
            )
            self._result = [
                {
                    "frequency": row["frequency"],
                    "real_value": row["real_value"],
                    "imag_value": row["imag_value"],
                }
                for row in rows
            ]
            return

        raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        if not self._result:
            return None
        return self._result[0]

    def fetchall(self):
        return list(self._result)

    def close(self):
        return None


class _FrfConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
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


def test_import_unv_frf_data_replaces_same_name_curve_points(monkeypatch):
    cursor = _FrfCursor()
    conn = _FrfConnection(cursor)

    monkeypatch.setattr(unv_frf_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(unv_frf_service, "get_connection", lambda: conn)
    monkeypatch.setattr(
        unv_frf_service,
        "parse_unv58",
        lambda _: [
            {
                "name": "FRF_A",
                "freq": [1.0, 2.0],
                "real": [10.0, 20.0],
                "imag": [1.0, 2.0],
                "meta": {
                    "index": 1,
                    "title": "NONE",
                    "type": "ACCELERANCE (A/F)",
                    "response_node": 3,
                    "response_dir": 3,
                    "reference_node": 29,
                    "reference_dir": 3,
                    "x_type": 18,
                    "y_type": 12,
                    "denominator_type": 13,
                    "z_type": 0,
                    "ordinate_type": 5,
                    "abscissa_spacing": 0,
                    "n_points": 2,
                },
            }
        ],
    )

    first = unv_frf_service.import_unv_frf_data("demo.unv", 7)
    assert conn.committed is True
    assert first["frf_curve_count"] == 1
    assert len(cursor.curves) == 1
    assert len(cursor.points) == 2

    conn.committed = False
    monkeypatch.setattr(
        unv_frf_service,
        "parse_unv58",
        lambda _: [
            {
                "name": "FRF_A",
                "freq": [5.0],
                "real": [30.0],
                "imag": [3.0],
                "meta": {
                    "index": 1,
                    "title": "NONE",
                    "type": "ACCELERANCE (A/F)",
                    "response_node": 3,
                    "response_dir": 3,
                    "reference_node": 29,
                    "reference_dir": 3,
                    "x_type": 18,
                    "y_type": 12,
                    "denominator_type": 13,
                    "z_type": 0,
                    "ordinate_type": 5,
                    "abscissa_spacing": 0,
                    "n_points": 1,
                },
            }
        ],
    )

    second = unv_frf_service.import_unv_frf_data("demo2.unv", 7)

    assert conn.committed is True
    assert second["frf_point_count"] == 1
    assert len(cursor.curves) == 1
    assert len(cursor.points) == 1
    assert cursor.points[0]["frequency"] == 5.0
    assert cursor.points[0]["real_value"] == 30.0


def test_get_project_frf_curve_uses_expected_component_algorithms(monkeypatch):
    cursor = _FrfCursor()
    cursor.curves = [
        {"id": 1, "pid": 7, "curve_name": "TEST FRF 1 (+3UZ : +3UZ)", "curve_no": 1},
        {"id": 2, "pid": 7, "curve_name": "TEST FRF 2 (+5UX : +3UZ)", "curve_no": 2},
    ]
    cursor.points = [
        {"pid": 7, "curve_id": 1, "point_no": 1, "frequency": 1.0, "real_value": 3.0, "imag_value": 4.0},
        {"pid": 7, "curve_id": 1, "point_no": 2, "frequency": 2.123456, "real_value": 1.234567, "imag_value": 0.0},
        {"pid": 7, "curve_id": 2, "point_no": 1, "frequency": 1.5, "real_value": 6.0, "imag_value": 8.0},
    ]
    conn = _FrfConnection(cursor)

    monkeypatch.setattr(unv_frf_service, "get_connection", lambda: conn)

    names_payload = unv_frf_service.get_project_frf_names(7)
    assert names_payload == {
        "project_id": 7,
        "names": ["TEST FRF 1 (+3UZ : +3UZ)", "TEST FRF 2 (+5UX : +3UZ)"],
    }

    real_payload = unv_frf_service.get_project_frf_curve(7, "TEST FRF 1 (+3UZ : +3UZ)", 1)
    imag_payload = unv_frf_service.get_project_frf_curve(7, "TEST FRF 1 (+3UZ : +3UZ)", 2)
    mag_payload = unv_frf_service.get_project_frf_curve(7, "TEST FRF 1 (+3UZ : +3UZ)", 3)
    phase_payload = unv_frf_service.get_project_frf_curve(7, "TEST FRF 1 (+3UZ : +3UZ)", 4)

    assert real_payload["x_name"] == "Frequency[Hz]"
    assert real_payload["y_name"] == "Real"
    assert real_payload["x"] == [1.0, 2.1235]
    assert real_payload["series"] == [[1.0, 3.0], [2.1235, 1.2346]]
    assert imag_payload["x_name"] == "Frequency[Hz]"
    assert imag_payload["y_name"] == "Imaginary"
    assert imag_payload["x"] == [1.0, 2.1235]
    assert imag_payload["series"] == [[1.0, 4.0], [2.1235, 0.0]]
    assert mag_payload["x_name"] == "Frequency[Hz]"
    assert mag_payload["y_name"] == "Magnitude"
    assert mag_payload["x"] == [1.0, 2.1235]
    assert mag_payload["series"] == [[1.0, 5.0], [2.1235, 1.2346]]
    assert phase_payload["x_name"] == "Frequency[Hz]"
    assert phase_payload["y_name"] == "Phase"
    assert phase_payload["x"] == [1.0, 2.1235]
    assert phase_payload["series"][0][0] == 1.0
    assert phase_payload["series"][0][1] == 53.13
    assert phase_payload["line_name"] == "FRF 1"

    multi_payload = unv_frf_service.get_project_frf_curve(
        7,
        names=["TEST FRF 1 (+3UZ : +3UZ)", "TEST FRF 2 (+5UX : +3UZ)"],
        index=3,
    )
    assert multi_payload["line_names"] == ["FRF 1", "FRF 2"]
    assert multi_payload["x_name"] == "Frequency[Hz]"
    assert multi_payload["y_name"] == "Magnitude"
    assert multi_payload["lines"][0]["line_name"] == "FRF 1"
    assert multi_payload["lines"][0]["x"] == [1.0, 2.1235]
    assert multi_payload["lines"][0]["series"] == [[1.0, 5.0], [2.1235, 1.2346]]
    assert multi_payload["lines"][1]["line_name"] == "FRF 2"
    assert multi_payload["lines"][1]["x"] == [1.5]
    assert multi_payload["lines"][1]["series"] == [[1.5, 10.0]]
    assert multi_payload["line_name"] == "FRF 1"
    assert multi_payload["x"] == [1.0, 2.1235]
