"""
L3 FastAPI entry point.

Start with:
    uvicorn src.l3.main:app --reload                              # dev
    gunicorn src.l3.main:app -w 4 -k uvicorn.workers.UvicornWorker  # prod
"""
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.errors import AppError
from .core.exception_handlers import app_error_handler, unhandled_error_handler
from .core.state import registry
from .infra.registry_repo import RegistryRepo
from .api.router import router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_POLL_INTERVAL = 10          # seconds between registry.db polls
_HEARTBEAT_TIMEOUT = 10      # minutes before a running job is declared stuck


def _bootstrap_registry() -> None:
    """
    Pre-fork: ensure schema exists, recover stuck jobs, then load all
    ready/l1_done ODB workspaces into the in-memory registry.

    Two modes:
      - Dev mode  : APP_ODB_WORKSPACE is set → load that single workspace directly.
      - Prod mode : read odb_jobs table in registry.db.
    """
    repo = RegistryRepo(settings.registry_db_path)

    # Recover jobs that were l1_running/l2_running when the process last died
    n_fixed = repo.mark_stuck_jobs_as_error(timeout_minutes=_HEARTBEAT_TIMEOUT)
    if n_fixed:
        logger.warning("Startup: marked %d stuck job(s) as error", n_fixed)

    # Dev mode: single workspace via env var
    if settings.odb_workspace:
        ws = settings.odb_workspace
        odb_id = settings.odb_id or Path(ws).name
        try:
            registry.load(odb_id, ws, "ready")
            logger.info("Dev mode: loaded workspace '%s' as odb_id='%s'", ws, odb_id)
        except Exception:
            logger.exception("Dev mode: failed to load workspace '%s'", ws)
        return

    # Prod mode: load all ready / l1_done jobs from odb_jobs table
    for row in repo.list_ready_or_l1done():
        odb_id    = row["odb_id"]
        workspace = repo.resolve_workspace(row["workspace"], settings.data_root)
        status    = row["status"]
        try:
            registry.load(odb_id, workspace, status)
            logger.info("Loaded ODB %s (status=%s)", odb_id, status)
        except Exception:
            logger.exception("Failed to load ODB %s", odb_id)


def _poll_registry_once(repo: RegistryRepo) -> None:
    """
    Check registry.db for changes:
    - New ready/l1_done ODB not yet in memory → load it.
    - Existing ODB upgraded from l1_done to ready → supplement with L2 data.
    """
    for row in repo.list_ready_or_l1done():
        odb_id = row["odb_id"]
        status = row["status"]
        existing = registry.get(odb_id)
        if existing is None:
            workspace = repo.resolve_workspace(row["workspace"], settings.data_root)
            registry.load(odb_id, workspace, status)
            logger.info("Poll: hot-loaded ODB %s (status=%s)", odb_id, status)
        elif status == "ready" and not existing.is_render_ready:
            registry.upgrade(odb_id)
            logger.info("Poll: upgraded ODB %s to render-ready", odb_id)


def _poll_loop(repo: RegistryRepo) -> None:
    while True:
        try:
            _poll_registry_once(repo)
        except Exception:
            logger.exception("Poll: registry check failed")
        time.sleep(_POLL_INTERVAL)


def _start_poll_thread() -> None:
    """
    Start one polling daemon thread per Gunicorn worker process.
    Each worker maintains its own independent copy of the registry in memory.
    (CoW sharing only applies to data loaded during _bootstrap_registry.)
    """
    repo = RegistryRepo(settings.registry_db_path)
    t = threading.Thread(
        target=_poll_loop, args=(repo,),
        daemon=True, name="registry-poll",
    )
    t.start()
    logger.debug("Registry poll thread started (interval=%ds)", _POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap_registry()
    _start_poll_thread()
    if settings.embedded_runner:
        from .infra.runner_thread import start_embedded_runner
        started = start_embedded_runner(
            registry_db=settings.registry_db_path,
            data_root=settings.data_root,
            abaqus_cmd=settings.abaqus_cmd,
            poll_interval=settings.runner_poll_interval,
        )
        if not started:
            logger.debug("Embedded runner not started in this worker (lock held elsewhere)")
    yield
    # daemon threads exit automatically when the process terminates


app = FastAPI(
    title="ODB L3 Service",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow browser requests from any origin (file://, localhost variants, etc.)
if settings.enable_gzip:
    app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "X-Val-Min", "X-Val-Max", "X-Component", "X-Frame",
        "X-Face-Count", "X-Payload-Type", "X-Layout-Version",
        "X-Tri-Count", "X-Edge-Count", "X-Axis", "X-Position",
        # raw-values endpoint
        "X-Components", "X-Etype-Groups",
        # node-table endpoints
        "X-Node-Count", "X-Col-Count", "X-Columns", "X-Field-Coverage",
        # user-field endpoints
        "X-Field-Name",
    ],
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(router)

# Serve tools/ as /static  →  http://127.0.0.1:8000/static/viewer.html
# __file__ = src/l3/main.py  →  .parent×3 = repo root
_tools_dir = Path(__file__).resolve().parent.parent.parent / "tools"
if _tools_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_tools_dir)), name="static")
