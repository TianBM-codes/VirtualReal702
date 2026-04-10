from services.model_update.analysis import inp_service


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
