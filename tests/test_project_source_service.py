from services.model_update.analysis import project_source_service


class _FakeCursor:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=None):
        return None

    def fetchone(self):
        return self.row

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, row):
        self.row = row

    def cursor(self, dictionary=False):
        return _FakeCursor(self.row)

    def close(self):
        return None


def test_resolve_project_source_inp_path_uses_workspace_filename_from_url(monkeypatch, tmp_path):
    project_dir = tmp_path / "24"
    project_dir.mkdir()
    inp_path = project_dir / "door.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    class _FakeRepo:
        def __init__(self, db_path: str):
            self.db_path = db_path

        def get_project(self, project_id: str):
            return {
                "workspace": "24",
                "inp_path": "https://example.com/models/door.inp?token=abc",
            }

        def resolve_workspace(self, stored: str, data_root: str) -> str:
            return str(project_dir)

    monkeypatch.setattr(project_source_service, "get_connection", lambda: _FakeConnection(None))
    monkeypatch.setattr(project_source_service, "RegistryRepo", _FakeRepo)
    monkeypatch.setattr(project_source_service.settings, "data_root", str(tmp_path))
    monkeypatch.setattr(project_source_service.settings, "registry_db_path", str(tmp_path / "registry.db"))

    resolved = project_source_service.resolve_project_source_inp_path(24)

    assert resolved == str(inp_path.resolve())
