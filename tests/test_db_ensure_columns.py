import db


class _FakeCursor:
    def __init__(self):
        self.executed = []
        self._fetchone = (1,)
        self._fetchall = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        normalized = " ".join(str(sql).split())
        if "FROM information_schema.COLUMNS" in normalized:
            table_name = params[1]
            column_name = params[2]
            if table_name == "t_mt_py_fem_modal_correlation" and column_name in {"flip", "msf"}:
                self._fetchone = None
            else:
                self._fetchone = (1,)
            self._fetchall = []
        elif "FROM information_schema.TABLES" in normalized:
            self._fetchone = (1,)
            self._fetchall = []
        else:
            self._fetchone = (1,)
            self._fetchall = []

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return self._fetchall

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None


def test_ensure_tables_exist_adds_modal_correlation_columns_when_missing(monkeypatch):
    fake_cursor = _FakeCursor()
    fake_conn = _FakeConnection(fake_cursor)
    old_tables_ensured = db._tables_ensured
    db._tables_ensured = False
    try:
        monkeypatch.setattr(db, "get_connection", lambda: fake_conn)
        db.ensure_tables_exist()
    finally:
        db._tables_ensured = old_tables_ensured

    alter_sql = [sql for sql, _ in fake_cursor.executed if "ALTER TABLE t_mt_py_fem_modal_correlation ADD COLUMN flip" in sql]
    assert len(alter_sql) == 1
    msf_sql = [sql for sql, _ in fake_cursor.executed if "ALTER TABLE t_mt_py_fem_modal_correlation ADD COLUMN msf" in sql]
    assert len(msf_sql) == 1
