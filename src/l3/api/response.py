"""
统一 JSON 响应格式工具函数。

所有 JSON 接口统一返回：
  成功: {"code": 200, "data": <any>,  "message": ""}
  失败: {"code": <4xx/5xx>, "data": null, "message": "<error>"}
"""
from typing import Any


def ok(data: Any = None) -> dict:
    return {"code": 200, "data": data, "message": ""}


def err(code: int, message: str) -> dict:
    return {"code": code, "data": None, "message": message}
