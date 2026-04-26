from pathlib import Path

from services.model_update.analysis import model_update_meta_service


class _WriteCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def close(self):
        return None


class _WriteConnection:
    def __init__(self):
        self.cursor_obj = _WriteCursor()
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


def test_resolve_abaqus_command_reads_service_config(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "service_config.json"
    config_path.write_text(
        '{"APP_ABAQUS_CMD": "C:/SIMULIA/Commands/abaqus.bat"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(model_update_meta_service, "_service_config_path", lambda: config_path)

    result = model_update_meta_service.resolve_abaqus_command(None)

    assert result == "C:/SIMULIA/Commands/abaqus.bat"


def test_resolve_python3_and_bayesian_output_dir_read_service_config(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "service_config.json"
    config_path.write_text(
        '{"APP_PYTHON3_CMD": "C:/Python/python.exe", "APP_BAYESIAN_OUTPUT_DIR": "D:/temp/bayesian"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(model_update_meta_service, "_service_config_path", lambda: config_path)

    assert model_update_meta_service.resolve_python3_command(None) == "C:/Python/python.exe"
    assert model_update_meta_service.resolve_bayesian_output_dir(None) == "D:/temp/bayesian"


def test_add_manual_response_upserts_row(monkeypatch):
    fake_conn = _WriteConnection()
    monkeypatch.setattr(model_update_meta_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(model_update_meta_service, "get_connection", lambda: fake_conn)

    result = model_update_meta_service.add_manual_response(
        project_id=7,
        response_type="DISPLACEMENT",
        scatter=0.05,
        dof="UX",
        step="Step-1",
    )

    assert result == {
        "project_id": 7,
        "type": "DISPLACEMENT",
        "step": "Step-1",
        "dof": "UX",
        "scatter": 0.05,
    }
    assert fake_conn.committed is True
    assert fake_conn.cursor_obj.executed == [
        (
            "INSERT INTO t_mt_py_fem_manual_response (pid, response_type, step_name, dof, scatter) VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE scatter = VALUES(scatter), updated_at = CURRENT_TIMESTAMP",
            (7, "DISPLACEMENT", "Step-1", "UX", 0.05),
        )
    ]
