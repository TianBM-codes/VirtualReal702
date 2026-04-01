from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live():
    return {"ok": True, "data": {"status": "live"}}


@router.get("/health/ready")
async def ready():
    return {"ok": True, "data": {"status": "ready"}}
