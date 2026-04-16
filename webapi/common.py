from fastapi.responses import JSONResponse

from src.l3.core.errors import AppError


def server_error(exc: Exception) -> AppError:
    # Normalize unexpected exceptions into the same shape used by the
    # application-specific AppError hierarchy.
    return AppError(message=str(exc), status_code=500)


def success_response(data=None, message: str = "璇锋眰鎴愬姛") -> dict:
    # Keep a stable success envelope across all routers so the frontend and
    # debug tooling can parse responses without endpoint-specific branches.
    return {
        "ok": True,
        "code": 200,
        "message": str(message),
        "data": data,
    }


def error_response(
    status_code: int,
    message: str,
    *,
    error_code: str = "INTERNAL_ERROR",
    details: dict = None,
):
    # Use JSONResponse so FastAPI preserves the requested HTTP status code
    # together with the shared error payload structure.
    return JSONResponse(
        status_code=int(status_code),
        content={
            "ok": False,
            "code": int(status_code),
            "message": str(message),
            "data": None,
            "error": {
                "code": str(error_code),
                "message": str(message),
                "details": details or {},
            },
        },
    )
