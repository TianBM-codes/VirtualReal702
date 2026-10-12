from services.model_update.analysis import bayesian_service as service


def test_iteration_parameter_includes_are_independent(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "parameters.inp").write_text("*PARAMETER\nE_SET_149=210000000000\n", encoding="utf-8")
    main = source / "model.inp"
    main.write_text("*INCLUDE, INPUT=parameters.inp\n", encoding="utf-8")
    root = tmp_path / "iterations"
    first = service._copy_iteration_input(str(main), root, 0)
    second = service._copy_iteration_input(str(first), root, 1)
    service.update_parameter_section_values(str(second), {"E_SET_149": 190000000000})
    assert first.parent != second.parent
    assert "210000000000" in (first.parent / "parameters.inp").read_text(encoding="utf-8")
    assert "190000000000" in (second.parent / "parameters.inp").read_text(encoding="utf-8")
    assert "210000000000" in (source / "parameters.inp").read_text(encoding="utf-8")


def test_target_resolution_uses_instance_node_match_before_bare_number(monkeypatch):
    class Cursor:
        def execute(self, sql, params):
            self.sql = sql

        def fetchall(self):
            if "MAX(created_at)" in self.sql:
                return [{"load_case_no": 1, "result_no": 1}]
            return [
                {"test_node_id": "WY4", "instance_name": "PART-1-1", "fem_node_label": 4},
                {"test_node_id": "OTHER", "instance_name": "PART-2-1", "fem_node_label": 4},
            ]

        def close(self):
            pass

    class Connection:
        def cursor(self, **kwargs):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(service, "get_connection", lambda: Connection())
    monkeypatch.setattr(service._correlation, "_load_test_static_rows", lambda *args: [
        {"point": "WY4", "uy": 0.58}, {"point": "OTHER", "uy": 0.99}, {"point": "4", "uy": 10.0},
    ])
    resolved = service._load_project_static_target_response_values(project_id=1, response_rows=[{
        "row_key": "response", "response_label": "PART-1-1::4", "instance": "PART-1-1", "response_component": "U2",
    }])
    assert resolved == {"response": 0.58}
