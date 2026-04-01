"""
L3 FastAPI entry point.

Start with:
    uvicorn main:app --reload                         # dev
    gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker  # prod
"""
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.errors import AppError
from .core.exception_handlers import app_error_handler, unhandled_error_handler
from .core.state import registry
from .api.router import router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _bootstrap_registry() -> None:
    """
    Pre-fork: load ODB workspaces into the in-memory registry.

    Two modes:
      - Dev mode  : APP_ODB_WORKSPACE is set → load that single workspace directly.
      - Prod mode : read registry.db jobs table → load all ready workspaces.
    """
    # Dev mode: single workspace via env var
    if settings.odb_workspace:
        ws = settings.odb_workspace
        odb_id = settings.odb_id or os.path.basename(os.path.normpath(ws))
        try:
            registry.load(odb_id, ws, "ready")
            logger.info("Dev mode: loaded workspace '%s' as odb_id='%s'", ws, odb_id)
        except Exception:
            logger.exception("Dev mode: failed to load workspace '%s'", ws)
        return

    # Prod mode: read global registry.db
    try:
        conn = sqlite3.connect(settings.registry_db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT odb_id, workspace, status FROM jobs"
        ).fetchall()
        conn.close()
    except Exception:
        logger.warning(
            "Could not read registry.db at %s — starting with empty registry",
            settings.registry_db_path,
            exc_info=True,
        )
        return

    for row in rows:
        try:
            registry.load(row["odb_id"], row["workspace"], row["status"])
            logger.info("Loaded ODB %s (status=%s)", row["odb_id"], row["status"])
        except Exception:
            logger.exception("Failed to load ODB %s", row["odb_id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap_registry()
    yield
    # Nothing to clean up (HDF5 files are opened/closed per request)


app = FastAPI(
    title="ODB L3 Service",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow browser requests from any origin (file://, localhost variants, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Val-Min", "X-Val-Max", "X-Component", "X-Frame",
                    "X-Face-Count", "X-Payload-Type", "X-Layout-Version",
                    "X-Tri-Count", "X-Edge-Count", "X-Axis", "X-Position"],
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(router)

# Serve tools/ as /static  →  http://127.0.0.1:8000/static/viewer.html
# __file__ = src/l3/main.py  →  .parent×3 = repo root
_tools_dir = Path(__file__).resolve().parent.parent.parent / "tools"
if _tools_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_tools_dir)), name="static")
