"""Middleware helpers for modal-service compatibility routes."""

from typing import Any, Optional, Tuple

from fastapi import Request


API_TEST_MESH_PREFIX = "/api/model/testMesh"
LEGACY_TEST_MESH_PREFIXES = ("//model/testMesh", "/model/testMesh")


def rewrite_test_mesh_path(path: str) -> str:
    """Map proxy-stripped testMesh paths back to the registered API prefix."""
    if path == API_TEST_MESH_PREFIX or path.startswith(f"{API_TEST_MESH_PREFIX}/"):
        return path

    for prefix in LEGACY_TEST_MESH_PREFIXES:
        if path == prefix:
            return API_TEST_MESH_PREFIX
        if path.startswith(f"{prefix}/"):
            return f"{API_TEST_MESH_PREFIX}{path[len(prefix):]}"

    return path


def rewrite_test_mesh_scope(scope: dict[str, Any]) -> Optional[Tuple[str, str]]:
    path = scope.get("path")
    if not isinstance(path, str):
        return None

    new_path = rewrite_test_mesh_path(path)
    if new_path == path:
        return None

    scope["path"] = new_path
    raw_path = scope.get("raw_path")
    if isinstance(raw_path, (bytes, bytearray)):
        for prefix in LEGACY_TEST_MESH_PREFIXES:
            raw_prefix = prefix.encode("ascii")
            if raw_path == raw_prefix or raw_path.startswith(raw_prefix + b"/"):
                scope["raw_path"] = API_TEST_MESH_PREFIX.encode("ascii") + raw_path[len(raw_prefix):]
                break

    return path, new_path


def add_test_mesh_route_rewrite_middleware(app) -> None:
    @app.middleware("http")
    async def rewrite_test_mesh_proxy_route(request: Request, call_next):
        rewrite_test_mesh_scope(request.scope)
        return await call_next(request)
