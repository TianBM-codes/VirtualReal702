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

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_ROOT     = Path(__file__).resolve().parent.parent

# Ensure repo root is on sys.path so "from src.xxx import ..." works
# regardless of how job_runner.py is invoked (python src/job_runner.py or
# python -m src.job_runner).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.file_fetch import download_if_url, is_http_url
DUMP_SCRIPT     = REPO_ROOT / "src" / "l1" / "abaqus_dump.py"
PACK_SCRIPT     = REPO_ROOT / "src" / "l1" / "l1_pack.py"
INP_PACK_SCRIPT = REPO_ROOT / "src" / "l1" / "inp_pack.py"
INGEST_SCRIPT   = REPO_ROOT / "src" / "l2" / "ingest.py"

# ── Config ─────────────────────────────────────────────────────────────────────

def _load_service_config() -> dict:
    """Read service_config.json from repo root (same logic as src/l3/core/config.py)."""
    import json
    candidates = [
        os.getenv("CONFIG_FILE", ""),
        os.path.join(os.getcwd(), "service_config.json"),
        str(REPO_ROOT / "service_config.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def _cfg(cfg: dict, key: str, default: str) -> str:
    """env var wins → config file → default."""
    if key in os.environ:
        return os.environ[key]
    return str(cfg.get(key, default))


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


_svc_cfg = _load_service_config()

_DEFAULT_MODEL   = str(REPO_ROOT / "model")
REGISTRY_DB      = _cfg(_svc_cfg, "APP_REGISTRY_DB_PATH", str(REPO_ROOT / "model" / "registry.db"))
DATA_ROOT        = _cfg(_svc_cfg, "APP_DATA_ROOT", _DEFAULT_MODEL)
POLL_INTERVAL    = int(_cfg(_svc_cfg, "JOB_RUNNER_POLL_INTERVAL", "10"))    # seconds
HEARTBEAT_INTERVAL = 60   # seconds between heartbeat updates
ABAQUS_CMD       = _cfg(_svc_cfg, "APP_ABAQUS_CMD", "abaqus")  # override if not in PATH
# "full" → pass --invariants full to abaqus_dump.py; "none" → skip (default)
INVARIANTS_MODE  = _cfg(_svc_cfg, "APP_INVARIANTS", "none")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s job_runner: %(message)s",
)
logger = logging.getLogger("job_runner")

# ── Shared mutable state for heartbeat thread ──────────────────────────────────

_current_odb_id: list = [None]   # [0] = odb_id currently being processed, or None

# ── job_logs helpers ───────────────────────────────────────────────────────────

_JOB_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS job_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    odb_id  TEXT    NOT NULL,
    ts      TEXT    NOT NULL,
    level   TEXT    NOT NULL DEFAULT 'info',
    stage   TEXT,
    message TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_odb ON job_logs(odb_id, id);
"""


def _ensure_job_logs_schema() -> None:
    """Create job_logs table if not present (needed for standalone runner without L3)."""
    try:
        with _connect() as conn:
            conn.executescript(_JOB_LOGS_DDL)
    except Exception:
        pass


def _log_job(odb_id: str, level: str, message: str, stage: str = None) -> None:
    """Persist one log line to job_logs. Never raises — logging must not break the pipeline."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO job_logs (odb_id, ts, level, stage, message) VALUES (?,?,?,?,?)",
                (odb_id, _now_iso(), level, stage, message),
            )
    except Exception:
        pass


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

def _run_streaming(cmd: list, odb_id: str, label: str, cwd: str = None) -> tuple:
    """Run a subprocess, printing each output line as it arrives. Returns (returncode, stderr_tail)."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', bufsize=1,
                                cwd=cwd)
    except FileNotFoundError:
        msg = (
            f"Command not found: {cmd[0]!r}\n"
            f"Set APP_ABAQUS_CMD in service_config.json to the full path of the Abaqus executable.\n"
            f"Example: {{\"APP_ABAQUS_CMD\": \"C:\\\\SIMULIA\\\\Commands\\\\abaqus.bat\"}}"
        )
        logger.error("[%s] %s: %s", odb_id, label, msg)
        _log_job(odb_id, "error", msg, stage=label)
        return 1, msg
    except OSError as exc:
        msg = f"Failed to launch {cmd[0]!r}: {exc}"
        logger.error("[%s] %s: %s", odb_id, label, msg)
        _log_job(odb_id, "error", msg, stage=label)
        return 1, msg

    stderr_lines = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            logger.info("[%s] %s: %s", odb_id, label, line)
            stderr_lines.append(line)
            _log_job(odb_id, "info", line, stage=label)
    proc.wait()
    return proc.returncode, "\n".join(stderr_lines[-50:])


# ── ODB version upgrade helpers ───────────────────────────────────────────────

def _is_odb_version_error(rc: int, tail: str) -> bool:
    """
    Detect ODB version mismatch via the explicit marker printed by _open_odb()
    in abaqus_dump.py.  Reliable and specific — no keyword heuristics that
    could match ODB filenames containing 'upgrade' or 'odb'.
    """
    return "ODB_VERSION_ERROR" in tail


def _upgrade_odb(odb_path: str, label: str) -> tuple:
    """
    Run 'abaqus -upgrade -job <tmp_base> -odb <source.odb>' in the ODB's directory.
    Returns (success: bool, upgraded_path: str, tail: str).

    upgraded_path is the path callers should use for the retry:
    - If the upgraded file could be renamed over the original → original path
    - If rename fails (Windows file lock) → the temp file path
    """
    odb_abs  = os.path.abspath(odb_path)
    odb_dir  = os.path.dirname(odb_abs)
    base     = os.path.splitext(os.path.basename(odb_abs))[0]
    tmp_base = base + "_upgraded_tmp"
    tmp_odb  = os.path.join(odb_dir, tmp_base + ".odb")

    # Remove stale temp file from a previous failed attempt
    if os.path.exists(tmp_odb):
        try:
            os.remove(tmp_odb)
        except Exception:
            pass

    # Run from odb_dir so Abaqus writes tmp_base.odb there
    cmd = [ABAQUS_CMD, "-upgrade", "-job", tmp_base, "-odb", odb_abs]
    logger.info("[%s] ODB version mismatch — upgrading (cwd=%s): %s",
                label, odb_dir, " ".join(cmd))
    rc, tail = _run_streaming(cmd, label, "odb_upgrade", cwd=odb_dir)
    if rc != 0:
        logger.error("[%s] ODB upgrade failed (rc=%d)", label, rc)
        return False, odb_abs, tail

    # Abaqus -upgrade may exit 0 even on failure — check output for error markers
    if "ODB FILE UPGRADE FAILED" in tail.upper():
        logger.error("[%s] ODB upgrade failed (rc=0 but error in output)", label)
        return False, odb_abs, tail

    if not os.path.exists(tmp_odb):
        msg = "Upgraded ODB not found at expected path: {}".format(tmp_odb)
        logger.error("[%s] %s", label, msg)
        return False, odb_abs, msg

    # Try to replace the original; if Windows locks it, use the temp file directly
    try:
        os.replace(tmp_odb, odb_abs)
        logger.info("[%s] ODB upgraded successfully — replaced %s", label, odb_abs)
        return True, odb_abs, tail
    except Exception as exc:
        logger.warning("[%s] Could not replace original ODB (%s); "
                       "using upgraded temp file %s for retry", label, exc, tmp_odb)
        return True, tmp_odb, tail


# ── L1 pipeline (two phases) ──────────────────────────────────────────────────

def _run_l1_odb(odb_id: str, odb_path: str, workspace: str) -> bool:
    """
    ODB pipeline:
      Phase 1 — abaqus_dump.py  (Abaqus Python 2.7, requires Abaqus license)
      Phase 2 — l1_pack.py      (standard Python 3)
    If Abaqus reports a version mismatch on first attempt, the ODB is upgraded
    in-place and abaqus_dump.py is retried once.
    """
    logger.info("[%s] L1 phase 1: abaqus_dump.py", odb_id)
    _log_job(odb_id, "step", "L1 阶段 1：Abaqus 导出（abaqus_dump.py）启动", stage="l1_dump")
    dump_cmd = [ABAQUS_CMD, "python", str(DUMP_SCRIPT), "--odb", odb_path, "--out", workspace]
    if INVARIANTS_MODE == "full":
        dump_cmd += ["--invariants", "full"]
    rc1, tail1 = _run_streaming(dump_cmd, odb_id, "l1_dump")
    logger.info("[%s] abaqus_dump rc=%d, tail_len=%d", odb_id, rc1, len(tail1))

    if _is_odb_version_error(rc1, tail1):
        _log_job(odb_id, "warn", "ODB 版本不匹配，正在升级 ODB 文件…", stage="l1_dump")
        ok, upgraded_path, upgrade_tail = _upgrade_odb(odb_path, odb_id)
        if not ok:
            _update_status(odb_id, "error",
                           error_msg="ODB upgrade failed: " + upgrade_tail)
            _log_job(odb_id, "error", "ODB 升级失败：" + upgrade_tail[-500:], stage="l1_dump")
            return False
        logger.info("[%s] Retrying abaqus_dump.py after upgrade (odb=%s)",
                    odb_id, upgraded_path)
        _log_job(odb_id, "step", "ODB 升级完成，重试 abaqus_dump.py", stage="l1_dump")
        retry_cmd = [ABAQUS_CMD, "python", str(DUMP_SCRIPT),
                     "--odb", upgraded_path, "--out", workspace]
        if INVARIANTS_MODE == "full":
            retry_cmd += ["--invariants", "full"]
        rc1, tail1 = _run_streaming(retry_cmd, odb_id, "l1_dump")
        logger.info("[%s] abaqus_dump retry rc=%d", odb_id, rc1)

    if rc1 != 0:
        log_hint = "\n[Check {}/abaqus.log for full Abaqus output]".format(workspace)
        err = "abaqus_dump failed: " + tail1 + log_hint
        _update_status(odb_id, "error", error_msg=err)
        _log_job(odb_id, "error", f"L1 阶段 1 失败 (rc={rc1})：" + tail1[-500:], stage="l1_dump")
        logger.error("[%s] L1 phase 1 failed (rc=%d)", odb_id, rc1)
        return False

    _log_job(odb_id, "step", "L1 阶段 1 完成，开始打包 HDF5（l1_pack.py）", stage="l1_pack")
    logger.info("[%s] L1 phase 2: l1_pack.py", odb_id)
    rc2, tail2 = _run_streaming(
        [sys.executable, str(PACK_SCRIPT), "--workspace", workspace],
        odb_id, "l1_pack",
    )
    if rc2 != 0:
        _update_status(odb_id, "error", error_msg="l1_pack failed: " + tail2)
        _log_job(odb_id, "error", f"L1 阶段 2 失败 (rc={rc2})：" + tail2[-500:], stage="l1_pack")
        logger.error("[%s] L1 phase 2 failed (rc=%d)", odb_id, rc2)
        return False

    return True


def _run_l1_inp(odb_id: str, inp_path: str, workspace: str) -> bool:
    """
    INP pipeline: parse + export to L1-compatible HDF5 in-process.
    No Abaqus license required.
    """
    logger.info("[%s] L1 (INP): parsing %s", odb_id, inp_path)
    _log_job(odb_id, "step", f"L1（INP）：解析 {os.path.basename(inp_path)}", stage="l1_inp")
    try:
        from src.inp import parse_inp
        from src.inp.exporter import export_l1
        model = parse_inp(inp_path)
        export_l1(model, workspace)
    except Exception as exc:
        _update_status(odb_id, "error", error_msg=f"INP parse/export failed: {exc}")
        _log_job(odb_id, "error", f"INP 解析失败：{exc}", stage="l1_inp")
        logger.exception("[%s] INP L1 failed", odb_id)
        return False
    _log_job(odb_id, "step", "L1（INP）解析完成", stage="l1_inp")
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
    _log_job(odb_id, "step",
             f"L1 完成（节点数 {node_count:,}，实例数 {instance_count}），开始 L2 预处理",
             stage="l1_done")
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
        _log_job(odb_id, "warn", "L2 脚本不存在，跳过 L2，直接标记就绪", stage="l2_ingest")
        _update_status(odb_id, "ready", l2_done_at=_now_iso())
        return True

    _update_status(odb_id, "l2_running", l2_started_at=_now_iso())
    logger.info("[%s] L2: ingest.py", odb_id)
    _log_job(odb_id, "step", "L2 预处理（ingest.py）启动：三角面提取、特征边、Octree…", stage="l2_ingest")

    rc, tail = _run_streaming(
        [sys.executable, str(INGEST_SCRIPT), "--workspace", workspace],
        odb_id, "l2_ingest",
    )
    if rc != 0:
        _update_status(odb_id, "error", error_msg="[L2] ingest failed: " + tail[-2000:])
        _log_job(odb_id, "error", f"L2 预处理失败 (rc={rc})：" + tail[-500:], stage="l2_ingest")
        logger.error("[%s] L2 failed (rc=%d)", odb_id, rc)
        return False

    _update_status(odb_id, "ready", l2_done_at=_now_iso())
    _log_job(odb_id, "step", "解析全部完成，已就绪", stage="l2_done")
    logger.info("[%s] ready", odb_id)
    return True


# ── Project / result_group helpers ───────────────────────────────────────────

def _claim_pending_project() -> tuple:
    """原子认领 geom_status='pending' project → 'running'。返回 (project_id, inp_path, workspace) 或全 None。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET geom_status='running', updated_at=?"
            " WHERE project_id=("
            "  SELECT project_id FROM projects WHERE geom_status='pending'"
            "  ORDER BY created_at LIMIT 1"
            ")",
            (_now_iso(),),
        )
        if cur.rowcount == 0:
            return None, None, None, None
        row = conn.execute(
            "SELECT project_id, workspace, inp_path, source_type FROM projects"
            " WHERE geom_status='running' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None, None, None, None
        ws = row["workspace"]
        if not (os.path.isabs(ws) or re.match(r'^[A-Za-z]:[/\\]', ws)):
            ws = os.path.join(DATA_ROOT, row["project_id"])
        source_type = row["source_type"] if "source_type" in row.keys() else "inp"
        return row["project_id"], row["inp_path"], source_type, ws


def _update_project_geom_status(project_id: str, status: str,
                                 error_message: str = None) -> None:
    with _connect() as conn:
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


def _run_geom_project(project_id: str, inp_path: str, workspace: str) -> bool:
    if not inp_path:
        msg = "No INP path stored for project {}".format(project_id)
        logger.error(msg)
        _update_project_geom_status(project_id, "error", msg)
        return False
    if not os.path.exists(inp_path):
        msg = "INP file not found: {}".format(inp_path)
        logger.error("[%s] %s", project_id, msg)
        _update_project_geom_status(project_id, "error", msg)
        return False

    # --- L1: parse INP once, export geometry HDF5 + run catalog import ---
    logger.info("[%s] Geom: parsing INP %s", project_id, inp_path)
    _log_job(project_id, "step",
             "几何解析开始：解析 INP 文件 {}".format(os.path.basename(inp_path)),
             stage="l1_inp")
    try:
        import json as _json
        from src.inp import parse_inp
        from src.inp.exporter import export_l1
        from src.inp.summary import compute_inp_summary
        model = parse_inp(inp_path)
        export_l1(model, workspace)
        summary = compute_inp_summary(model)
        summary_path = os.path.join(workspace, "model_summary.json")
        with open(summary_path, "w", encoding="utf-8") as _f:
            _json.dump(summary, _f)
    except Exception as exc:
        msg = "INP parse/export failed: {}".format(exc)
        logger.exception("[%s] %s", project_id, msg)
        _update_project_geom_status(project_id, "error", msg)
        _log_job(project_id, "error", "INP 解析失败：{}".format(exc), stage="l1_inp")
        return False

    _log_job(project_id, "step", "INP 解析完成，导出几何 HDF5", stage="l1_inp")

    # --- Catalog import (optional: only available in combined deployment) ---
    try:
        from services.model_update.analysis.inp_service import import_inp_catalog
        logger.info("[%s] Geom: importing INP catalog", project_id)
        _log_job(project_id, "step", "导入 INP catalog（测点/参数信息）", stage="catalog")
        import_inp_catalog(inp_path, project_id, model=model)
        logger.info("[%s] Geom: catalog import done", project_id)
        _log_job(project_id, "step", "INP catalog 导入完成", stage="catalog")
    except ImportError:
        logger.info("[%s] Geom: inp_service not available — skipping catalog import", project_id)
    except Exception as exc:
        logger.warning("[%s] Catalog import failed (non-fatal): %s", project_id, exc)
        _log_job(project_id, "warn", "catalog 导入失败（非致命）：{}".format(exc), stage="catalog")

    # --- L2: ingest (subprocess, keeps numpy/HDF5 isolated) ---
    logger.info("[%s] Geom: ingest.py (L2)", project_id)
    _log_job(project_id, "step", "L2 预处理（ingest.py）启动：三角面提取、特征边、Octree…",
             stage="l2_ingest")
    rc_l2, tail_l2 = _run_streaming(
        [sys.executable, str(INGEST_SCRIPT), "--workspace", workspace],
        project_id, "l2_ingest",
    )
    if rc_l2 != 0:
        msg = "ingest failed: " + tail_l2[-2000:]
        _update_project_geom_status(project_id, "error", msg)
        _log_job(project_id, "error",
                 "L2 预处理失败 (rc={})：{}".format(rc_l2, tail_l2[-500:]),
                 stage="l2_ingest")
        logger.error("[%s] L2 failed (rc=%d)", project_id, rc_l2)
        return False

    _update_project_geom_status(project_id, "ready")
    _log_job(project_id, "step", "几何解析完成，已就绪", stage="l2_done")
    logger.info("[%s] Geom ready", project_id)
    return True


def _run_odb_project(project_id: str, odb_path: str, workspace: str) -> bool:
    if not odb_path:
        msg = "No ODB path stored for project {}".format(project_id)
        logger.error(msg)
        _update_project_geom_status(project_id, "error", msg)
        return False
    if not os.path.exists(odb_path):
        msg = "ODB file not found: {}".format(odb_path)
        logger.error("[%s] %s", project_id, msg)
        _update_project_geom_status(project_id, "error", msg)
        return False

    logger.info("[%s] Project ODB: abaqus_dump.py", project_id)
    _log_job(project_id, "step", "L1 阶段 1：Abaqus 导出（abaqus_dump.py）启动", stage="l1_dump")
    dump_cmd = [ABAQUS_CMD, "python", str(DUMP_SCRIPT), "--odb", odb_path, "--out", workspace]
    if INVARIANTS_MODE == "full":
        dump_cmd += ["--invariants", "full"]
    rc1, tail1 = _run_streaming(dump_cmd, project_id, "l1_dump")
    logger.info("[%s] project_abaqus_dump rc=%d, tail_len=%d", project_id, rc1, len(tail1))

    if _is_odb_version_error(rc1, tail1):
        _log_job(project_id, "warn", "ODB 版本不匹配，正在升级 ODB 文件…", stage="l1_dump")
        ok, upgraded_path, upgrade_tail = _upgrade_odb(odb_path, project_id)
        if not ok:
            msg = "ODB upgrade failed: " + upgrade_tail
            _update_project_geom_status(project_id, "error", msg)
            _log_job(project_id, "error", "ODB 升级失败：" + upgrade_tail[-500:], stage="l1_dump")
            return False
        logger.info("[%s] Retrying abaqus_dump.py after upgrade (odb=%s)",
                    project_id, upgraded_path)
        _log_job(project_id, "step", "ODB 升级完成，重试 abaqus_dump.py", stage="l1_dump")
        retry_cmd = [ABAQUS_CMD, "python", str(DUMP_SCRIPT),
                     "--odb", upgraded_path, "--out", workspace]
        if INVARIANTS_MODE == "full":
            retry_cmd += ["--invariants", "full"]
        rc1, tail1 = _run_streaming(retry_cmd, project_id, "l1_dump")
        logger.info("[%s] project_abaqus_dump retry rc=%d", project_id, rc1)

    if rc1 != 0:
        msg = "abaqus_dump failed: " + tail1
        _update_project_geom_status(project_id, "error", msg)
        _log_job(project_id, "error",
                 "L1 阶段 1 失败 (rc={})：{}".format(rc1, tail1[-500:]), stage="l1_dump")
        logger.error("[%s] Project ODB phase 1 failed (rc=%d)", project_id, rc1)
        return False

    _log_job(project_id, "step", "L1 阶段 1 完成，开始打包 HDF5（l1_pack.py）", stage="l1_pack")
    logger.info("[%s] Project ODB: l1_pack.py", project_id)
    rc2, tail2 = _run_streaming(
        [sys.executable, str(PACK_SCRIPT), "--workspace", workspace],
        project_id, "l1_pack",
    )
    if rc2 != 0:
        msg = "l1_pack failed: " + tail2
        _update_project_geom_status(project_id, "error", msg)
        _log_job(project_id, "error",
                 "L1 阶段 2 失败 (rc={})：{}".format(rc2, tail2[-500:]), stage="l1_pack")
        logger.error("[%s] Project ODB phase 2 failed (rc=%d)", project_id, rc2)
        return False

    _log_job(project_id, "step", "L1 完成，开始 L2 预处理", stage="l1_done")

    # --- Catalog import (optional: only available in combined deployment) ---
    try:
        from services.model_update.analysis.inp_service import import_inp_catalog
        from src.l1.odb_model import load_as_inp_model
        logger.info("[%s] Project ODB: importing catalog (via inp_service)", project_id)
        _log_job(project_id, "step", "导入 ODB catalog（测点/参数信息）", stage="catalog")
        inp_model = load_as_inp_model(workspace)
        import_inp_catalog(odb_path, project_id, model=inp_model)
        logger.info("[%s] Project ODB: catalog import done", project_id)
        _log_job(project_id, "step", "ODB catalog 导入完成", stage="catalog")
    except ImportError:
        logger.info("[%s] Project ODB: inp_service not available — skipping catalog import", project_id)
    except Exception as exc:
        logger.warning("[%s] ODB catalog import failed (non-fatal): %s", project_id, exc)
        _log_job(project_id, "warn", "catalog 导入失败（非致命）：{}".format(exc), stage="catalog")

    logger.info("[%s] Project ODB: ingest.py (L2)", project_id)
    _log_job(project_id, "step", "L2 预处理（ingest.py）启动：三角面提取、特征边、Octree…",
             stage="l2_ingest")
    rc_l2, tail_l2 = _run_streaming(
        [sys.executable, str(INGEST_SCRIPT), "--workspace", workspace],
        project_id, "l2_ingest",
    )
    if rc_l2 != 0:
        msg = "ingest failed: " + tail_l2[-2000:]
        _update_project_geom_status(project_id, "error", msg)
        _log_job(project_id, "error",
                 "L2 预处理失败 (rc={})：{}".format(rc_l2, tail_l2[-500:]),
                 stage="l2_ingest")
        logger.error("[%s] Project ODB L2 failed (rc=%d)", project_id, rc_l2)
        return False

    _update_project_geom_status(project_id, "ready")
    _log_job(project_id, "step", "ODB 解析全部完成，已就绪", stage="l2_done")
    logger.info("[%s] Project ODB ready", project_id)
    _adopt_odb_result_group(project_id, odb_path, workspace)
    return True


def _adopt_odb_result_group(project_id: str, odb_path: str, workspace: str) -> None:
    """
    ODB 全量解析完成后立即注册 result_group='default_result'（status='ready'）。
    等效于 runner_thread._adopt_odb_default_results，避免依赖 L3 服务类。
    """
    rg_name = "default_result"
    source_file = os.path.basename(odb_path) if odb_path else None
    display_name = os.path.splitext(source_file)[0] if source_file else rg_name

    # Step 1: 在 manifest.db 里把 result_group=NULL 的行打上 rg_name
    manifest_path = os.path.join(workspace, "manifest.db")
    if os.path.exists(manifest_path):
        try:
            with sqlite3.connect(manifest_path, timeout=5.0) as conn:
                for tbl in ("steps", "frames", "result_files", "result_blocks"):
                    try:
                        conn.execute(
                            "UPDATE {} SET result_group=? WHERE result_group IS NULL".format(tbl),
                            (rg_name,),
                        )
                    except Exception:
                        pass
                # result_group_meta
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO result_group_meta"
                        " (result_group, display_name, source_file, consistency_check, created_at)"
                        " VALUES (?,?,?,'count-only',datetime('now'))",
                        (rg_name, display_name, source_file or ""),
                    )
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("[%s] _adopt_odb_result_group: manifest update failed: %s", project_id, exc)

    # Step 2: 在 registry.db 里插入 result_groups 行（status='ready'）
    try:
        now = _now_iso()
        with _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO result_groups"
                " (project_id, result_group, display_name, source_path, source_file,"
                "  status, parse_options, created_at, updated_at)"
                " VALUES (?,?,?,?,?,'ready',NULL,?,?)",
                (project_id, rg_name, display_name,
                 odb_path or "", source_file or "", now, now),
            )
        logger.info("[%s] Registered result_group='%s' (status=ready)", project_id, rg_name)
    except Exception as exc:
        logger.warning("[%s] _adopt_odb_result_group: registry update failed: %s", project_id, exc)


def _run_project(project_id: str, source_path: str, source_type: str, workspace: str) -> bool:
    if source_type == "odb":
        return _run_odb_project(project_id, source_path, workspace)
    return _run_geom_project(project_id, source_path, workspace)


def _claim_pending_result_group() -> tuple:
    """认领一个 pending result_group（project geom_status='ready'）。返回 5-tuple 或全 None。"""
    with _connect() as conn:
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
            return None, None, None, None, None
        row = conn.execute(
            "SELECT rg.project_id, rg.result_group, rg.source_path,"
            "       rg.parse_options, p.workspace"
            " FROM result_groups rg"
            " JOIN projects p ON rg.project_id = p.project_id"
            " WHERE rg.status='running' ORDER BY rg.updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None, None, None, None, None
        ws = row["workspace"]
        if not (os.path.isabs(ws) or re.match(r'^[A-Za-z]:[/\\]', ws)):
            ws = os.path.join(DATA_ROOT, row["project_id"])
        return (row["project_id"], row["result_group"],
                row["source_path"], row["parse_options"], ws)


def _update_result_group_status(project_id: str, result_group: str,
                                 status: str, error_message: str = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE result_groups SET status=?, error_message=?, updated_at=?"
            " WHERE project_id=? AND result_group=?",
            (status, error_message, _now_iso(), project_id, result_group),
        )


def _cleanup_result_group(workspace: str, result_group: str) -> None:
    rg_safe = result_group.replace("/", "__").replace("\\", "__").replace(" ", "_")
    for rel in [
        os.path.join("l1", "results", rg_safe),
        os.path.join("l1_raw", "consistency", rg_safe),
        os.path.join("l1_raw", "rg_{}".format(rg_safe)),
    ]:
        p = os.path.join(workspace, rel)
        if os.path.exists(p):
            shutil.rmtree(p)
            logger.info("Removed stale dir %s", p)

    manifest = os.path.join(workspace, "manifest.db")
    if os.path.exists(manifest):
        with sqlite3.connect(manifest, timeout=5.0) as conn:
            for table in ("steps", "frames", "result_files",
                          "result_blocks", "result_group_meta"):
                conn.execute(
                    "DELETE FROM {} WHERE result_group=?".format(table),
                    (result_group,),
                )


def _run_result_group(project_id: str, result_group: str,
                      source_path: str, parse_options_json: str,
                      workspace: str) -> bool:
    label = "{}/{}".format(project_id, result_group)
    if not source_path or not os.path.exists(source_path):
        msg = "ODB file not found: {}".format(source_path)
        logger.error("[%s] %s", label, msg)
        _update_result_group_status(project_id, result_group, "error", msg)
        _log_job(project_id, "error", "[{}] ODB 文件不存在：{}".format(result_group, source_path),
                 stage="rg_preflight")
        return False

    parse_opts = {}
    if parse_options_json:
        try:
            parse_opts = json.loads(parse_options_json)
        except Exception:
            pass

    check_mode = parse_opts.get("consistency_check", "count-only")
    display_name = parse_opts.get("display_name", result_group)
    source_file = os.path.basename(source_path)

    # Step 1: consistency check
    logger.info("[%s] preflight (%s)", label, check_mode)
    _log_job(project_id, "step",
             "[{}] 结果组解析开始：一致性校验（{}）".format(result_group, check_mode),
             stage="rg_preflight")
    rc, tail = _run_streaming(
        [ABAQUS_CMD, "python", str(DUMP_SCRIPT),
         "--odb", source_path, "--out", workspace,
         "--result-group", result_group,
         "--mode", "consistency-check",
         "--check-mode", check_mode],
        project_id, "rg_preflight",
    )
    if rc != 0:
        msg = "consistency-check failed: " + tail
        _update_result_group_status(project_id, result_group, "error", msg)
        _log_job(project_id, "error",
                 "[{}] 一致性校验失败 (rc={})：{}".format(result_group, rc, tail[-500:]),
                 stage="rg_preflight")
        return False

    # Step 2: extract results
    logger.info("[%s] extract", label)
    _log_job(project_id, "step",
             "[{}] 提取结果数据（abaqus_dump extract）".format(result_group),
             stage="rg_extract")
    inv_mode = parse_opts.get("invariants", INVARIANTS_MODE)
    extract_cmd = [ABAQUS_CMD, "python", str(DUMP_SCRIPT),
                   "--odb", source_path, "--out", workspace,
                   "--result-group", result_group,
                   "--mode", "extract"]
    if inv_mode == "full":
        extract_cmd += ["--invariants", "full"]
    _append_extract_filters(extract_cmd, parse_opts)
    rc, tail = _run_streaming(extract_cmd, project_id, "rg_extract")
    if rc != 0:
        msg = "extract failed: " + tail
        _update_result_group_status(project_id, result_group, "error", msg)
        _log_job(project_id, "error",
                 "[{}] 结果提取失败 (rc={})：{}".format(result_group, rc, tail[-500:]),
                 stage="rg_extract")
        return False

    # Step 3: pack into HDF5
    logger.info("[%s] l1_pack (result_group)", label)
    _log_job(project_id, "step",
             "[{}] 打包结果 HDF5（l1_pack）".format(result_group),
             stage="rg_l1_pack")
    rc, tail = _run_streaming(
        [sys.executable, str(PACK_SCRIPT),
         "--workspace", workspace,
         "--result-group", result_group,
         "--display-name", display_name,
         "--consistency-check", check_mode,
         "--source-file", source_file],
        project_id, "rg_l1_pack",
    )
    if rc != 0:
        msg = "l1_pack (result_group) failed: " + tail
        _update_result_group_status(project_id, result_group, "error", msg)
        _log_job(project_id, "error",
                 "[{}] 打包 HDF5 失败 (rc={})：{}".format(result_group, rc, tail[-500:]),
                 stage="rg_l1_pack")
        return False

    # If l1_pack patched sections into geometry H5 (INP+ODB mode), re-run L2
    # so averaging domains are rebuilt with correct section boundaries.
    marker = os.path.join(workspace, 'l1', 'geometry', '.sections_patched')
    if os.path.exists(marker):
        try:
            os.remove(marker)
        except Exception:
            pass
        _log_job(project_id, "step",
                 "[{}] 截面数据已补入几何，重跑 L2 重建平均域…".format(result_group),
                 stage="rg_rerun_l2")
        logger.info("[%s] sections patched — re-running L2", label)
        rc_l2r, tail_l2r = _run_streaming(
            [sys.executable, str(INGEST_SCRIPT), "--workspace", workspace],
            project_id, "rg_rerun_l2",
        )
        if rc_l2r != 0:
            _log_job(project_id, "warn",
                     "[{}] L2 重跑失败（非致命，平均域可能不完整）：{}".format(
                         result_group, tail_l2r[-300:]),
                     stage="rg_rerun_l2")
            logger.warning("[%s] L2 re-run failed (rc=%d), averaging domains may be incomplete",
                           label, rc_l2r)
        else:
            _log_job(project_id, "step",
                     "[{}] L2 重跑完成，平均域已更新".format(result_group),
                     stage="rg_rerun_l2")
            logger.info("[%s] L2 re-run complete", label)

    _update_result_group_status(project_id, result_group, "ready")
    _log_job(project_id, "step",
             "[{}] 结果组解析完成，已就绪".format(result_group),
             stage="rg_done")
    logger.info("[%s] ready", label)
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


def _recover_stuck_running_states() -> None:
    """
    启动时检查是否有因 runner 崩溃而卡在 'running' 状态的 project 或 result_group。
    将它们重置为 'pending'，以便本次启动重新认领处理。
    """
    try:
        with _connect() as conn:
            n1 = conn.execute(
                "UPDATE projects SET geom_status='pending', updated_at=?"
                " WHERE geom_status='running'",
                (_now_iso(),),
            ).rowcount
            n2 = conn.execute(
                "UPDATE result_groups SET status='pending', error_message=NULL, updated_at=?"
                " WHERE status='running'",
                (_now_iso(),),
            ).rowcount
        if n1 > 0:
            logger.warning("Recovered %d stuck project(s) (running → pending)", n1)
        if n2 > 0:
            logger.warning("Recovered %d stuck result_group(s) (running → pending)", n2)
    except Exception:
        logger.exception("Failed to recover stuck running states on startup")


def main() -> None:
    logger.info("job_runner starting (registry=%s)", REGISTRY_DB)

    _ensure_job_logs_schema()
    _recover_stuck_running_states()

    # Start heartbeat daemon
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    t.start()

    while True:
        did_work = False

        try:
            # 1. Project geometry (INP or ODB via POST /api/projects)
            project_id, source_path, source_type, ws = _claim_pending_project()
            if project_id is not None:
                did_work = True
                logger.info("Claimed project geom %s", project_id)
                try:
                    source_path = download_if_url(source_path, ws)
                    _run_project(project_id, source_path, source_type, ws)
                except Exception:
                    logger.exception("Error in project geom %s", project_id)
                    try:
                        _update_project_geom_status(
                            project_id, "error",
                            "Unhandled runner exception — check server logs")
                    except Exception:
                        pass

            # 2. Result groups (ODB, requires project geom_status='ready')
            project_id, rg, src, parse_opts, ws = _claim_pending_result_group()
            if project_id is not None:
                did_work = True
                label = "{}/{}".format(project_id, rg)
                logger.info("Claimed result_group %s", label)
                try:
                    _cleanup_result_group(ws, rg)
                    if is_http_url(src):
                        rg_safe = rg.replace("/", "__").replace("\\", "__").replace(" ", "_")
                        ext = os.path.splitext(src.split("?")[0])[1] or ".odb"
                        src = download_if_url(src, ws,
                                               dest_name="{}_source{}".format(rg_safe, ext))
                    _run_result_group(project_id, rg, src, parse_opts, ws)
                except Exception:
                    logger.exception("Error in result_group %s", label)
                    try:
                        _update_result_group_status(
                            project_id, rg, "error",
                            "Unhandled runner exception — check server logs")
                    except Exception:
                        pass

            # 3. Legacy ODB jobs
            odb_id, odb_path, workspace = _claim_submitted()
            if odb_id is not None:
                did_work = True
                logger.info("Claimed job %s", odb_id)
                try:
                    odb_path = download_if_url(odb_path, workspace)
                    _run_job(odb_id, odb_path, workspace)
                except Exception:
                    logger.exception("Error in job %s", odb_id)
                    try:
                        _update_status(odb_id, "error",
                                       error_msg="Unhandled runner exception — check server logs")
                    except Exception:
                        pass

        except Exception:
            logger.exception("Unexpected error in runner main loop — continuing")
            did_work = False

        if not did_work:
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
