"""
Simright DATA-API compatibility layer.

Exposes a single endpoint mirroring the simright 3DLite API:

  POST /applications/3dlite/api/v1/query
  Body: {"name": "<handler>", "args": {...}}

Response envelope:
  {"code": 0, "data": <any>, "message": "success"}
  {"code": <non-zero>, "data": null, "message": "<error description>"}

Supported name values:
  loadcases    — list load cases (steps)
  variables    — list result variables for a step
  assemble     — assembly tree (parts + sets)
  extremeValue — global min/max of a field across frames
  nodeInfo     — per-node positions and result values
  elementInfo  — per-element positions and result values
  XYCurveData1 — time-series XY data for a list of nodes
  freqValue    — modal frequencies
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any, Dict, Optional

from ...core.errors import AppError
from ...core.state import registry
from ...services import simright_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simright"])


class SimrightRequest(BaseModel):
    name: str
    args: Dict[str, Any] = {}


def _ok(data: Any) -> Dict:
    return {"code": 200, "data": data, "message": ""}


def _err(code: int, message: str) -> Dict:
    return {"code": code, "data": None, "message": message}


@router.post("/applications/3dlite/api/v1/query")
async def simright_query(body: SimrightRequest):
    """
    Universal query endpoint compatible with the simright 3DLite DATA-API.
    The `name` field selects the operation; `args` carries all parameters.
    """
    try:
        data = simright_service.dispatch(body.name, body.args, registry)
        return _ok(data)
    except AppError as e:
        logger.warning("simright query '%s' error: %s", body.name, e.message)
        return _err(400, e.message)
    except Exception as e:
        logger.exception("simright query '%s' unexpected error", body.name)
        return _err(500, str(e))
