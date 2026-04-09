import json
from typing import Any


def _read_attr(obj: Any, name: str, default=None):
    return getattr(obj, name, default)


def _resolve_path(request: Any) -> Any:
    url = _read_attr(request, "url")
    if url is not None:
        path = _read_attr(url, "path")
        if path is not None:
            return path
    return _read_attr(request, "path")


def _resolve_client_ip(request: Any) -> Any:
    client = _read_attr(request, "client")
    if client is not None:
        host = _read_attr(client, "host")
        if host is not None:
            return host
    return _read_attr(request, "remote_addr")


def log_request(request: Any, body: Any = None) -> None:
    method = _read_attr(request, "method")
    if method != "POST":
        return

    log_info = {
        "method": method,
        "path": _resolve_path(request),
        "body": body,
        "client_ip": _resolve_client_ip(request),
    }

    print("\n[Request Log]")
    print(json.dumps(log_info, indent=2, ensure_ascii=False, default=str))
