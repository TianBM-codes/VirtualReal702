"""
统一 JSON 响应格式工具函数。

所有 JSON 接口统一返回：
  成功: {"code": 200, "data": <any>,  "message": ""}
  失败: {"code": <4xx/5xx>, "data": null, "message": "<error>"}
"""
from typing import Any, Optional


def nullable_float(value: Any) -> Optional[float]:
    """把查询参数解析成 Optional[float]，容忍字面量 'null'/'none'/'' 等。

    前端用 JS 把 null 序列化进 URL 时会变成字符串 "null"（例如
    `?global_min=null`），而 FastAPI 的 `Optional[float]` 会因为无法把
    "null" 解析成数字而直接返回 422。所以这些"占位空值"的接口参数统一按
    str 接收、再用本函数解析：空串/null/none/undefined/nan → None，
    其余按 float 解析。
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() in ("null", "none", "undefined", "nan"):
        return None
    return float(s)


def ok(data: Any = None) -> dict:
    return {"code": 200, "data": data, "message": ""}


def err(code: int, message: str) -> dict:
    return {"code": code, "data": None, "message": message}
