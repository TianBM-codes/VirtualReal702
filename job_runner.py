#!/usr/bin/env python3
"""
job_runner.py — Standalone process for L1+L2 pipeline (optional).

By default the L3 web service runs the job runner as an embedded daemon
thread (APP_EMBEDDED_RUNNER=1), so you only need to start uvicorn.

Use this script only if you want to run the runner as a separate process
(e.g., on a different machine, or when APP_EMBEDDED_RUNNER=0):

    APP_EMBEDDED_RUNNER=0 uvicorn src.l3.main:app ...
    python src/job_runner.py

Shares only registry.db with the L3 web service.
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

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_ROOT     = Path(__file__).resolve().parent.parent

# Ensure repo root is on sys.path so "from src.xxx import ..." works
# regardless of how job_runner.py is invoked (python src/job_runner.py or
# python -m src.job_runner).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DUMP_SCRIPT   = REPO_ROOT / "src" / "l1" / "abaqus_dump.py"
PACK_SCRIPT   = REPO_ROOT / "src" / "l1" / "l1_pack.py"
INGEST_SCRIPT = REPO_ROOT / "src" / "l2" / "ingest.py"

# ── Config ─────────────────────────────────────────────────────────────────────

_DEFAULT_MODEL   = str(REPO_ROOT / "model")
REGISTRY_DB      = os.getenv("APP_REGISTRY_DB_PATH", str(REPO_ROOT / "model" / "registry.db"))
DATA_ROOT        = os.getenv("APP_DATA_ROOT", _DEFAULT_MODEL)
POLL_INTERVAL    = int(os.getenv("JOB_RUNNER_POLL_INTERVAL", "10"))    # seconds
HEARTBEAT_INTERVAL = 60   # seconds between heartbeat updates
ABAQUS_CMD       = os.getenv("APP_ABAQUS_CMD", "abaqus")  # override if not in PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s job_runner: %(message)s",
)
logger = logging.getLogger("job_runner")

# ── Shared mutable state for heartbeat thread ──────────────────────────────────

_current_odb_id: list = [None]   # [0] = odb_id currently being processed, or None


# ── Timestamp helper ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Registry helpers ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(REGISTRY_DB, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _update_status(odb_id: str, status: str, **fields) -> None:
    allowed = {
        "l1_started_at", "l1_done_at",
        "l2_started_at", "l2_done_at",
        "error_msg", "node_count", "instance_count",
    }
    kv = {k: v for k, v in fields.items() if k in allowed}
    kv["status"] = status
    set_clause = ", ".join(f"{k}=?" for k in kv)
    with _connect() as conn:
        conn.execute(
            f"UPDATE odb_jobs SET {set_clause} WHERE odb_id=?",
            (*kv.values(), odb_id),
        )


def _claim_submitted() -> tuple:
    """
    Atomically claim one submitted job → l1_running.
    Returns (odb_id, odb_path, workspace) or (None, None, None).
    """
    with _connect() as conn:
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
        # Resolve to absolute path using DATA_ROOT — the DB stores a bare
        # odb_id (portable) which must be joined with DATA_ROOT before use.
        # Old-style absolute paths (pre-fix) are returned as-is.
        is_abs = os.path.isabs(stored_ws) or bool(
            re.match(r'^[A-Za-z]:[/\\]', stored_ws)
        )
        workspace_abs = stored_ws if is_abs else os.path.join(DATA_ROOT, stored_ws)
        return row["odb_id"], row["odb_path"], workspace_abs


def _read_l1_stats(workspace: str) -> tuple:
    """
    Read node_count and instance_count from manifest.db.
    Tries `meta` table first (ODB pipeline), falls back to `instances` table (INP pipeline).
    """
    manifest = os.path.join(workspace, "manifest.db")
    try:
        with sqlite3.connect(manifest, timeout=5.0) as conn:
            # ODB pipeline: l1_pack.py writes a `meta` key/value table
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "meta" in tables:
                rows = {r[0]: r[1] for r in conn.execute(
                    "SELECT key, value FROM meta"
                ).fetchall()}
                return int(rows.get("node_count", 0)), int(rows.get("instance_count", 0))
            # INP pipeline: exporter writes `instances` table with per-instance counts
            if "instances" in tables:
                row = conn.execute(
                    "SELECT SUM(node_count), COUNT(*) FROM instances"
                ).fetchone()
                return int(row[0] or 0), int(row[1] or 0)
    except Exception:
        logger.warning("Could not read manifest.db stats in %s", workspace)
    return 0, 0


# ── Heartbeat thread ──────────────────────────────────────────────────────────

def _heartbeat_loop() -> None:
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        oid = _current_odb_id[0]
        if oid:
            try:
                with _connect() as conn:
                    conn.execute(
                        "UPDATE odb_jobs SET last_heartbeat=? WHERE odb_id=?",
                        (_now_iso(), oid),
                    )
            except Exception:
                logger.warning("Heartbeat update failed for %s", oid)


# ── Subprocess helper ─────────────────────────────────────────────────────────

def _run_streaming(cmd: list, odb_id: str, label: str) -> tuple:
    """Run a subprocess, printing each output line as it arrives. Returns (returncode, stderr_tail)."""
    stderr_lines = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            logger.info("[%s] %s: %s", odb_id, label, line)
            stderr_lines.append(line)
    proc.wait()
    return proc.returncode, "\n".join(stderr_lines[-50:])


# ── L1 pipeline (two phases) ──────────────────────────────────────────────────

def _run_l1_odb(odb_id: str, odb_path: str, workspace: str) -> bool:
    """
    ODB pipeline:
      Phase 1 — abaqus_dump.py  (Abaqus Python 2.7, requires Abaqus license)
      Phase 2 — l1_pack.py      (standard Python 3)
    """
    logger.info("[%s] L1 phase 1: abaqus_dump.py", odb_id)
    rc1, tail1 = _run_streaming(
        [ABAQUS_CMD, "python", str(DUMP_SCRIPT), "--odb", odb_path, "--out", workspace],
        odb_id, "abaqus_dump",
    )
    if rc1 != 0:
        log_hint = f"\n[Check {workspace}/abaqus.log for full Abaqus output]"
        _update_status(odb_id, "error", error_msg="abaqus_dump failed: " + tail1 + log_hint)
        logger.error("[%s] L1 phase 1 failed (rc=%d)", odb_id, rc1)
        return False

    logger.info("[%s] L1 phase 2: l1_pack.py", odb_id)
    rc2, tail2 = _run_streaming(
        [sys.executable, str(PACK_SCRIPT), "--workspace", workspace],
        odb_id, "l1_pack",
    )
    if rc2 != 0:
        _update_status(odb_id, "error", error_msg="l1_pack failed: " + tail2)
        logger.error("[%s] L1 phase 2 failed (rc=%d)", odb_id, rc2)
        return False

    return True


def _run_l1_inp(odb_id: str, inp_path: str, workspace: str) -> bool:
    """
    INP pipeline: parse + export to L1-compatible HDF5 in-process.
    No Abaqus license required.
    """
    logger.info("[%s] L1 (INP): parsing %s", odb_id, inp_path)
    try:
        from src.inp import parse_inp
        from src.inp.exporter import export_l1
        model = parse_inp(inp_path)
        export_l1(model, workspace)
    except Exception as exc:
        _update_status(odb_id, "error", error_msg=f"INP parse/export failed: {exc}")
        logger.exception("[%s] INP L1 failed", odb_id)
        return False
    logger.info("[%s] L1 (INP) done", odb_id)
    return True


def _run_l1(odb_id: str, source_path: str, workspace: str) -> bool:
    """
    Dispatch to the correct L1 pipeline based on file extension.
      .inp → INP parser + exporter (no Abaqus needed)
      *    → ODB pipeline (abaqus_dump + l1_pack)
    Returns True on success; sets status='error' and returns False on failure.
    """
    if source_path.lower().endswith(".inp"):
        ok = _run_l1_inp(odb_id, source_path, workspace)
    else:
        ok = _run_l1_odb(odb_id, source_path, workspace)

    if not ok:
        return False

    # Read stats from manifest.db and store in registry
    node_count, instance_count = _read_l1_stats(workspace)
    _update_status(odb_id, "l1_done",
        l1_done_at=_now_iso(),
        node_count=node_count,
        instance_count=instance_count,
    )
    logger.info("[%s] L1 done (nodes=%d, instances=%d)", odb_id, node_count, instance_count)
    return True


# ── L2 pipeline ───────────────────────────────────────────────────────────────

def _run_l2(odb_id: str, workspace: str) -> bool:
    """
    Run ingest.py (pure Python 3, no Abaqus needed).
    On failure, status is rolled back to l1_done so L1 data is preserved.
    Returns True on success.
    """
    if not INGEST_SCRIPT.exists():
        logger.info("[%s] L2 script not found, skipping L2 — marking ready", odb_id)
        _update_status(odb_id, "ready", l2_done_at=_now_iso())
        return True

    _update_status(odb_id, "l2_running", l2_started_at=_now_iso())
    logger.info("[%s] L2: ingest.py", odb_id)

    ret = subprocess.run(
        [sys.executable, str(INGEST_SCRIPT), "--workspace", workspace],
        capture_output=True, text=True,
    )
    if ret.returncode != 0:
        # Roll back to l1_done so L1 data remains accessible and job can be retried
        _update_status(odb_id, "l1_done",
            error_msg="ingest failed: " + ret.stderr[-2000:])
        logger.error("[%s] L2 failed (rc=%d)", odb_id, ret.returncode)
        return False

    _update_status(odb_id, "ready", l2_done_at=_now_iso())
    logger.info("[%s] ready", odb_id)
    return True


# ── Main job runner ───────────────────────────────────────────────────────────

def _run_job(odb_id: str, odb_path: str, workspace: str) -> None:
    _current_odb_id[0] = odb_id
    try:
        ok = _run_l1(odb_id, odb_path, workspace)
        if ok:
            _run_l2(odb_id, workspace)
    finally:
        _current_odb_id[0] = None


def main() -> None:
    logger.info("job_runner starting (registry=%s)", REGISTRY_DB)

    # Start heartbeat daemon
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    t.start()

    while True:
        odb_id, odb_path, workspace = _claim_submitted()
        if odb_id is None:
            time.sleep(POLL_INTERVAL)
            continue
        logger.info("Claimed job %s", odb_id)
        _run_job(odb_id, odb_path, workspace)


if __name__ == "__main__":
    main()
