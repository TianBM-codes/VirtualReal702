"""
Embedded job runner — executes L1+L2 pipeline inside the L3 web process.

When APP_EMBEDDED_RUNNER=1 (default), the lifespan hook in main.py calls
start_embedded_runner().  It spins up a single daemon thread that polls
registry.db and processes submitted jobs — no separate process needed.

Multi-worker safety (Gunicorn):
  A runner_lock table in registry.db acts as a process mutex.
  Only the worker that successfully writes its PID holds the lock.
  Other workers check every poll cycle; they steal the lock if the
  heartbeat is older than _LOCK_TTL seconds (runner died silently).
"""
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT    = Path(__file__).resolve().parent.parent.parent.parent
_DUMP_SCRIPT  = _REPO_ROOT / "src" / "l1" / "abaqus_dump.py"
_PACK_SCRIPT  = _REPO_ROOT / "src" / "l1" / "l1_pack.py"
_INGEST_SCRIPT = _REPO_ROOT / "src" / "l2" / "ingest.py"

_LOCK_TTL    = 30   # seconds — steal lock if heartbeat is older than this
_HB_INTERVAL = 15   # seconds between heartbeat refreshes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmbeddedRunner:
    def __init__(
        self,
        registry_db: str,
        data_root: str,
        abaqus_cmd: str = "abaqus",
        poll_interval: int = 10,
    ):
        self.registry_db   = registry_db
        self.data_root     = data_root
        self.abaqus_cmd    = abaqus_cmd
        self.poll_interval = poll_interval
        self._pid = os.getpid()

    # ── SQLite helpers ─────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.registry_db, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_lock_table(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runner_lock (
                singleton INTEGER PRIMARY KEY DEFAULT 1,
                pid       INTEGER,
                heartbeat TEXT
            )
        """)

    # ── Process lock ───────────────────────────────────────────────────────────

    def try_acquire_lock(self) -> bool:
        """
        Return True if this process successfully acquired the runner lock.
        Another worker holding a fresh lock → return False.
        """
        try:
            with self._connect() as conn:
                self._ensure_lock_table(conn)
                row = conn.execute(
                    "SELECT pid, heartbeat FROM runner_lock WHERE singleton=1"
                ).fetchone()
                if row and row["pid"] != self._pid:
                    try:
                        hb = datetime.fromisoformat(row["heartbeat"])
                        if hb.tzinfo is None:
                            hb = hb.replace(tzinfo=timezone.utc)
                        age = (datetime.now(timezone.utc) - hb).total_seconds()
                        if age < _LOCK_TTL:
                            return False   # another runner is alive
                    except Exception:
                        pass   # malformed heartbeat → steal

                conn.execute(
                    "INSERT OR REPLACE INTO runner_lock (singleton, pid, heartbeat) "
                    "VALUES (1, ?, ?)",
                    (self._pid, _now_iso()),
                )
                return True
        except Exception:
            logger.exception("Runner: lock acquisition failed")
            return False

    def _refresh_lock(self):
        try:
            with self._connect() as conn:
                self._ensure_lock_table(conn)
                conn.execute(
                    "UPDATE runner_lock SET heartbeat=? WHERE singleton=1 AND pid=?",
                    (_now_iso(), self._pid),
                )
        except Exception:
            logger.warning("Runner: heartbeat refresh failed")

    # ── Job status helpers ─────────────────────────────────────────────────────

    def _update_status(self, odb_id: str, status: str, **fields) -> None:
        allowed = {
            "l1_started_at", "l1_done_at",
            "l2_started_at", "l2_done_at",
            "error_msg", "node_count", "instance_count",
        }
        kv = {k: v for k, v in fields.items() if k in allowed}
        kv["status"] = status
        set_clause = ", ".join(f"{k}=?" for k in kv)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE odb_jobs SET {set_clause} WHERE odb_id=?",
                (*kv.values(), odb_id),
            )

    def _claim_submitted(self):
        """Atomically claim one submitted job → l1_running.
        Returns (odb_id, odb_path, workspace_abs) or (None, None, None)."""
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
                return None, None, None
            row = conn.execute(
                "SELECT odb_id, odb_path, workspace FROM odb_jobs WHERE status='l1_running'"
            ).fetchone()
        if row is None:
            return None, None, None

        stored_ws = row["workspace"]
        is_abs = os.path.isabs(stored_ws) or bool(re.match(r'^[A-Za-z]:[/\\]', stored_ws))
        workspace_abs = stored_ws if is_abs else os.path.join(self.data_root, stored_ws)
        return row["odb_id"], row["odb_path"], workspace_abs

    def _read_l1_stats(self, workspace: str):
        manifest = os.path.join(workspace, "manifest.db")
        try:
            with sqlite3.connect(manifest, timeout=5.0) as conn:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if "meta" in tables:
                    rows = {r[0]: r[1] for r in conn.execute(
                        "SELECT key, value FROM meta"
                    ).fetchall()}
                    return int(rows.get("node_count", 0)), int(rows.get("instance_count", 0))
                if "instances" in tables:
                    row = conn.execute(
                        "SELECT SUM(node_count), COUNT(*) FROM instances"
                    ).fetchone()
                    return int(row[0] or 0), int(row[1] or 0)
        except Exception:
            logger.warning("Runner: cannot read manifest.db stats in %s", workspace)
        return 0, 0

    # ── Subprocess helpers ─────────────────────────────────────────────────────

    def _run_streaming(self, cmd: list, odb_id: str, label: str):
        """Run subprocess; stream each line to logger. Returns (returncode, last_50_lines)."""
        lines = []
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logger.info("[%s] %s: %s", odb_id, label, line)
                lines.append(line)
        proc.wait()
        return proc.returncode, "\n".join(lines[-50:])

    # ── L1 pipeline ───────────────────────────────────────────────────────────

    def _run_l1_odb(self, odb_id: str, odb_path: str, workspace: str) -> bool:
        logger.info("[%s] L1 phase 1: abaqus_dump.py", odb_id)
        rc, tail = self._run_streaming(
            [self.abaqus_cmd, "python", str(_DUMP_SCRIPT),
             "--odb", odb_path, "--out", workspace],
            odb_id, "abaqus_dump",
        )
        if rc != 0:
            self._update_status(odb_id, "error",
                error_msg=f"abaqus_dump failed: {tail}\n"
                          f"[Check {workspace}/abaqus.log for full output]")
            return False

        logger.info("[%s] L1 phase 2: l1_pack.py", odb_id)
        rc, tail = self._run_streaming(
            [sys.executable, str(_PACK_SCRIPT), "--workspace", workspace],
            odb_id, "l1_pack",
        )
        if rc != 0:
            self._update_status(odb_id, "error", error_msg=f"l1_pack failed: {tail}")
            return False
        return True

    def _run_l1_inp(self, odb_id: str, inp_path: str, workspace: str) -> bool:
        logger.info("[%s] L1 (INP): parsing %s", odb_id, inp_path)
        try:
            from src.inp import parse_inp
            from src.inp.exporter import export_l1
            model = parse_inp(inp_path)
            export_l1(model, workspace)
        except Exception as exc:
            self._update_status(odb_id, "error",
                error_msg=f"INP parse/export failed: {exc}")
            logger.exception("[%s] INP L1 failed", odb_id)
            return False
        logger.info("[%s] L1 (INP) done", odb_id)
        return True

    def _run_l1(self, odb_id: str, source_path: str, workspace: str) -> bool:
        if source_path.lower().endswith(".inp"):
            ok = self._run_l1_inp(odb_id, source_path, workspace)
        else:
            ok = self._run_l1_odb(odb_id, source_path, workspace)

        if not ok:
            return False

        nc, ic = self._read_l1_stats(workspace)
        self._update_status(odb_id, "l1_done",
            l1_done_at=_now_iso(), node_count=nc, instance_count=ic)
        logger.info("[%s] L1 done (nodes=%d, instances=%d)", odb_id, nc, ic)
        return True

    # ── L2 pipeline ───────────────────────────────────────────────────────────

    def _run_l2(self, odb_id: str, workspace: str) -> bool:
        if not _INGEST_SCRIPT.exists():
            logger.info("[%s] L2 script not found, skipping — marking ready", odb_id)
            self._update_status(odb_id, "ready", l2_done_at=_now_iso())
            return True

        self._update_status(odb_id, "l2_running", l2_started_at=_now_iso())
        logger.info("[%s] L2: ingest.py", odb_id)

        ret = subprocess.run(
            [sys.executable, str(_INGEST_SCRIPT), "--workspace", workspace],
            capture_output=True, text=True,
        )
        if ret.returncode != 0:
            self._update_status(odb_id, "l1_done",
                error_msg="ingest failed: " + ret.stderr[-2000:])
            logger.error("[%s] L2 failed (rc=%d)", odb_id, ret.returncode)
            return False

        self._update_status(odb_id, "ready", l2_done_at=_now_iso())
        logger.info("[%s] ready", odb_id)
        return True

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Daemon thread entry point: poll → claim → run → repeat."""
        logger.info(
            "Embedded runner started (pid=%d, registry=%s)",
            self._pid, self.registry_db,
        )
        last_hb = 0.0

        while True:
            # Refresh process lock heartbeat
            now = time.monotonic()
            if now - last_hb >= _HB_INTERVAL:
                self._refresh_lock()
                last_hb = now

            odb_id, odb_path, workspace = self._claim_submitted()
            if odb_id is None:
                time.sleep(self.poll_interval)
                continue

            logger.info("Runner claimed job %s", odb_id)
            try:
                ok = self._run_l1(odb_id, odb_path, workspace)
                if ok:
                    self._run_l2(odb_id, workspace)
            except Exception:
                logger.exception("Runner: unhandled error processing %s", odb_id)
                try:
                    self._update_status(odb_id, "error",
                        error_msg="Unhandled runner exception — check server logs")
                except Exception:
                    pass


# ── Public entry point ────────────────────────────────────────────────────────

def start_embedded_runner(
    registry_db: str,
    data_root: str,
    abaqus_cmd: str = "abaqus",
    poll_interval: int = 10,
) -> bool:
    """
    Try to start the embedded runner daemon thread.

    Returns True  — this worker acquired the lock and started the thread.
    Returns False — another worker already holds the lock (this worker skips).
    """
    runner = EmbeddedRunner(registry_db, data_root, abaqus_cmd, poll_interval)
    if not runner.try_acquire_lock():
        logger.debug(
            "Embedded runner: lock held by another worker (pid=%d), skipping",
            os.getpid(),
        )
        return False

    t = threading.Thread(target=runner.run, daemon=True, name="embedded-runner")
    t.start()
    return True
