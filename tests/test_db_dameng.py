"""Focused adapter regression tests; no database connection is required."""

from types import SimpleNamespace

import pytest

from db_dameng import (
    CaseRow, DamengConnection, DamengCursor, UnsupportedSQL,
    basic, bind, column_definition, prepare, render, table_spec, tokens,
)


DDL = """CREATE TABLE IF NOT EXISTS example (
    id BIGINT NOT NULL AUTO_INCREMENT,
    pid BIGINT NOT NULL,
    code VARCHAR(100),
    payload JSON,
    PRIMARY KEY (id),
    UNIQUE KEY uk_code (pid, code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


class RawCursor:
    def __init__(self, conflicts=()):
        self.calls = []
        self.conflicts = list(conflicts)
        self.rowcount = 1
        self.lastrowid = None
        self.description = [("PAYLOAD", None)]

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchmany(self, size):
        return self.conflicts[:size]

    def fetchone(self):
        return (11,)

    def close(self):
        pass


def cursor(conflicts=()):
    raw = RawCursor(conflicts)
    conn = SimpleNamespace(raw=SimpleNamespace(cursor=lambda: raw),
                           pool=SimpleNamespace(specs={"example": table_spec(DDL)}))
    return DamengCursor(conn), raw


def test_rewrite_preserves_literals_and_parameter_payload():
    payload = "NOW() LIMIT 1 %s `time` ON DUPLICATE KEY UPDATE"
    sql = "SELECT 'NOW() LIMIT 1 %s', `time` FROM example WHERE code=%s LIMIT %s,%s"
    translated, params = bind(basic(prepare(sql, (payload, 4, 5))), (payload, 4, 5))
    assert "'NOW() LIMIT 1 %s'" in translated
    assert '"time"' in translated
    assert "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY" in translated
    assert params == (payload, 4, 5)


def test_pagination_reorders_bound_values():
    args = (10, 20)
    sql, values = bind(basic(prepare("SELECT id FROM example LIMIT %s OFFSET %s", args)), args)
    assert "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY" in sql
    assert values == (20, 10)


def test_ddl_keeps_defaults_and_moves_identity_after_type():
    definition = column_definition(tokens("id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id'"))
    assert render(definition) == "id BIGINT IDENTITY(1,1) NOT NULL"
    definition = column_definition(tokens("label VARCHAR(100) DEFAULT 'COMMENT NOW()' COMMENT 'text'"))
    assert "'COMMENT NOW()'" in render(definition)
    assert "'text'" not in definition


def test_prefix_index_retains_prefix_length():
    spec = table_spec("CREATE TABLE cache (pid INT, path VARCHAR(1024), UNIQUE KEY uk_path(pid,path(255)))")
    assert spec[2] == [[("pid", None), ("path", 255)]]


def test_upsert_uses_locked_row_and_does_not_commit():
    cur, raw = cursor([("physical-row",)])
    cur.execute("INSERT INTO example (pid,code,payload) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE payload=VALUES(payload)", (7, "abc", "中文"))
    select, update, identity = raw.calls
    assert '"pid" = ?' in select[0] and '"code" = ?' in select[0]
    assert select[1] == (7, "abc")
    assert update[1] == ("中文", "physical-row")
    assert "WHERE ROWID=?" in update[0]
    assert identity[1] == ("physical-row",)
    assert cur.lastrowid == 11


def test_upsert_refuses_to_update_two_conflicting_rows():
    cur, raw = cursor([("r1",), ("r2",)])
    with pytest.raises(UnsupportedSQL, match="multiple records"):
        cur.execute("INSERT INTO example (id,pid,code) VALUES (%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE code=VALUES(code)", (1, 2, "c"))
    assert not any(sql.startswith("UPDATE") for sql, _ in raw.calls)


def test_concurrent_duplicate_rolls_back_to_savepoint_only():
    cur, raw = cursor()
    reads = iter([[], [("race-winner",)]])
    raw.fetchmany = lambda size: next(reads)
    original = raw.execute

    def insert_races(sql, params=()):
        original(sql, params)
        if sql.startswith("INSERT"):
            raise RuntimeError(-6602, "unique key violation")

    raw.execute = insert_races
    cur.execute("INSERT INTO example (pid,code,payload) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE payload=VALUES(payload)", (7, "abc", "payload"))
    statements = [sql for sql, _ in raw.calls]
    assert "SAVEPOINT odb_upsert" in statements
    assert "ROLLBACK TO SAVEPOINT odb_upsert" in statements
    assert "ROLLBACK" not in statements
    assert "COMMIT" not in statements
    update = next(params for sql, params in raw.calls if sql.startswith("UPDATE"))
    assert update == ("payload", "race-winner")


def test_dictionary_rows_and_clob_are_usable_by_services():
    cur, _ = cursor()
    lob = SimpleNamespace(read=lambda: '{"中文":1}')
    cur.dictionary = True
    row = cur._row((lob,))
    assert row["payload"] == '{"中文":1}'
    assert row["PAYLOAD"] == row.get("payload")
    assert CaseRow({"COLUMN_NAME": "pid"}).get("column_name") == "pid"


def test_close_rolls_back_and_releases_slot_even_on_driver_error():
    calls = []
    def rollback():
        calls.append("rollback")
        raise RuntimeError("disconnected")
    raw = SimpleNamespace(rollback=rollback, close=lambda: calls.append("close"))
    pool = SimpleNamespace(slots=SimpleNamespace(release=lambda: calls.append("release")))
    conn = DamengConnection(raw, pool)
    with pytest.raises(RuntimeError):
        conn.close()
    conn.close()
    assert calls == ["rollback", "close", "release"]


def test_config_keeps_mysql_default_and_uses_separate_dm_credentials(monkeypatch, tmp_path):
    import json
    import runpy
    from pathlib import Path

    path = tmp_path / "service_config.json"
    path.write_text(json.dumps({"DB_HOST": "mysql-host", "DB_PASSWORD": "mysql-secret",
                                "DM_HOST": "dm-host", "DM_PASSWORD": "dm-secret",
                                "DM_SCHEMA": "APP_SCHEMA"}), encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(path))
    monkeypatch.delenv("DB_DIALECT", raising=False)
    for key in ("DB_HOST", "DB_PASSWORD", "DM_HOST", "DM_PASSWORD", "DM_SCHEMA"):
        monkeypatch.delenv(key, raising=False)
    config = Path(__file__).resolve().parents[1] / "config.py"
    result = runpy.run_path(str(config))
    assert result["DB_DIALECT"] == "mysql"
    assert result["DB_CONFIG"]["host"] == "mysql-host"
    assert result["DB_CONFIG"]["password"] == "mysql-secret"

    monkeypatch.setenv("DB_DIALECT", "DM")
    result = runpy.run_path(str(config))
    assert result["DB_DIALECT"] == "dm"
    assert result["DB_CONFIG"]["host"] == "dm-host"
    assert result["DB_CONFIG"]["password"] == "dm-secret"
    assert result["DB_CONFIG"]["database"] == "APP_SCHEMA"


def test_config_rejects_unknown_database_type(monkeypatch):
    import runpy
    from pathlib import Path

    monkeypatch.setenv("DB_DIALECT", "dmm")
    with pytest.raises(ValueError, match="DB_DIALECT"):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "config.py"))
