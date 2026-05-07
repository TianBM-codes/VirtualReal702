from fastapi import APIRouter

from db import ensure_tables_exist

from ..common import error_response, server_error, success_response

router = APIRouter(tags=["model-update"])


@router.post("/init")
async def init_db():
    try:
        ensure_tables_exist()
        return success_response(None, "database tables are ready")
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(
            app_exc.status_code,
            app_exc.message,
            error_code=app_exc.code,
            details=app_exc.details,
        )
