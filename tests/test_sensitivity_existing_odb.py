from services.model_update.analysis import sensitivity_service as service


def test_existing_odb_is_parsed_without_generating_or_running_solver(monkeypatch, tmp_path):
    analysis_inp = tmp_path / "model_dsa.inp"
    analysis_inp.write_text("*Heading\n", encoding="utf-8")
    odb_path = tmp_path / "solved.odb"
    captured = {}

    monkeypatch.setattr(service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(service, "_run_with_project_sensitivity_status", lambda project_id, run: run())

    def unexpected_solver(**kwargs):
        raise AssertionError("Reusing an ODB must not generate another INP or start the solver")

    def load_matrix(**kwargs):
        captured.update(kwargs)
        return {"matrix": [[0.5]]}

    monkeypatch.setattr(service, "run_abaqus_sensitivity_job", unexpected_solver)
    monkeypatch.setattr(service, "_load_dsa_normalized_sensitivity_matrix", load_matrix)
    monkeypatch.setattr(service, "_finalize_sensitivity_store_result", lambda **kwargs: kwargs)

    result = service.store_dsa_sensitivity_results(
        project_id=1,
        input_inp=str(analysis_inp),
        odb_path=str(odb_path),
        run_solver=False,
        step="Step-1",
        instances=["ZD-1"],
        frame=1,
    )

    assert captured["inp_path"] == str(analysis_inp)
    assert captured["odb_path"] == str(odb_path)
    assert captured["frame"] == 1
    assert result["solver_payload"] is None
    assert result["matrix_payload"]["matrix"] == [[0.5]]
