from fastapi import APIRouter

from db import ensure_tables_exist

from ..common import server_error

router = APIRouter(tags=["model-update"])


@router.post("/init")
async def init_db():
    try:
        ensure_tables_exist()
        return {"ok": True, "message": "tables checked and ready", "data": None}
    except Exception as exc:
        raise server_error(exc) from exc
