from services.model_update.analysis import project_file_service


def test_resolve_project_input_file_finds_name_under_sensitivity_dir(monkeypatch, tmp_path):
    project_dir = tmp_path / "25"
    sensitivity_dir = project_dir / "cal" / "sensitivity"
    sensitivity_dir.mkdir(parents=True)
    inp_path = sensitivity_dir / "static_dsa.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    monkeypatch.setattr(project_file_service, "resolve_project_workspace_root", lambda project_id: project_dir.resolve())

    def _fake_cal_root(project_id, *parts):
        path = project_dir / "cal"
        for part in parts:
            if str(part or "").strip():
                path = path / str(part)
        return path.resolve()

    monkeypatch.setattr(project_file_service, "resolve_project_cal_root", _fake_cal_root)

    resolved = project_file_service.resolve_project_input_file(
        25,
        explicit_path=None,
        file_name="static_dsa.inp",
        field_name="input_inp",
    )

    assert resolved == inp_path.resolve()
