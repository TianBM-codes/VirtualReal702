from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/api/health/live")
async def live():
    return {"ok": True, "data": {"status": "live"}}


@router.get("/api/health/ready")
async def ready():
    return {"ok": True, "data": {"status": "ready"}}
