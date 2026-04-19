"""
GET  /api/odb/{odb_id}/fields
POST /api/odb/{odb_id}/results/node-table

Field discovery and batch node-result query endpoints.

GET /fields
-----------
Returns the fields available for a given (instance, step).
Uses result_blocks (instance-level) for positions; result_files for components.
Response is JSON — small metadata payload.

POST /results/node-table
------------------------
Batch-queries NODAL result values for a set of node labels and returns an
L3BE binary payload containing a [N, M] float32 values matrix.

  N = len(node_labels)
  M = len(items)

Response headers
----------------
X-Payload-Type    node_table_v1
X-Layout-Version  1
X-Node-Count      N
X-Col-Count       M
X-Columns         JSON array of column definitions (same order as items)
X-Field-Coverage  step   — availability is guaranteed at step level, not frame level

L3BE section layout
-------------------
  "node_labels"  [N]    int32    ODB node labels (same order as request)
  "values"       [N, M] float32  result values; NaN = node not in instance

See docs/l3/L3-Node-Field-Table-Requirement.md for full design rationale.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.node_table_service import get_instance_fields, get_node_table
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["results"])


# ── GET /fields ────────────────────────────────────────────────────────────────

@router.get("/fields")
async def list_fields(
    odb_id: str,
    instance: str = Query(..., description="Instance name"),
    step: str = Query(..., description="Step name"),
    result_group: Optional[str] = Query(None, description="Result group (project mode)"),
):
    fields = get_instance_fields(registry, odb_id, instance, step, result_group)
    return ok({"instance": instance, "step": step, "fields": fields})


# ── POST /results/node-table ───────────────────────────────────────────────────

class QueryItem(BaseModel):
    field: str
    component: str = ""


class NodeTableRequest(BaseModel):
    instance: str
    step: str
    frame_idx: int
    node_labels: List[int]
    items: List[QueryItem]
    result_group: Optional[str] = None


@router.post("/results/node-table")
async def get_node_table_endpoint(
    odb_id: str,
    body: NodeTableRequest,
):
    sections, columns = get_node_table(
        registry=registry,
        odb_id=odb_id,
        instance=body.instance,
        step=body.step,
        frame_idx=body.frame_idx,
        node_labels=body.node_labels,
        items=[{"field": it.field, "component": it.component} for it in body.items],
        result_group=body.result_group,
    )

    payload = l3be_build(sections)

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "X-Payload-Type":   "node_table_v1",
            "X-Layout-Version": "1",
            "X-Node-Count":     str(len(body.node_labels)),
            "X-Col-Count":      str(len(body.items)),
            "X-Columns":        json.dumps(columns),
            "X-Field-Coverage": "step",
        },
    )
