import re
from typing import Optional

from fastapi import APIRouter, Query

from ...core.errors import NotFoundError
from ...core.state import registry
from ...infra.manifest_repo import ManifestRepo
from ..response import ok

router = APIRouter(prefix="/api/odb/{odb_id}", tags=["sensitivity"])

_SENSITIVITY_PREFIX = "sensitivity_"
# 原始 result_group: sensitivity_batch_<batch_no>_<job_name>_<ts>
_RAW_RE = re.compile(r"^sensitivity_batch_")
# 合并 result_group: sensitivity_<batch_no>_<field_prefix>  (第二段是数字)
_MERGE_RE = re.compile(r"^sensitivity_\d+_")


def _rg_kind(rg: str) -> str:
    """区分 result_group 类型：'merge' | 'raw' | 'other'"""
    if _RAW_RE.match(rg):
        return "raw"
    if _MERGE_RE.match(rg):
        return "merge"
    return "other"


@router.get("/sensitivity/result_groups")
async def list_sensitivity_result_groups(
    odb_id: str,
    merge_only: bool = Query(
        default=True,
        # 默认只返回合并 result_group（sensitivity_<batch>_<field>）。
        # 原始组（sensitivity_batch_*）仅供调试，前端字段下拉不需要它们。
        description="False = 同时返回原始 DSA result_group（调试用）",
    ),
):
    """
    列出 workspace 内所有以 sensitivity_ 开头的 result_group。
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    all_groups = manifest.list_result_groups()

    groups = []
    for rg in all_groups:
        if not str(rg).startswith(_SENSITIVITY_PREFIX):
            continue
        if merge_only and _rg_kind(rg) != "merge":
            continue
        groups.append(rg)

    return ok({"result_groups": groups})


@router.get("/sensitivity/fields")
async def list_sensitivity_fields(
    odb_id: str,
    result_group: str = Query(..., description="sensitivity_* result group 名"),
    step: Optional[str] = Query(default=None, description="分析步名，缺省返回所有步的字段"),
):
    """
    列出指定 result_group 下的合并场字段名（如 d_4_U1_T）。
    前端用作第二个下拉：选择具体字段上色。

    返回结构：
    {
      "result_group": "sensitivity_1_U",
      "fields": [
        { "field_name": "d_4_U1_T", "step": "Step-1",
          "response_node_label": 4, "component": "U1" },
        ...
      ]
    }
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)

    fields = []
    with manifest._get_conn() as conn:
        if step:
            rows = conn.execute(
                "SELECT step_name, field_name FROM result_files"
                " WHERE result_group=? AND step_name=? ORDER BY field_name",
                (result_group, step),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT step_name, field_name FROM result_files"
                " WHERE result_group=? ORDER BY field_name",
                (result_group,),
            ).fetchall()

    for row in rows:
        field_name = str(row["field_name"] or "")
        parsed = _parse_merged_field_name(field_name)
        fields.append({
            "field_name": field_name,
            "step": row["step_name"],
            **parsed,
        })

    fields.sort(key=lambda x: (x.get("step") or "", x.get("response_node_label") or 0, x.get("component") or ""))
    return ok({"result_group": result_group, "fields": fields})


# ── 内部工具 ──────────────────────────────────────────────────────────────────

_MERGED_FIELD_RE = re.compile(r"^d_(\d+)_(U[123])_T$", re.IGNORECASE)


def _parse_merged_field_name(field_name: str) -> dict:
    """从 d_4_U1_T 提取 response_node_label=4, component=U1。解析失败返回空字段。"""
    m = _MERGED_FIELD_RE.match(field_name)
    if not m:
        return {"response_node_label": None, "component": None}
    return {
        "response_node_label": int(m.group(1)),
        "component": m.group(2).upper(),
    }
