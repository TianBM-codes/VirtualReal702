"""
registry_repo.py — Read/write access to the global registry.db (odb_jobs table).

This module is the *only* place that touches registry.db.
Each method opens a short-lived connection, commits/rolls back, then closes.
All timestamps are ISO-8601 strings generated on the Python side (_now_iso),
never SQL datetime() expressions.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS odb_jobs (
    odb_id           TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    odb_path         TEXT NOT NULL,
    workspace        TEXT NOT NULL,
    status           TEXT NOT NULL,
    created_at       TEXT,
    l1_started_at    TEXT,
    l1_done_at       TEXT,
    l2_started_at    TEXT,
    l2_done_at       TEXT,
    error_msg        TEXT,
    last_heartbeat   TEXT,
    odb_size_bytes   INTEGER,
    node_count       INTEGER,
    instance_count   INTEGER
);
"""

_ALLOWED_UPDATE_FIELDS = frozenset({
    "l1_started_at", "l1_done_at",
    "l2_started_at", "l2_done_at",
    "error_msg", "node_count", "instance_count",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryRepo:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.ensure_schema()

    # ── internal ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── schema ──────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create odb_jobs table if it does not exist."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── write ────────────────────────────────────────────────────────────────

    def create_job(self, odb_id: str, display_name: str,
                   odb_path: str, workspace: str,
                   odb_size_bytes: int) -> None:
        """Insert a new job with status='submitted'."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO odb_jobs
                   (odb_id, display_name, odb_path, workspace,
                    status, created_at, odb_size_bytes)
                   VALUES (?,?,?,?,'submitted',?,?)""",
                (odb_id, display_name, odb_path, workspace,
                 _now_iso(), odb_size_bytes),
            )

    def update_status(self, odb_id: str, status: str, **extra_fields) -> None:
        """
        Update status and any subset of allowed extra fields.
        Extra field values must be plain Python scalars (str, int, None).
        Do NOT pass SQL expressions like "datetime('now')".
        """
        fields = {k: v for k, v in extra_fields.items()
                  if k in _ALLOWED_UPDATE_FIELDS}
        fields["status"] = status
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE odb_jobs SET {set_clause} WHERE odb_id=?",
                (*fields.values(), odb_id),
            )

    def update_heartbeat(self, odb_id: str) -> None:
        """Called by job_runner every ~60 s to signal it is still alive."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE odb_jobs SET last_heartbeat=? WHERE odb_id=?",
                (_now_iso(), odb_id),
            )

    def delete_job(self, odb_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM odb_jobs WHERE odb_id=?", (odb_id,))

    def mark_stuck_jobs_as_error(self, timeout_minutes: int = 10) -> int:
        """
        Mark l1_running / l2_running jobs whose last_heartbeat is stale (or NULL)
        as 'error'. Returns the number of rows affected.
        Called at L3 startup to recover from a crashed job_runner.
        """
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE odb_jobs
                   SET status='error',
                       error_msg='Job runner process appears to have crashed (heartbeat timeout)'
                   WHERE status IN ('l1_running', 'l2_running')
                     AND (last_heartbeat IS NULL
                          OR datetime(last_heartbeat) < datetime('now', ?))""",
                (f"-{timeout_minutes} minutes",),
            )
            return cur.rowcount

    # ── claim (atomic, safe for multi-process competition) ──────────────────

    def claim_submitted(self) -> Optional[str]:
        """
        Atomically claim one 'submitted' job → 'l1_running'.
        Returns the odb_id that was claimed, or None if queue is empty.
        SQLite WAL guarantees this UPDATE is atomic across processes.
        """
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE odb_jobs
                   SET status='l1_running', l1_started_at=?
                   WHERE odb_id = (
                     SELECT odb_id FROM odb_jobs
                     WHERE status='submitted'
                     ORDER BY created_at LIMIT 1
                   )""",
                (_now_iso(),),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT odb_id FROM odb_jobs WHERE status='l1_running'"
            ).fetchone()
            return row["odb_id"] if row else None

    # ── read ─────────────────────────────────────────────────────────────────

    def get_job(self, odb_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM odb_jobs WHERE odb_id=?", (odb_id,)
            ).fetchone()

    def list_jobs(self) -> list:
        """Return all jobs ordered by created_at DESC."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM odb_jobs ORDER BY created_at DESC"
            ).fetchall()

    def resolve_workspace(self, stored: str, data_root: str) -> str:
        """
        Convert the value stored in the workspace column to an absolute path.

        Storage format (from newest to oldest):
          - New-style (post-fix): bare odb_id, e.g. "7a5f6938-..."
            → resolved as os.path.join(data_root, stored)
          - Old-style (pre-fix): absolute path set at job-creation time,
            e.g. "/data/7a5f..." or "E:\\code\\...\\7a5f..."
            → used directly if the path exists on this machine;
            → remapped to data_root/<basename> otherwise (cross-platform move).
        """
        import os, re
        # Detect absolute paths: Unix (/...) or Windows (C:\... / C:/...)
        is_abs = os.path.isabs(stored) or bool(re.match(r'^[A-Za-z]:[/\\]', stored))
        if not is_abs:
            # New-style: relative identifier (just odb_id)
            return os.path.join(data_root, stored)
        # Old-style: absolute path — use directly if it exists on this machine
        if os.path.exists(stored):
            return stored
        # Cross-platform migration: remap using only the last path component
        # e.g. "E:\code\model\7a5f..." → data_root + "7a5f..."
        basename = re.split(r'[/\\]', stored.rstrip('/\\'))[-1]
        return os.path.join(data_root, basename)

    def is_runner_alive(self, ttl_seconds: int = 30) -> bool:
        """
        Return True if a job runner has refreshed its heartbeat within the last
        `ttl_seconds` seconds.  Reads the runner_lock table written by runner_thread.py.
        Returns False if the table doesn't exist yet or the heartbeat is stale.
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT heartbeat FROM runner_lock WHERE singleton=1"
                ).fetchone()
                if row is None or not row["heartbeat"]:
                    return False
                hb = datetime.fromisoformat(row["heartbeat"])
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - hb).total_seconds()
                return age < ttl_seconds
        except Exception:
            return False

    def list_ready_or_l1done(self) -> list:
        """
        Used by the L3 polling thread.
        Returns rows whose status is 'ready' or 'l1_done'.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT odb_id, workspace, status FROM odb_jobs "
                "WHERE status IN ('ready', 'l1_done')"
            ).fetchall()
