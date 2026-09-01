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


def test_resolve_project_input_file_translates_original_project_source_name(monkeypatch, tmp_path):
    project_dir = tmp_path / "26"
    project_dir.mkdir(parents=True)
    runtime_bdf = project_dir / "project_bdf_abcd1234.bdf"
    runtime_bdf.write_text("CEND\nBEGIN BULK\nENDDATA\n", encoding="utf-8")

    class _FakeRepo:
        def __init__(self, db_path):
            self.db_path = db_path

        def get_project(self, project_id):
            return {
                "workspace": str(project_dir),
                "inp_path": str(runtime_bdf),
                "source_file": runtime_bdf.name,
                "original_inp_path": str(project_dir / "车门模型.bdf"),
                "original_source_file": "车门模型.bdf",
            }

        def resolve_workspace(self, stored, data_root):
            return str(project_dir)

    monkeypatch.setattr(project_file_service, "RegistryRepo", _FakeRepo)
    monkeypatch.setattr(project_file_service.settings, "registry_db_path", str(tmp_path / "registry.db"))
    monkeypatch.setattr(project_file_service.settings, "data_root", str(tmp_path))
    monkeypatch.setattr(project_file_service, "resolve_project_workspace_root", lambda project_id: project_dir.resolve())

    resolved = project_file_service.resolve_project_input_file(
        26,
        explicit_path=str(project_dir / "车门模型.bdf"),
        file_name=None,
        field_name="input_bdf",
    )

    assert resolved == runtime_bdf.resolve()
