from fastapi.responses import JSONResponse

from src.l3.core.errors import AppError


def server_error(exc: Exception) -> AppError:
    return AppError(message=str(exc), status_code=500)


def success_response(data=None, message: str = "请求成功") -> dict:
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
