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


def test_import_fe_modal_results_defaults_modal_identity_to_bdf_model(monkeypatch):
    fake_conn = _FakeConnection()
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(inp_service, "log_project_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(inp_service, "log_project_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(inp_service, "log_project_error", lambda *args, **kwargs: None)

    result = inp_service.import_fe_modal_results(
        project_id=7,
        overwrite=False,
        modes=[
            {
                "mode_no": 1,
                "frequency": 12.3,
                "nodes": [
                    {
                        "fem_node_label": 101,
                        "u1": 1.0,
                        "u2": 2.0,
                        "u3": 3.0,
                    }
                ],
            }
        ],
    )

    insert_params = fake_conn.cursor_obj.executed[0][1]
    assert insert_params[3] == "BDF_MODEL"
    assert insert_params[4] == "BDF_MODEL"
    assert result["rows_preview"][0]["instance_name"] == "BDF_MODEL"
    assert result["rows_preview"][0]["part_name"] == "BDF_MODEL"
    assert fake_conn.committed is True
