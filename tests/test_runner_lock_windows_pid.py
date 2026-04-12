import sqlite3
from datetime import datetime, timezone

from src.l3.infra.registry_repo import RegistryRepo
from src.l3.infra.runner_thread import EmbeddedRunner


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_embedded_runner_steals_stale_lock_when_pid_probe_raises_systemerror(monkeypatch, tmp_path):
    db_path = tmp_path / "registry.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE runner_lock (
            singleton INTEGER PRIMARY KEY DEFAULT 1,
            pid INTEGER,
            heartbeat TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO runner_lock (singleton, pid, heartbeat) VALUES (1, ?, ?)",
        (32716, _now_iso()),
    )
    conn.commit()
    conn.close()

    runner = EmbeddedRunner(str(db_path), str(tmp_path))
    monkeypatch.setattr("src.l3.infra.runner_thread.os.kill", lambda pid, sig: (_ for _ in ()).throw(SystemError("win pid probe failed")))

    assert runner.try_acquire_lock() is True

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT pid FROM runner_lock WHERE singleton=1").fetchone()
    conn.close()
    assert row[0] == runner._pid


def test_registry_repo_reports_runner_dead_when_pid_probe_raises_systemerror(monkeypatch, tmp_path):
    db_path = tmp_path / "registry.db"
    repo = RegistryRepo(str(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runner_lock (
            singleton INTEGER PRIMARY KEY DEFAULT 1,
            pid INTEGER,
            heartbeat TEXT
        )
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO runner_lock (singleton, pid, heartbeat) VALUES (1, ?, ?)",
        (32716, _now_iso()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("os.kill", lambda pid, sig: (_ for _ in ()).throw(SystemError("win pid probe failed")))

    assert repo.is_runner_alive() is False
