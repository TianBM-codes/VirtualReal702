from services.model_update.analysis import project_status_service


class _FakeCursor:
    def __init__(self, columns):
        self.columns = list(columns)
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return [(name,) for name in self.columns]

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, columns):
        self.columns = list(columns)
        self.cursor_obj = _FakeCursor(columns)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_update_work_condition_project_status_skips_unknown_columns(monkeypatch):
    project_status_service._work_condition_project_columns.cache_clear()
    fake_conn = _FakeConnection(["project_id", "sensitivity_status"])
    monkeypatch.setattr(project_status_service, "get_connection", lambda: fake_conn)

    project_status_service.update_work_condition_project_status(
        22,
        sensitivity_status=1,
        simulation_result_status=2,
    )

    executed = fake_conn.cursor_obj.executed
    assert executed[0][0] == "SHOW COLUMNS FROM t_mt_work_condition_project"
    assert "sensitivity_status = %s" in executed[1][0]
    assert "simulation_result_status = %s" not in executed[1][0]
    assert executed[1][1] == (1, 22)
    assert fake_conn.committed is True
    assert fake_conn.closed is True


def test_update_work_condition_project_status_returns_when_all_columns_unknown(monkeypatch):
    project_status_service._work_condition_project_columns.cache_clear()
    fake_conn = _FakeConnection(["project_id", "sensitivity_status"])
    monkeypatch.setattr(project_status_service, "get_connection", lambda: fake_conn)

    project_status_service.update_work_condition_project_status(
        22,
        simulation_result_status=2,
    )

    executed = fake_conn.cursor_obj.executed
    assert executed == [("SHOW COLUMNS FROM t_mt_work_condition_project", None)]
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_update_status_skips_missing_legacy_project_table(monkeypatch):
    class MissingTableError(Exception):
        errno = 1146

    fake_conn = _FakeConnection([])

    def show_columns(sql):
        assert sql == "SHOW COLUMNS FROM t_mt_work_condition_project"
        raise MissingTableError()

    columns = project_status_service._work_condition_project_columns
    columns.cache_clear()
    monkeypatch.setattr(fake_conn.cursor_obj, "execute", show_columns)
    monkeypatch.setattr(project_status_service, "get_connection", lambda: fake_conn)

    try:
        project_status_service.update_work_condition_project_status(22, sensitivity_status=1)
    finally:
        columns.cache_clear()

    assert fake_conn.cursor_obj.closed
    assert fake_conn.closed
    assert not fake_conn.committed
