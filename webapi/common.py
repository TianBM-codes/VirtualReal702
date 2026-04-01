from src.l3.core.errors import AppError


def server_error(exc: Exception) -> AppError:
    return AppError(message=str(exc), status_code=500)
