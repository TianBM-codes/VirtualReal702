class AppError(Exception):
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", status_code: int = 500, details: dict = None):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)

class NotFoundError(AppError):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="NOT_FOUND", status_code=404, details=details)

class NotReadyError(AppError):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="NOT_READY", status_code=202, details=details)

class ConflictError(AppError):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="CONFLICT", status_code=409, details=details)

class ValidationError(AppError):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="BAD_REQUEST", status_code=400, details=details)
