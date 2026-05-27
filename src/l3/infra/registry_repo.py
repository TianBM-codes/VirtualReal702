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

CREATE TABLE IF NOT EXISTS job_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    odb_id  TEXT    NOT NULL,
    ts      TEXT    NOT NULL,
    level   TEXT    NOT NULL DEFAULT 'info',
    stage   TEXT,
    message TEXT    NOT NULL,
    percent INTEGER
);
CREATE INDEX IF NOT EXISTS idx_job_logs_odb ON job_logs(odb_id, id);

CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    workspace    TEXT NOT NULL,
    inp_path     TEXT,
    source_type  TEXT NOT NULL DEFAULT 'inp',
    geom_status  TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS result_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(project_id),
    result_group    TEXT NOT NULL,
    display_name    TEXT,
    source_path     TEXT NOT NULL,
    source_file     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    parse_options   TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(project_id, result_group)
);
"""

_ALLOWED_UPDATE_FIELDS = frozenset({
    "l1_started_at", "l1_done_at",
    "l2_started_at", "l2_done_at",
    "error_msg", "node_count", "instance_count",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_log_ts() -> str:
    """Human-readable Beijing time (UTC+8) for job_logs.ts column."""
    from datetime import timedelta
    tz_bj = timezone(timedelta(hours=8))
    return datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")


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
            for migration in [
                "ALTER TABLE projects ADD COLUMN source_type TEXT NOT NULL DEFAULT 'inp'",
                "ALTER TABLE job_logs ADD COLUMN percent INTEGER",
            ]:
                try:
                    conn.execute(migration)
                except Exception:
                    pass

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
            conn.execute("DELETE FROM job_logs WHERE odb_id=?", (odb_id,))
            conn.execute("DELETE FROM odb_jobs WHERE odb_id=?", (odb_id,))

    # ── job_logs ─────────────────────────────────────────────────────────────

    def append_job_log(self, odb_id: str, level: str, message: str,
                       stage: str = None, percent: Optional[int] = None) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO job_logs (odb_id, ts, level, stage, message, percent)"
                    " VALUES (?,?,?,?,?,?)",
                    (odb_id, _now_log_ts(), level, stage, message, percent),
                )
        except Exception:
            pass

    def clear_job_logs(self, odb_id: str) -> int:
        """删除指定 odb_id / project_id 的全部日志，返回删除行数。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM job_logs WHERE odb_id=?", (odb_id,))
            return cur.rowcount

    def get_job_logs(self, odb_id: str, since_id: int = 0,
                     limit: int = 500) -> list:
        with self._connect() as conn:
            return conn.execute(
                "SELECT id, ts, level, stage, message, percent FROM job_logs"
                " WHERE odb_id=? AND id>? ORDER BY id LIMIT ?",
                (odb_id, since_id, limit),
            ).fetchall()

    def get_current_percent(self, odb_id: str) -> Optional[int]:
        """返回该 job/project 最新一条非 null 的 percent 值，用于进度条。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT percent FROM job_logs"
                " WHERE odb_id=? AND percent IS NOT NULL ORDER BY id DESC LIMIT 1",
                (odb_id,),
            ).fetchone()
        return row["percent"] if row else None

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
            candidate = conn.execute(
                "SELECT odb_id FROM odb_jobs WHERE status='submitted' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if candidate is None:
                return None
            odb_id = candidate["odb_id"]
            cur = conn.execute(
                "UPDATE odb_jobs SET status='l1_running', l1_started_at=?"
                " WHERE odb_id=? AND status='submitted'",
                (_now_iso(), odb_id),
            )
            return odb_id if cur.rowcount > 0 else None

    # ── read ─────────────────────────────────────────────────────────────────

    def get_job(self, odb_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM odb_jobs WHERE odb_id=?", (odb_id,)
            ).fetchone()
            return dict(row) if row is not None else None

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

        def _join_with_root_style(root: str, leaf: str) -> str:
            """
            Join using the style implied by data_root rather than the current OS.
            This keeps '/data/...' stable in tests even on Windows, while still
            producing native-looking paths for Windows absolute roots.
            """
            if re.match(r'^[A-Za-z]:[/\\]', root):
                return root.rstrip('/\\') + '\\' + leaf
            if root.startswith('/'):
                return root.rstrip('/\\') + '/' + leaf
            return os.path.join(root, leaf)

        # Detect absolute paths in a cross-platform way:
        #   - Unix style: /data/...
        #   - Windows style: C:\... or C:/...
        is_abs = (
            os.path.isabs(stored)
            or stored.startswith('/')
            or bool(re.match(r'^[A-Za-z]:[/\\]', stored))
        )
        if not is_abs:
            # New-style: relative identifier (just odb_id)
            return _join_with_root_style(data_root, stored)
        # Old-style: absolute path — use directly if it exists on this machine
        if os.path.exists(stored):
            return stored
        # Cross-platform migration: remap using only the last path component
        # e.g. "E:\code\model\7a5f..." → data_root + "7a5f..."
        basename = re.split(r'[/\\]', stored.rstrip('/\\'))[-1]
        return _join_with_root_style(data_root, basename)

    def is_runner_alive(self, ttl_seconds: int = 30) -> bool:
        """
        Return True if a job runner is genuinely alive:
          1. runner_lock heartbeat is within ttl_seconds, AND
          2. the recorded PID is still running (os.kill check).
        Returns False if the table doesn't exist, heartbeat is stale, or PID is dead.
        """
        import os as _os
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT pid, heartbeat FROM runner_lock WHERE singleton=1"
                ).fetchone()
                if row is None or not row["heartbeat"]:
                    return False
                hb = datetime.fromisoformat(row["heartbeat"])
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - hb).total_seconds()
                if age >= ttl_seconds:
                    return False
                # Heartbeat is fresh — also verify the PID is still alive
                try:
                    _os.kill(row["pid"], 0)
                except Exception:
                    # On Windows, probing a dead PID may raise SystemError
                    # instead of ProcessLookupError. Any failure means the
                    # runner should be considered dead.
                    return False
                return True
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

    # ── projects ─────────────────────────────────────────────────────────────

    def create_project(self, project_id: str, workspace: str,
                       inp_path: str = None,
                       source_type: str = "inp") -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects"
                " (project_id, workspace, inp_path, source_type, geom_status, created_at, updated_at)"
                " VALUES (?,?,?,?,'pending',?,?)",
                (project_id, workspace, inp_path, source_type, now, now),
            )

    def list_projects(self) -> list:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()

    def get_project(self, project_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()

    def update_project_geom_status(self, project_id: str, status: str,
                                   error_message: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET geom_status=?, updated_at=? WHERE project_id=?",
                (status, _now_iso(), project_id),
            )
            if status == "error" and error_message is not None:
                # 批量把该 project 下所有 pending result_groups 置为 error
                conn.execute(
                    "UPDATE result_groups SET status='error', error_message=?, updated_at=?"
                    " WHERE project_id=? AND status='pending'",
                    (error_message, _now_iso(), project_id),
                )

    def claim_l2_rerun(self, project_id: str) -> bool:
        """原子将 geom_status 从 ready/error 改为 l2_pending，交由 job_runner 执行。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE projects SET geom_status='l2_pending', updated_at=?"
                " WHERE project_id=? AND geom_status IN ('ready','error')",
                (_now_iso(), project_id),
            )
            return cur.rowcount == 1

    def claim_l2_pending(self) -> Optional[tuple]:
        """job_runner 调用：原子认领一个 geom_status='l2_pending' 的 project → 'l2_running'。
        返回 (project_id, workspace) 或 None。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE projects SET geom_status='l2_running', updated_at=?"
                " WHERE project_id=("
                "  SELECT project_id FROM projects WHERE geom_status='l2_pending'"
                "  ORDER BY updated_at LIMIT 1"
                ")",
                (_now_iso(),),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT project_id, workspace FROM projects"
                " WHERE geom_status='l2_running' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            return (row["project_id"], row["workspace"]) if row else None

    def claim_pending_project(self) -> Optional[str]:
        """原子认领一个 geom_status='pending' 的 project → 'running'。返回 project_id 或 None。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE projects SET geom_status='running', updated_at=?"
                " WHERE project_id=("
                "  SELECT project_id FROM projects WHERE geom_status='pending'"
                "  ORDER BY created_at LIMIT 1"
                ")",
                (_now_iso(),),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT project_id FROM projects WHERE geom_status='running'"
                " ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            return row["project_id"] if row else None

    def adopt_default_result_group(self, project_id: str, result_group: str,
                                    display_name: str, source_path: str,
                                    source_file: str = None) -> bool:
        """INSERT OR IGNORE a result_group with status='ready'. Returns True if inserted."""
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO result_groups"
                " (project_id, result_group, display_name, source_path, source_file,"
                "  status, parse_options, created_at, updated_at)"
                " VALUES (?,?,?,?,?,'ready',NULL,?,?)",
                (project_id, result_group, display_name,
                 source_path or '', source_file, now, now),
            )
            return cur.rowcount > 0

    def reset_project_for_retry(self, project_id: str,
                                source_path: str, source_type: str) -> None:
        """将 error 状态的 project 重置为 pending（重新提交时调用），同时更新 source_path。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM job_logs WHERE odb_id=?", (project_id,))
            conn.execute(
                "UPDATE projects SET geom_status='pending', inp_path=?, source_type=?,"
                " updated_at=? WHERE project_id=? AND geom_status='error'",
                (source_path, source_type, _now_iso(), project_id),
            )

    def delete_project(self, project_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM job_logs WHERE odb_id=?", (project_id,))
            conn.execute("DELETE FROM result_groups WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))

    # ── result_groups ────────────────────────────────────────────────────────

    def create_result_group(self, project_id: str, result_group: str,
                            display_name: str, source_path: str,
                            source_file: str, parse_options: str) -> None:
        """插入新 result_group，status='pending'。parse_options 为 JSON 字符串。"""
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO result_groups"
                " (project_id, result_group, display_name, source_path,"
                "  source_file, status, parse_options, created_at, updated_at)"
                " VALUES (?,?,?,?,?,'pending',?,?,?)",
                (project_id, result_group, display_name, source_path,
                 source_file, parse_options, now, now),
            )

    def get_result_group(self, project_id: str,
                         result_group: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM result_groups WHERE project_id=? AND result_group=?",
                (project_id, result_group),
            ).fetchone()

    def list_result_groups(self, project_id: str) -> list:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM result_groups WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()

    def project_has_active_tasks(self, project_id: str) -> bool:
        """Return True if project geometry or any result group is queued/running."""
        with self._connect() as conn:
            proj = conn.execute(
                "SELECT geom_status FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            if proj is None:
                return False
            if proj["geom_status"] in ("pending", "running"):
                return True
            row = conn.execute(
                "SELECT 1 FROM result_groups"
                " WHERE project_id=? AND status IN ('pending', 'running') LIMIT 1",
                (project_id,),
            ).fetchone()
            return row is not None

    def clone_project_records(self, source_project_id: str, new_project_id: str) -> None:
        """
        Clone registry rows for one project and all its result_groups.
        The workspace field is set to the bare new_project_id.
        """
        now = _now_iso()
        with self._connect() as conn:
            src = conn.execute(
                "SELECT * FROM projects WHERE project_id=?", (source_project_id,)
            ).fetchone()
            if src is None:
                raise ValueError(f"Project '{source_project_id}' not found")
            existing = conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (new_project_id,)
            ).fetchone()
            if existing is not None:
                raise sqlite3.IntegrityError(f"Project '{new_project_id}' already exists")

            conn.execute(
                "INSERT INTO projects"
                " (project_id, workspace, inp_path, source_type, geom_status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    new_project_id,
                    new_project_id,
                    src["inp_path"],
                    src["source_type"],
                    src["geom_status"],
                    now,
                    now,
                ),
            )

            rows = conn.execute(
                "SELECT * FROM result_groups WHERE project_id=? ORDER BY created_at",
                (source_project_id,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "INSERT INTO result_groups"
                    " (project_id, result_group, display_name, source_path,"
                    "  source_file, status, parse_options, error_message,"
                    "  created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_project_id,
                        row["result_group"],
                        row["display_name"],
                        row["source_path"],
                        row["source_file"],
                        row["status"],
                        row["parse_options"],
                        row["error_message"],
                        now,
                        now,
                    ),
                )

    def update_result_group_status(self, project_id: str, result_group: str,
                                   status: str,
                                   error_message: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE result_groups SET status=?, error_message=?, updated_at=?"
                " WHERE project_id=? AND result_group=?",
                (status, error_message, _now_iso(), project_id, result_group),
            )

    def update_result_group_display_name(self, project_id: str, result_group: str,
                                         display_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE result_groups SET display_name=?, updated_at=?"
                " WHERE project_id=? AND result_group=?",
                (display_name, _now_iso(), project_id, result_group),
            )

    def reset_result_group_for_retry(self, project_id: str,
                                     result_group: str) -> None:
        """将 error 状态的 result_group 重置为 pending（重新提交时调用）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE result_groups SET status='pending', error_message=NULL, updated_at=?"
                " WHERE project_id=? AND result_group=? AND status='error'",
                (_now_iso(), project_id, result_group),
            )

    def reset_result_group_for_resubmit(self, project_id: str, result_group: str,
                                         source_path: str, source_file: str,
                                         display_name: str,
                                         parse_options: str) -> None:
        """将非 running 状态的 result_group 重置为 pending，同时更新源文件信息。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE result_groups"
                " SET status='pending', error_message=NULL,"
                "     source_path=?, source_file=?, display_name=?, parse_options=?,"
                "     updated_at=?"
                " WHERE project_id=? AND result_group=? AND status != 'running'",
                (source_path, source_file, display_name, parse_options,
                 _now_iso(), project_id, result_group),
            )

    def claim_pending_result_group(self, project_id: str) -> Optional[sqlite3.Row]:
        """
        原子认领 project 下一个 pending result_group → running。
        仅在 project.geom_status='ready' 时调用。
        返回完整 row 或 None。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE result_groups SET status='running', updated_at=?"
                " WHERE id=("
                "  SELECT id FROM result_groups"
                "  WHERE project_id=? AND status='pending'"
                "  ORDER BY created_at LIMIT 1"
                ")",
                (_now_iso(), project_id),
            )
            if cur.rowcount == 0:
                return None
            return conn.execute(
                "SELECT * FROM result_groups"
                " WHERE project_id=? AND status='running'"
                " ORDER BY updated_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()

    def delete_result_group(self, project_id: str, result_group: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM result_groups WHERE project_id=? AND result_group=?",
                (project_id, result_group),
            )
