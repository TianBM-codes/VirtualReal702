from fastapi import APIRouter

from ..response import ok

router = APIRouter(tags=["health"])


@router.get("/api/health/live")
async def live():
    return ok({"status": "live"})


@router.get("/api/health/ready")
async def ready():
    return ok({"status": "ready"})
