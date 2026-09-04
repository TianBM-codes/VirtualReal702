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
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from src.utils.file_fetch import (
    materialize_source_file,
    resolve_runner_source,
)
from src.l3.infra.manifest_repo import ManifestRepo as _ManifestRepo

logger = logging.getLogger(__name__)

_REPO_ROOT    = Path(__file__).resolve().parent.parent.parent.parent
_DUMP_SCRIPT  = _REPO_ROOT / "src" / "l1" / "abaqus_dump.py"
_PACK_SCRIPT  = _REPO_ROOT / "src" / "l1" / "l1_pack.py"
_INP_PACK_SCRIPT = _REPO_ROOT / "src" / "l1" / "inp_pack.py"
_BDF_PACK_SCRIPT = _REPO_ROOT / "src" / "l1" / "bdf_pack.py"
_CDB_PACK_SCRIPT = _REPO_ROOT / "src" / "l1" / "cdb_pack.py"
_SIPESC_UNV_PACK_SCRIPT = _REPO_ROOT / "src" / "l1" / "sipesc_unv_pack.py"
_INGEST_SCRIPT = _REPO_ROOT / "src" / "l2" / "ingest.py"

_LOCK_TTL    = 30   # seconds — steal lock if heartbeat is older than this
_HB_INTERVAL = 15   # seconds between heartbeat refreshes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _materialize_runner_source(source_path: str, workspace: str, source_file: str = None) -> str:
    # 统一入口：URL 下载 / 本地 ASCII 原样 / 本地非 ASCII 名挂 ASCII 硬链接（0 复制，原文件不动）。
    return resolve_runner_source(source_path, workspace, source_file)


def _append_extract_filters(cmd: list, parse_opts: dict) -> list:
    steps = parse_opts.get("steps")
    if isinstance(steps, str):
        steps_arg = steps.strip()
    elif isinstance(steps, (list, tuple)):
        steps_arg = ",".join(str(s).strip() for s in steps if str(s).strip())
    else:
        steps_arg = None
    if steps_arg:
        cmd.extend(["--steps", steps_arg])

    frames = parse_opts.get("frames")
    frames_arg = None
    if isinstance(frames, str):
        frames_arg = frames.strip()
    elif isinstance(frames, int):
        frames_arg = str(frames)
    elif isinstance(frames, (list, tuple)):
        vals = [str(int(v)) for v in frames]
        if vals:
            frames_arg = ",".join(vals)
    if frames_arg:
        cmd.extend(["--frames", frames_arg])

    field_prefix = parse_opts.get("field_prefix")
    if field_prefix:
        cmd.extend(["--field-prefix", str(field_prefix)])

    return cmd


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

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return True if the process with the given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            # On Windows, os.kill(pid, 0) against a stale PID can surface as
            # SystemError/OSError rather than a clean ProcessLookupError.
            # Treat any probe failure as "not alive" so a stale runner_lock
            # never blocks the embedded runner from starting.
            return False

    def try_acquire_lock(self) -> bool:
        """
        Return True if this process successfully acquired the runner lock.
        Another worker holding a fresh lock AND whose PID is alive → return False.
        Steals the lock immediately if the PID is dead, regardless of heartbeat age.
        """
        try:
            with self._connect() as conn:
                self._ensure_lock_table(conn)
                row = conn.execute(
                    "SELECT pid, heartbeat FROM runner_lock WHERE singleton=1"
                ).fetchone()
                if row and row["pid"] != self._pid:
                    # If the PID is still alive, respect the heartbeat TTL
                    if self._pid_alive(row["pid"]):
                        try:
                            hb = datetime.fromisoformat(row["heartbeat"])
                            if hb.tzinfo is None:
                                hb = hb.replace(tzinfo=timezone.utc)
                            age = (datetime.now(timezone.utc) - hb).total_seconds()
                            if age < _LOCK_TTL:
                                return False   # another runner is alive
                        except Exception:
                            pass   # malformed heartbeat → steal
                    else:
                        logger.info(
                            "Runner: lock held by dead PID %d — stealing immediately",
                            row["pid"],
                        )

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
            candidate = conn.execute(
                "SELECT odb_id FROM odb_jobs WHERE status='submitted' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if candidate is None:
                return None, None, None
            odb_id = candidate["odb_id"]
            cur = conn.execute(
                "UPDATE odb_jobs SET status='l1_running', l1_started_at=?"
                " WHERE odb_id=? AND status='submitted'",
                (_now_iso(), odb_id),
            )
            if cur.rowcount == 0:
                return None, None, None  # race: another process claimed it first
            row = conn.execute(
                "SELECT odb_id, odb_path, workspace FROM odb_jobs WHERE odb_id=?", (odb_id,)
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
        """Run subprocess; stream each line to logger. Returns (returncode, last_50_lines).

        Sets PYTHONUNBUFFERED=1 so Abaqus Python 2.7 flushes print() immediately.
        Also refreshes the runner lock heartbeat every _HB_INTERVAL seconds so the
        lock is not stolen by another worker during long-running L1 jobs.
        """
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # On Windows, CREATE_NEW_PROCESS_GROUP isolates the child from the
        # parent's console group so that Intel MKL/Fortran runtime cleanup
        # in the child does not propagate CTRL_C_EVENT to the parent process.
        _win_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

        lines = []
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding='utf-8', errors='replace', bufsize=1, env=env,
            creationflags=_win_flags,
        )

        start = time.monotonic()
        last_hb = start
        _PROGRESS_INTERVAL = 30  # seconds between "still running" log messages

        def _read_stdout():
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    logger.info("[%s] %s: %s", odb_id, label, line)
                    lines.append(line)

        reader = threading.Thread(target=_read_stdout, daemon=True)
        reader.start()

        while reader.is_alive():
            reader.join(timeout=min(_PROGRESS_INTERVAL, _HB_INTERVAL))
            now = time.monotonic()
            if now - last_hb >= _HB_INTERVAL:
                self._refresh_lock()
                last_hb = now
            if reader.is_alive() and now - start >= _PROGRESS_INTERVAL:
                elapsed = now - start
                logger.info(
                    "[%s] %s: still running… (%.0f s elapsed)",
                    odb_id, label, elapsed,
                )

        proc.wait()
        total = time.monotonic() - start
        logger.info("[%s] %s: finished in %.1f s (exit code %d)", odb_id, label, total, proc.returncode)
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
        logger.info("[%s] L1 (INP): inp_pack.py", odb_id)
        rc, tail = self._run_streaming(
            [sys.executable, str(_INP_PACK_SCRIPT),
             "--inp", inp_path, "--workspace", workspace],
            odb_id, "inp_pack",
        )
        if rc != 0:
            self._update_status(odb_id, "error", error_msg=f"inp_pack failed: {tail}")
            return False
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

        _win_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        ret = subprocess.run(
            [sys.executable, str(_INGEST_SCRIPT), "--workspace", workspace],
            capture_output=True, encoding='utf-8', errors='replace',
            creationflags=_win_flags,
        )
        if ret.returncode != 0:
            self._update_status(odb_id, "error",
                error_msg="[L2] ingest failed: " + ret.stderr[-2000:])
            logger.error("[%s] L2 failed (rc=%d)", odb_id, ret.returncode)
            return False

        self._update_status(odb_id, "ready", l2_done_at=_now_iso())
        logger.info("[%s] ready", odb_id)
        return True

    # ── Project / result_group helpers ────────────────────────────────────────

    def _claim_pending_project(self):
        """原子认领一个 geom_status='pending' 的 project → 'running'。
        返回 (project_id, inp_path, workspace) 或 (None, None, None)。"""
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
                return None, None, None, None, None
            row = conn.execute(
                "SELECT project_id, workspace, inp_path, source_file, source_type FROM projects"
                " WHERE geom_status='running'"
                " ORDER BY updated_at DESC LIMIT 1",
            ).fetchone()
            if row is None:
                return None, None, None, None, None
            ws = row["workspace"]
            if not os.path.isabs(ws):
                ws = os.path.join(self.data_root, row["project_id"])
            source_type = row["source_type"] if "source_type" in row.keys() else "inp"
            return row["project_id"], row["inp_path"], row["source_file"], source_type, ws

    def _update_project_geom_status(self, project_id: str, status: str,
                                    error_message: str = None):
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET geom_status=?, updated_at=? WHERE project_id=?",
                (status, _now_iso(), project_id),
            )
            if status == "error" and error_message:
                conn.execute(
                    "UPDATE result_groups SET status='error', error_message=?, updated_at=?"
                    " WHERE project_id=? AND status='pending'",
                    (error_message, _now_iso(), project_id),
                )

    def _claim_pending_result_group(self):
        """认领一个 pending result_group（要求其 project geom_status='ready'）。
        返回 (project_id, result_group, source_path, parse_options_json, workspace) 或全 None。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE result_groups SET status='running', updated_at=?"
                " WHERE id=("
                "  SELECT rg.id FROM result_groups rg"
                "  JOIN projects p ON rg.project_id = p.project_id"
                "  WHERE rg.status='pending' AND p.geom_status='ready'"
                "  ORDER BY rg.created_at LIMIT 1"
                ")",
                (_now_iso(),),
            )
            if cur.rowcount == 0:
                return None, None, None, None, None, None
            row = conn.execute(
                "SELECT rg.project_id, rg.result_group, rg.source_path,"
                "       rg.source_file, rg.parse_options, p.workspace"
                " FROM result_groups rg"
                " JOIN projects p ON rg.project_id = p.project_id"
                " WHERE rg.status='running'"
                " ORDER BY rg.updated_at DESC LIMIT 1",
            ).fetchone()
            if row is None:
                return None, None, None, None, None, None
            ws = row["workspace"]
            if not os.path.isabs(ws):
                ws = os.path.join(self.data_root, row["project_id"])
            return (row["project_id"], row["result_group"],
                    row["source_path"], row["source_file"], row["parse_options"], ws)

    def _update_result_group_status(self, project_id: str, result_group: str,
                                    status: str, error_message: str = None):
        with self._connect() as conn:
            conn.execute(
                "UPDATE result_groups SET status=?, error_message=?, updated_at=?"
                " WHERE project_id=? AND result_group=?",
                (status, error_message, _now_iso(), project_id, result_group),
            )

    def _cleanup_result_group(self, workspace: str, result_group: str):
        """重试前清理旧文件和旧 manifest rows，防止幽灵元数据。"""
        rg_safe = result_group.replace("/", "__").replace("\\", "__").replace(" ", "_")
        for rel in [
            os.path.join("l1", "results", rg_safe),
            os.path.join("l1_raw", "consistency", rg_safe),
            os.path.join("l1_raw", "rg_{}".format(rg_safe)),
        ]:
            p = os.path.join(workspace, rel)
            if os.path.exists(p):
                shutil.rmtree(p)
                logger.info("Runner: removed stale dir %s", p)

        manifest = os.path.join(workspace, "manifest.db")
        if os.path.exists(manifest):
            with sqlite3.connect(manifest, timeout=5.0) as conn:
                for table in ("steps", "frames", "result_files",
                              "result_blocks", "result_group_meta"):
                    conn.execute(
                        "DELETE FROM {} WHERE result_group=?".format(table),
                        (result_group,),
                    )
            logger.info("Runner: cleaned manifest rows for result_group=%s", result_group)

    # ── Project geometry pipeline ─────────────────────────────────────────────

    def _run_geom_project(self, project_id: str, inp_path: str,
                          workspace: str) -> bool:
        if not inp_path:
            msg = "No INP path stored for project {}".format(project_id)
            logger.error("Runner: %s", msg)
            self._update_project_geom_status(project_id, "error", msg)
            return False

        logger.info("[%s] Geom: inp_pack.py", project_id)
        rc, tail = self._run_streaming(
            [sys.executable, str(_INP_PACK_SCRIPT),
             "--inp", inp_path, "--workspace", workspace],
            project_id, "inp_pack",
        )
        if rc != 0:
            msg = "inp_pack failed: " + tail
            self._update_project_geom_status(project_id, "error", msg)
            return False

        logger.info("[%s] Geom: ingest.py (L2)", project_id)
        ret = subprocess.run(
            [sys.executable, str(_INGEST_SCRIPT), "--workspace", workspace],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if ret.returncode != 0:
            msg = "ingest failed: " + ret.stderr[-2000:]
            self._update_project_geom_status(project_id, "error", msg)
            logger.error("[%s] L2 failed (rc=%d)", project_id, ret.returncode)
            return False

        self._update_project_geom_status(project_id, "ready")
        logger.info("[%s] Geom ready", project_id)
        return True

    def _run_odb_project(self, project_id: str, odb_path: str,
                         workspace: str) -> bool:
        if not odb_path:
            msg = "No ODB path stored for project {}".format(project_id)
            logger.error("Runner: %s", msg)
            self._update_project_geom_status(project_id, "error", msg)
            return False

        logger.info("[%s] Project ODB: abaqus_dump.py", project_id)
        dump_cmd = [self.abaqus_cmd, "python", str(_DUMP_SCRIPT), "--odb", odb_path, "--out", workspace]
        rc, tail = self._run_streaming(dump_cmd, project_id, "project_abaqus_dump")
        if rc != 0:
            msg = "abaqus_dump failed: " + tail
            self._update_project_geom_status(project_id, "error", msg)
            return False

        logger.info("[%s] Project ODB: l1_pack.py", project_id)
        rc, tail = self._run_streaming(
            [sys.executable, str(_PACK_SCRIPT), "--workspace", workspace],
            project_id, "project_l1_pack",
        )
        if rc != 0:
            msg = "l1_pack failed: " + tail
            self._update_project_geom_status(project_id, "error", msg)
            return False

        logger.info("[%s] Project ODB: ingest.py (L2)", project_id)
        ret = subprocess.run(
            [sys.executable, str(_INGEST_SCRIPT), "--workspace", workspace],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if ret.returncode != 0:
            msg = "ingest failed: " + ret.stderr[-2000:]
            self._update_project_geom_status(project_id, "error", msg)
            logger.error("[%s] Project ODB L2 failed (rc=%d)", project_id, ret.returncode)
            return False

        self._update_project_geom_status(project_id, "ready")
        logger.info("[%s] Project ODB ready", project_id)
        try:
            self._adopt_odb_default_results(project_id, odb_path, workspace)
        except Exception:
            logger.exception("[%s] Failed to auto-register default result_group", project_id)
        return True

    def _adopt_odb_default_results(self, project_id: str, odb_path: str, workspace: str):
        """Tag NULL result_group in manifest + register in registry.db as 'default_result'."""
        rg_name = "default_result"
        source_file = os.path.basename(odb_path) if odb_path else None
        original_source_path = odb_path
        original_source_file = source_file
        try:
            with self._connect() as conn:
                project_row = conn.execute(
                    "SELECT inp_path, source_file, original_inp_path, original_source_file"
                    " FROM projects WHERE project_id=?",
                    (project_id,),
                ).fetchone()
            if project_row is not None:
                original_source_path = project_row["original_inp_path"] or project_row["inp_path"] or odb_path
                original_source_file = project_row["original_source_file"] or project_row["source_file"] or source_file
                source_file = project_row["source_file"] or source_file
        except Exception:
            pass
        display_name = os.path.splitext(original_source_file or source_file or rg_name)[0]
        manifest = _ManifestRepo(workspace)
        migrated = manifest.adopt_null_result_group(rg_name, display_name, source_file)
        logger.debug("[%s] adopt_null_result_group migrated=%s", project_id, migrated)
        if migrated:
            now = _now_iso()
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO result_groups"
                    " (project_id, result_group, display_name, source_path, source_file,"
                    "  original_source_path, original_source_file,"
                    "  status, parse_options, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,'ready',NULL,?,?)",
                    (
                        project_id,
                        rg_name,
                        display_name,
                        odb_path or '',
                        source_file or '',
                        original_source_path or '',
                        original_source_file or '',
                        now,
                        now,
                    ),
                )
            logger.info("[%s] Adopted ODB results as result_group='default_result'", project_id)

    def _run_project(self, project_id: str, source_path: str,
                     source_type: str, workspace: str) -> bool:
        if source_type == "odb":
            return self._run_odb_project(project_id, source_path, workspace)
        if source_type in ("bdf", "cdb", "sipesc_unv"):
            if source_type == "bdf":
                script, arg, stage = _BDF_PACK_SCRIPT, "--bdf", "bdf_geometry"
            elif source_type == "cdb":
                script, arg, stage = _CDB_PACK_SCRIPT, "--cdb", "cdb_geometry"
            else:
                script, arg, stage = _SIPESC_UNV_PACK_SCRIPT, "--unv", "sipesc_unv_geometry"
            cmd = [sys.executable, str(script), arg, source_path, "--workspace", workspace]
            if source_type == "sipesc_unv":
                cmd.extend(["--mode", "geometry"])
            rc, tail = self._run_streaming(
                cmd, project_id, stage)
            if rc != 0:
                self._update_project_geom_status(
                    project_id, "error", source_type + " geometry pack failed: " + tail)
                return False
            ret = subprocess.run(
                [sys.executable, str(_INGEST_SCRIPT), "--workspace", workspace],
                capture_output=True, encoding="utf-8", errors="replace",
            )
            if ret.returncode != 0:
                self._update_project_geom_status(
                    project_id, "error", "ingest failed: " + ret.stderr[-2000:])
                return False
            self._update_project_geom_status(project_id, "ready")
            logger.info("[%s] %s geometry ready", project_id, source_type)
            return True
        return self._run_geom_project(project_id, source_path, workspace)

    # ── Result group pipeline ─────────────────────────────────────────────────

    def _verify_consistency(self, workspace: str, result_group: str,
                            check_json_path: str) -> tuple:
        """
        比对 check.json 与 manifest.db instances 表。
        返回 (ok: bool, message: str)。
        """
        try:
            import numpy as np
            import h5py as _h5py
        except ImportError as e:
            return False, "Missing dependency: {}".format(e)

        try:
            with open(check_json_path) as f:
                check = json.load(f)
        except Exception as e:
            return False, "Cannot read check.json: {}".format(e)

        mode     = check.get("mode", "count-only")
        odb_inst = check.get("instances", {})

        manifest = os.path.join(workspace, "manifest.db")
        with sqlite3.connect(manifest, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT instance_name, node_count, elem_count, geom_path FROM instances"
            ).fetchall()
        inp_inst = {r["instance_name"]: dict(r) for r in rows}

        if set(inp_inst) != set(odb_inst):
            return False, "实例列表不匹配: INP={} ODB={}".format(
                sorted(inp_inst), sorted(odb_inst))

        mismatches = []
        for inst, inp in inp_inst.items():
            odb = odb_inst[inst]
            if inp["node_count"] != odb["node_count"]:
                mismatches.append("{}: 节点数 INP={} ODB={}".format(
                    inst, inp["node_count"], odb["node_count"]))
            if inp["elem_count"] != odb["elem_count"]:
                mismatches.append("{}: 单元数 INP={} ODB={}".format(
                    inst, inp["elem_count"], odb["elem_count"]))
        if mismatches:
            return False, "数量校验失败:\n" + "\n".join(mismatches)

        if mode == "count-only":
            return True, "OK (count-only)"

        # label-only: read from geometry H5
        for inst, inp in inp_inst.items():
            geom_h5 = os.path.join(workspace, inp["geom_path"])
            odb_info = odb_inst[inst]
            try:
                with _h5py.File(geom_h5, "r") as f:
                    inp_nodes = f["nodes/labels"][:]
            except Exception as e:
                return False, "{}: cannot read geometry H5: {}".format(inst, e)

            nl_path = odb_info.get("node_labels_path")
            if not nl_path:
                return False, "{}: check.json missing node_labels_path".format(inst)
            odb_nodes = np.load(os.path.join(workspace, nl_path))
            if not np.array_equal(inp_nodes, odb_nodes):
                return False, "{}: node labels 不一致".format(inst)

            inp_etypes = set()
            with _h5py.File(geom_h5, "r") as f:
                if "elements" in f:
                    inp_etypes = set(f["elements"].keys())
            odb_etypes = set(odb_info.get("element_labels", {}).keys())
            if inp_etypes != odb_etypes:
                return False, "{}: 单元类型不匹配 INP={} ODB={}".format(
                    inst, sorted(inp_etypes), sorted(odb_etypes))

            for etype, odb_npy_rel in odb_info.get("element_labels", {}).items():
                with _h5py.File(geom_h5, "r") as f:
                    inp_elems = f["elements/{}/labels".format(etype)][:]
                odb_elems = np.load(os.path.join(workspace, odb_npy_rel))
                if not np.array_equal(inp_elems, odb_elems):
                    return False, "{}/{}: element labels 不一致".format(inst, etype)

        return True, "OK (label-only)"

    def _run_result_group(self, project_id: str, result_group: str,
                          source_path: str, parse_options_json: str,
                          workspace: str) -> bool:
        label = "{}/{}".format(project_id, result_group)
        parse_opts = {}
        if parse_options_json:
            try:
                parse_opts = json.loads(parse_options_json)
            except Exception:
                pass

        check_mode = parse_opts.get("consistency_check", "count-only")
        display_name = parse_opts.get("display_name", result_group)
        source_file = os.path.basename(source_path)

        # SIPESC UNV is not an ODB and therefore has no Abaqus check.json.
        # Its parser performs a non-blocking node-label match against geometry.
        if source_path.lower().endswith(".unv"):
            rc, tail = self._run_streaming(
                [sys.executable, str(_SIPESC_UNV_PACK_SCRIPT), "--unv", source_path,
                 "--workspace", workspace, "--mode", "results",
                 "--result-group", result_group, "--display-name", display_name],
                label, "sipesc_unv_results")
            if rc != 0:
                self._update_result_group_status(
                    project_id, result_group, "error", "sipesc_unv result pack failed: " + tail)
                return False
            self._update_result_group_status(project_id, result_group, "ready")
            logger.info("[%s] SIPESC UNV result group ready", label)
            return True

        # ── Step 1: preflight consistency check ──────────────────────────────
        logger.info("[%s] preflight (%s)", label, check_mode)
        rc, tail = self._run_streaming(
            [self.abaqus_cmd, "python", str(_DUMP_SCRIPT),
             "--odb", source_path,
             "--out", workspace,
             "--result-group", result_group,
             "--mode", "consistency-check",
             "--check-mode", check_mode],
            label, "consistency_check",
        )
        if rc != 0:
            msg = "consistency-check subprocess failed: " + tail
            self._update_result_group_status(project_id, result_group, "error", msg)
            return False

        rg_safe = result_group.replace("/", "__").replace("\\", "__").replace(" ", "_")
        check_json = os.path.join(workspace, "l1_raw", "consistency",
                                  rg_safe, "check.json")
        ok, msg = self._verify_consistency(workspace, result_group, check_json)
        if not ok:
            self._update_result_group_status(project_id, result_group, "error", msg)
            logger.warning("[%s] consistency check failed: %s", label, msg)
            return False
        logger.info("[%s] consistency check passed: %s", label, msg)

        # ── Step 2: extract results ───────────────────────────────────────────
        logger.info("[%s] extract", label)
        extract_cmd = [self.abaqus_cmd, "python", str(_DUMP_SCRIPT),
                       "--odb", source_path,
                       "--out", workspace,
                       "--result-group", result_group,
                       "--mode", "extract"]
        if parse_opts.get("invariants") == "full":
            extract_cmd.extend(["--invariants", "full"])
        _append_extract_filters(extract_cmd, parse_opts)
        rc, tail = self._run_streaming(extract_cmd, label, "extract")
        if rc != 0:
            msg = "extract failed: " + tail
            self._update_result_group_status(project_id, result_group, "error", msg)
            return False

        # ── Step 3: pack results into HDF5 ────────────────────────────────────
        logger.info("[%s] l1_pack (result_group)", label)
        rc, tail = self._run_streaming(
            [sys.executable, str(_PACK_SCRIPT),
             "--workspace", workspace,
             "--result-group", result_group,
             "--display-name", display_name,
             "--consistency-check", check_mode,
             "--source-file", source_file],
            label, "l1_pack_rg",
        )
        if rc != 0:
            msg = "l1_pack (result_group) failed: " + tail
            self._update_result_group_status(project_id, result_group, "error", msg)
            return False

        self._update_result_group_status(project_id, result_group, "ready")
        logger.info("[%s] ready", label)
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

            did_work = False

            # ── 1. Project geometry tasks (INP) ──────────────────────────────
            project_id, source_path, source_file, source_type, ws = self._claim_pending_project()
            if project_id is not None:
                did_work = True
                logger.info("Runner claimed project geom %s", project_id)
                try:
                    source_path = _materialize_runner_source(source_path, ws, source_file)
                    self._run_project(project_id, source_path, source_type, ws)
                except Exception:
                    logger.exception("Runner: error in project geom %s", project_id)
                    try:
                        self._update_project_geom_status(
                            project_id, "error",
                            "Unhandled runner exception — check server logs")
                    except Exception:
                        pass

            # ── 2. Result group tasks (ODB) ───────────────────────────────────
            project_id, rg, src, source_file, parse_opts, ws = self._claim_pending_result_group()
            if project_id is not None:
                did_work = True
                label = "{}/{}".format(project_id, rg)
                logger.info("Runner claimed result_group %s", label)
                try:
                    # error 重试：先清理旧产物
                    self._cleanup_result_group(ws, rg)
                    src = _materialize_runner_source(src, ws, source_file)
                    self._run_result_group(project_id, rg, src, parse_opts, ws)
                except Exception:
                    logger.exception("Runner: error in result_group %s", label)
                    try:
                        self._update_result_group_status(
                            project_id, rg, "error",
                            "Unhandled runner exception — check server logs")
                    except Exception:
                        pass

            # ── 3. Legacy ODB jobs ────────────────────────────────────────────
            odb_id, odb_path, workspace = self._claim_submitted()
            if odb_id is not None:
                did_work = True
                logger.info("Runner claimed job %s", odb_id)
                try:
                    odb_path = materialize_source_file(odb_path, workspace)
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

            if not did_work:
                time.sleep(self.poll_interval)


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
