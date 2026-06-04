import logging
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .errors import AppError

logger = logging.getLogger(__name__)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("AppError %s: %s", exc.code, exc.message, exc_info=exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "data": None, "message": exc.message},
    )


_LOC_CN = {"query": "查询参数", "body": "请求体", "path": "路径参数", "header": "请求头"}

_TYPE_CN = {
    "missing":                  "必填",
    "string_pattern_mismatch":  lambda e: f"格式错误，应满足 {e.get('ctx', {}).get('pattern', '')}",
    "greater_than_equal":       lambda e: f"不能小于 {e.get('ctx', {}).get('ge', '')}",
    "less_than_equal":          lambda e: f"不能大于 {e.get('ctx', {}).get('le', '')}",
    "greater_than":             lambda e: f"必须大于 {e.get('ctx', {}).get('gt', '')}",
    "less_than":                lambda e: f"必须小于 {e.get('ctx', {}).get('lt', '')}",
    "int_parsing":              "需要整数",
    "float_parsing":            "需要数字",
    "bool_parsing":             "需要布尔值（true/false）",
    "string_too_long":          lambda e: f"长度不能超过 {e.get('ctx', {}).get('max_length', '')}",
    "string_too_short":         lambda e: f"长度不能少于 {e.get('ctx', {}).get('min_length', '')}",
    "enum":                     lambda e: f"不在允许值范围内，可选：{e.get('ctx', {}).get('expected', '')}",
}


def _format_error_cn(e: dict) -> str:
    loc_parts = [_LOC_CN.get(str(p), str(p)) for p in e["loc"]]
    loc_str = " -> ".join(loc_parts)
    rule = _TYPE_CN.get(e["type"])
    if rule is None:
        msg = e["msg"]
    elif callable(rule):
        msg = rule(e)
    else:
        msg = rule
    return f"{loc_str}：{msg}"


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    parts = [_format_error_cn(e) for e in exc.errors()]
    return JSONResponse(
        status_code=500,
        content={"code": 500, "data": None, "message": "; ".join(parts)},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "data": None, "message": "An unexpected error occurred."},
    )
