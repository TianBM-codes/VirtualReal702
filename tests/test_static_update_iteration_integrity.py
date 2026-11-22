from services.model_update.analysis import bayesian_service as service


def test_response_instance_case_alignment_preserves_other_instances():
    targets = ["zd-1::4", "OTHER::4", 8]
    aligned = service._sens._align_scoped_labels_to_instance(targets, "ZD-1")
    assert aligned == ["ZD-1::4", "OTHER::4", 8]
    assert targets == ["zd-1::4", "OTHER::4", 8]
    assert service._scoped_target_node_labels_for_instance(targets, instance_name="ZD-1") == [4, 8]


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


def test_matrix_resolves_frame_from_its_source_result_group(monkeypatch, tmp_path):
    import pytest

    inp = tmp_path / "model.inp"
    inp.write_text("*Heading\n")
    (tmp_path / "manifest.db").touch()

    monkeypatch.setattr(service._sens, "_discover_sensitivity_fields_from_workspace",
                        lambda *args, **kwargs: {"step": "LC1"})
    class FrameResolved(Exception):
        pass

    def resolve_frame(workspace, **kwargs):
        assert kwargs["result_group"] == "sensitivity_batch_new"
        assert kwargs["requested_frame"] is None
        raise FrameResolved()

    monkeypatch.setattr(service._sens, "_resolve_workspace_step_frame", resolve_frame)
    with pytest.raises(FrameResolved):
        service.build_dsa_normalized_sensitivity_matrix(
            project_id=1, inp_path=str(inp), workspace=str(tmp_path),
            result_group="sensitivity_batch_new", step="LC1",
        )


def test_update_solver_uses_dedicated_project_result_without_full_workspace(monkeypatch, tmp_path):
    inp = tmp_path / "model.inp"
    odb = tmp_path / "model.odb"
    inp.write_text("*Heading\n")
    odb.touch()
    monkeypatch.setattr(service._solver, "_run_local_solver",
                        lambda **kwargs: {"ok": True, "artifacts": {"odb": str(odb)}})

    def unexpected_full_workspace(**kwargs):
        raise AssertionError("Nodal model updating should use the dedicated project importer")

    monkeypatch.setattr(service._sens, "build_workspace_from_odb", unexpected_full_workspace)
    captured = {}

    def submit(**kwargs):
        captured.update(kwargs)
        return {"workspace": str(tmp_path), "result_group": kwargs["result_group"]}

    monkeypatch.setattr(service._sens, "_submit_project_result_group_and_wait", submit)
    result = service._run_iteration_solver(
        inp_path=inp, output_dir=tmp_path, iteration=0, abaqus="abaqus",
        job_name="model", cpus=1, interactive=True, timeout_sec=60,
        extra_args=[], python3=None, keep_raw=False, project_id=1, step="LC1",
        field_prefix="d_U_", result_group="sensitivity_batch_update_1_iter0",
    )
    assert captured["step"] == "LC1"
    assert captured["field_prefix"] == "d_U_"
    assert result["project_result_parse"]["result_group"] == "sensitivity_batch_update_1_iter0"
    assert result["workspace"]["workspace"] == str(tmp_path)


def test_update_publication_reuses_already_imported_iteration(monkeypatch):
    from services.model_update.analysis import l3_local_bridge_service

    def unexpected_reparse(**kwargs):
        raise AssertionError("An iteration should only be extracted once")

    monkeypatch.setattr(l3_local_bridge_service, "submit_project_result_group_and_wait", unexpected_reparse)
    monkeypatch.setattr(service._sens, "_persist_sensitivity_matrix", lambda **kwargs: {})
    monkeypatch.setattr(service._sens, "merge_dsa_sensitivity_fields", lambda **kwargs: [])
    source = {"workspace": "/project", "result_group": "sensitivity_batch_update_1_iter0"}
    result = service._publish_static_iteration_results(
        project_id=1, batch_no=1, iteration=0,
        solver_payload={"project_result_parse": source},
        matrix_payload={"step": "LC1", "frame": 0, "instances": ["PART-1-1"]},
        field_prefix="d_U_",
    )
    assert result["source_result_group"] == source["result_group"]
