import sqlite3

from services.model_update.analysis import inp_service
from services.model_update.analysis import fem_result_service


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

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        return None


class _FakeRegistryRepo:
    def __init__(self, workspace, *, project_exists=True, result_group_status="ready"):
        self.workspace = workspace
        self.project_exists = project_exists
        self.result_group_status = result_group_status

    def get_project(self, project_id):
        if not self.project_exists:
            return None
        return {"project_id": str(project_id), "workspace": self.workspace}

    def get_result_group(self, project_id, result_group):
        return {
            "project_id": str(project_id),
            "result_group": str(result_group),
            "status": self.result_group_status,
        }

    def resolve_workspace(self, stored, data_root):
        return self.workspace


def _write_project_result_manifest(workspace, *, result_group="rg_static"):
    conn = sqlite3.connect(workspace / "manifest.db")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        "CREATE TABLE steps (result_group TEXT, step_name TEXT, step_number INTEGER, procedure TEXT, num_frames INTEGER, description TEXT)"
    )
    conn.execute(
        "CREATE TABLE frames (result_group TEXT, step_name TEXT, frame_idx INTEGER, frame_value REAL, description TEXT)"
    )
    conn.execute(
        "CREATE TABLE result_files (result_group TEXT, step_name TEXT, field_name TEXT, components TEXT, invariants TEXT, positions TEXT)"
    )
    conn.execute(
        "CREATE TABLE result_blocks (result_group TEXT, step_name TEXT, field_name TEXT, instance_name TEXT, position TEXT, elem_type TEXT, h5_path TEXT)"
    )
    conn.execute("CREATE TABLE instances (instance_name TEXT, part_name TEXT)")
    conn.executemany(
        "INSERT INTO steps (result_group, step_name, step_number, procedure, num_frames, description) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (result_group, "Step-1", 1, "STATIC", 2, "Load case 1"),
            (result_group, "Step-2", 2, "STATIC", 1, "Load case 2"),
        ],
    )
    conn.executemany(
        "INSERT INTO frames (result_group, step_name, frame_idx, frame_value, description) VALUES (?, ?, ?, ?, ?)",
        [
            (result_group, "Step-1", 0, 0.0, "initial"),
            (result_group, "Step-1", 1, 1.0, "final"),
            (result_group, "Step-2", 0, 2.0, "case-2-final"),
        ],
    )
    conn.executemany(
        "INSERT INTO result_files (result_group, step_name, field_name, components, invariants, positions) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (result_group, "Step-1", "U", '["U1","U2","U3"]', "[]", '["NODAL"]'),
            (result_group, "Step-1", "S", '["S11","S22"]', '["MISES"]', '["INTEGRATION_POINT"]'),
            (result_group, "Step-2", "U", '["U1","U2","U3"]', "[]", '["NODAL"]'),
        ],
    )
    conn.executemany(
        "INSERT INTO result_blocks (result_group, step_name, field_name, instance_name, position, elem_type, h5_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (result_group, "Step-1", "U", "PART-1-1", "NODAL", None, "/fake/u"),
            (result_group, "Step-2", "U", "PART-1-1", "NODAL", None, "/fake/u2"),
            ("other_group", "Step-1", "U", "PART-9-9", "NODAL", None, "/fake/other"),
        ],
    )
    conn.execute(
        "INSERT INTO instances (instance_name, part_name) VALUES (?, ?)",
        ("PART-1-1", "PART-1"),
    )
    conn.commit()
    conn.close()


def test_load_static_result_rows_from_txt_skips_first_three_lines(tmp_path):
    txt_path = tmp_path / "static_result.txt"
    txt_path.write_text(
        "\n".join([
            "header 1",
            "header 2",
            "header 3",
            "1001 0.1 0.2 0.3 0.01 0.02 0.03",
            "1002,1.1,1.2,1.3,0.11,0.12,0.13",
        ]),
        encoding="utf-8",
    )

    rows = inp_service._load_static_result_rows_from_txt(str(txt_path))

    assert rows == [
        {
            "fem_node_label": 1001,
            "u1": 0.1,
            "u2": 0.2,
            "u3": 0.3,
            "ur1": 0.01,
            "ur2": 0.02,
            "ur3": 0.03,
        },
        {
            "fem_node_label": 1002,
            "u1": 1.1,
            "u2": 1.2,
            "u3": 1.3,
            "ur1": 0.11,
            "ur2": 0.12,
            "ur3": 0.13,
        },
    ]


def test_import_fe_static_results_writes_new_static_table(monkeypatch, tmp_path):
    txt_path = tmp_path / "static_result.txt"
    txt_path.write_text(
        "\n".join([
            "header 1",
            "header 2",
            "header 3",
            "1001 0.1 0.2 0.3 0.01 0.02 0.03",
            "1002 1.1 1.2 1.3 0.11 0.12 0.13",
        ]),
        encoding="utf-8",
    )

    fake_conn = _FakeConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(fem_result_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(fem_result_service, "get_connection", lambda: fake_conn)

    result = inp_service.import_fe_static_results(
        project_id=101,
        overwrite=True,
        file_path=str(txt_path),
        load_case_no=3,
        instance_name="PART-1-1",
        part_name="PART-1",
    )

    executed_sql = "\n".join(sql for sql, _ in fake_conn.cursor_obj.executed)
    insert_params = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "INSERT INTO t_mt_py_fem_static_result" in sql
    ]

    assert fake_conn.committed is True
    assert "DELETE FROM t_mt_py_fem_static_result" in executed_sql
    assert len(insert_params) == 2
    assert insert_params[0][0:5] == (101, 3, "PART-1-1", "PART-1", 1001)
    assert insert_params[0][5:11] == (0.1, 0.2, 0.3, 0.01, 0.02, 0.03)
    assert result["project_id"] == 101
    assert result["load_case_nos"] == [3]
    assert result["row_count"] == 2


def test_import_fe_static_results_from_project_result_uses_latest_frame_and_case_delete(monkeypatch, tmp_path):
    _write_project_result_manifest(tmp_path, result_group="rg_static")

    fake_conn = _FakeConnection()
    captured_calls = []

    def fake_label_map(workspace, *, step, field, instance, position, frame, aggregation, component=None,
                       component_index=None, result_group=None):
        captured_calls.append(
            {
                "workspace": workspace,
                "step": step,
                "field": field,
                "instance": instance,
                "position": position,
                "frame": frame,
                "aggregation": aggregation,
                "component": component,
                "result_group": result_group,
            }
        )
        base = {
            "PART-1-1::1001": {
                "U1": 0.1,
                "U2": 0.2,
                "U3": 0.3,
            }
        }
        return {label: values[component] for label, values in base.items()}

    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(inp_service, "_registry_repo", lambda: _FakeRegistryRepo(str(tmp_path)))
    monkeypatch.setattr(fem_result_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(fem_result_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(fem_result_service, "_registry_repo", lambda: _FakeRegistryRepo(str(tmp_path)))
    monkeypatch.setattr(inp_service._sens, "_workspace_result_label_map", fake_label_map)

    result = inp_service.import_fe_static_results_from_project_result(
        project_id=101,
        result_group="rg_static",
        load_case_no=7,
    )

    delete_calls = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "DELETE FROM t_mt_py_fem_static_result WHERE pid = %s AND load_case_no = %s" in sql
    ]
    insert_params = [
        params
        for sql, params in fake_conn.cursor_obj.executed
        if "INSERT INTO t_mt_py_fem_static_result" in sql
    ]

    assert fake_conn.committed is True
    assert delete_calls == [(101, 7)]
    assert len(insert_params) == 1
    assert insert_params[0][0:5] == (101, 7, "PART-1-1", "PART-1", 1001)
    assert insert_params[0][5:8] == (0.1, 0.2, 0.3)
    assert result["project_id"] == 101
    assert result["result_group"] == "rg_static"
    assert result["step_name"] == "Step-1"
    assert result["frame_idx"] == 1
    assert result["instances"] == ["PART-1-1"]
    assert result["load_case_nos"] == [7]
    assert len(captured_calls) == 3
    assert {item["component"] for item in captured_calls} == {"U1", "U2", "U3"}
    assert {item["result_group"] for item in captured_calls} == {"rg_static"}
    assert {item["frame"] for item in captured_calls} == {1}


def test_list_project_result_steps_returns_step_frame_and_field_catalog(monkeypatch, tmp_path):
    _write_project_result_manifest(tmp_path, result_group="rg_static")

    monkeypatch.setattr(inp_service, "_registry_repo", lambda: _FakeRegistryRepo(str(tmp_path)))
    monkeypatch.setattr(fem_result_service, "_registry_repo", lambda: _FakeRegistryRepo(str(tmp_path)))

    result = inp_service.list_project_result_steps(
        project_id=101,
        result_group="rg_static",
    )

    assert result["project_id"] == 101
    assert result["result_group"] == "rg_static"
    assert [item["step_name"] for item in result["steps"]] == ["Step-1", "Step-2"]
    assert result["steps"][0]["default_frame_idx"] == 1
    assert result["steps"][0]["frames"] == [
        {"frame_idx": 0, "frame_value": 0.0, "description": "initial"},
        {"frame_idx": 1, "frame_value": 1.0, "description": "final"},
    ]
    assert result["steps"][0]["fields"][0]["field_name"] == "S"
    assert result["steps"][0]["fields"][1]["field_name"] == "U"
    assert result["steps"][1]["frames"] == [
        {"frame_idx": 0, "frame_value": 2.0, "description": "case-2-final"},
    ]
