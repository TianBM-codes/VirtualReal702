"""
GET  /api/odb/{odb_id}/fields
POST /api/odb/{odb_id}/results/node-table
POST /api/odb/{odb_id}/results/node-time-value

Field discovery and batch node-result query endpoints.

POST /results/node-time-value
-----------------------------
HTTP 状态码永远是 200 —— 业务错误（时间越界、FREQUENCY 步不能插值、字段
没有 NODAL 位置……）通过信封里的 code(4xx) + message(中文) 表达，data 仍是
与成功时同构的结构，只是 nodes[].values 为 null。调用方只解析响应体即可。

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

from ...core.errors import AppError
from ...core.state import registry
from ...infra.l3be import build as l3be_build
from ...services.node_table_service import get_instance_fields, get_node_table
from ...services.node_time_value_service import get_node_time_value
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


# ── POST /results/node-time-value ──────────────────────────────────────────────

class NodeTimeValueRequest(BaseModel):
    instance: str
    field: str
    node_labels: List[int]
    time: float
    step: Optional[str] = None          # 传=step 内局部时间；不传=全局时间
    time_match: str = "interp"          # prev | next | interp
    result_group: Optional[str] = None


def _empty_time_value_data(body: "NodeTimeValueRequest") -> dict:
    """出错时返回一份与成功响应同构、但取不到值的 data。

    调用方（前端）因此只需要一套解析逻辑：nodes 数组永远在，found=false /
    values=null 表示这个节点这次没取到值。
    """
    return {
        "instance": body.instance,
        "field": body.field,
        "components": [],
        "invariants": [],
        "time_mode": "step" if body.step else "global",
        "step": body.step,
        "requested_time": body.time,
        "time_match": body.time_match,
        "resolved_mode": None,
        "frames_used": [],
        "time_range": None,
        "nodes": [{"label": int(l), "found": False, "values": None}
                  for l in body.node_labels],
    }


@router.post("/results/node-time-value")
async def get_node_time_value_endpoint(
    odb_id: str,
    body: NodeTimeValueRequest,
):
    # 本端点的业务错误一律走 HTTP 200 + 信封里的 code/message，调用方不必再区分
    # "HTTP 报错" 和 "业务报错" 两条分支。未预期的异常仍然抛出走 500。
    try:
        data = get_node_time_value(
            registry=registry,
            odb_id=odb_id,
            instance=body.instance,
            field=body.field,
            node_labels=body.node_labels,
            time=body.time,
            step=body.step,
            time_match=body.time_match,
            result_group=body.result_group,
        )
    except AppError as exc:
        return {
            "code": exc.status_code,
            "data": _empty_time_value_data(body),
            "message": exc.message,
        }
    return ok(data)
