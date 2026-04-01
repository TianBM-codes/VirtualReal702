import uuid
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from .errors import AppError

logger = logging.getLogger(__name__)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = str(uuid.uuid4())
    if exc.status_code >= 500:
        logger.error(
            "AppError [%s] %s: %s",
            request_id, exc.code, exc.message,
            exc_info=exc,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "meta": {"request_id": request_id},
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    logger.exception("Unhandled error [%s]", request_id)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
                "details": {},
            },
            "meta": {"request_id": request_id},
        },
    )
