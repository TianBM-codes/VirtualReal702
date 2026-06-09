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
_EXTERNAL_SENSITIVITY_FIELD_RE = re.compile(r"sensitivity", re.IGNORECASE)
_FRAME_ALIAS_RE = re.compile(r"^(?P<field>.+)__FRAME_(?P<frame>\d+)$")


def _rg_kind(rg: str) -> str:
    """区分 result_group 类型：'merge' | 'raw' | 'other'"""
    if _RAW_RE.match(rg):
        return "raw"
    if _MERGE_RE.match(rg):
        return "merge"
    return "other"


def _list_external_sensitivity_groups(manifest: ManifestRepo) -> set[str]:
    """
    Return external result groups that look like sensitivity outputs.

    Besides legacy `sensitivity_*` groups, this also surfaces external cloud
    results such as SOL200 `SENSITIVITY_CLOUD`.
    """
    try:
        with manifest._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT result_group, field_name
                  FROM result_files
                 WHERE source='external'
                   AND result_group IS NOT NULL
                   AND field_name IS NOT NULL
                """
            ).fetchall()
    except Exception:
        return set()

    groups: set[str] = set()
    for row in rows:
        result_group = str(row["result_group"] or "").strip()
        field_name = str(row["field_name"] or "").strip()
        if not result_group:
            continue
        if result_group.startswith(_SENSITIVITY_PREFIX):
            groups.add(result_group)
            continue
        if _EXTERNAL_SENSITIVITY_FIELD_RE.search(result_group) or (
            field_name and _EXTERNAL_SENSITIVITY_FIELD_RE.search(field_name)
        ):
            groups.add(result_group)
    return groups


def _make_frame_alias(field_name: str, frame_idx: int) -> str:
    return f"{str(field_name)}__FRAME_{int(frame_idx):04d}"


def _expand_external_sensitivity_fields(manifest: ManifestRepo, result_group: str, step_name: str, field_name: str) -> list[dict]:
    frames = manifest.get_frames(step_name, result_group=result_group) or []
    if not frames:
        parsed = _parse_merged_field_name(field_name)
        return [{
            "field_name": field_name,
            "source_field_name": field_name,
            "step": step_name,
            "frame_idx": None,
            "frame_value": None,
            "frame_description": None,
            **parsed,
        }]

    items = []
    for frame in frames:
        frame_idx = int(frame["frame_idx"])
        description = str(frame["description"] or "").strip() or f"Frame {frame_idx}"
        items.append({
            "field_name": _make_frame_alias(field_name, frame_idx),
            "source_field_name": field_name,
            "step": step_name,
            "frame_idx": frame_idx,
            "frame_value": float(frame["frame_value"]),
            "frame_description": description,
            # Keep legacy keys non-null so existing callers can still render a list.
            "response_node_label": frame_idx + 1,
            "component": description,
        })
    return items


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
    列出 workspace 内所有灵敏度 result_group。
    """
    idx = registry.get(odb_id)
    if idx is None:
        raise NotFoundError(f"ODB '{odb_id}' not found", {"odb_id": odb_id})
    manifest = ManifestRepo(idx.workspace)
    all_groups = manifest.list_result_groups()
    external_sensitivity_groups = _list_external_sensitivity_groups(manifest)

    groups = []
    for rg in all_groups:
        rg_text = str(rg or "")
        is_legacy_sensitivity = rg_text.startswith(_SENSITIVITY_PREFIX)
        is_external_sensitivity = rg_text in external_sensitivity_groups
        if not is_legacy_sensitivity and not is_external_sensitivity:
            continue
        # Keep hiding legacy raw DSA groups by default, but still surface
        # externally written sensitivity clouds such as sensitivity_batch_*.
        if (
            merge_only
            and is_legacy_sensitivity
            and _rg_kind(rg_text) != "merge"
            and not is_external_sensitivity
        ):
            continue
        groups.append(rg_text)

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
                "SELECT step_name, field_name, source FROM result_files"
                " WHERE result_group=? AND step_name=? ORDER BY field_name",
                (result_group, step),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT step_name, field_name, source FROM result_files"
                " WHERE result_group=? ORDER BY field_name",
                (result_group,),
            ).fetchall()

    for row in rows:
        field_name = str(row["field_name"] or "")
        source = str(row["source"] or "").strip().lower()
        step_name = str(row["step_name"] or "")
        parsed = _parse_merged_field_name(field_name)
        if source == "external" and parsed["response_node_label"] is None and parsed["component"] is None:
            fields.extend(
                _expand_external_sensitivity_fields(
                    manifest,
                    result_group=result_group,
                    step_name=step_name,
                    field_name=field_name,
                )
            )
            continue
        fields.append({
            "field_name": field_name,
            "source_field_name": field_name,
            "step": step_name,
            "frame_idx": None,
            "frame_value": None,
            "frame_description": None,
            **parsed,
        })

    fields.sort(
        key=lambda x: (
            x.get("step") or "",
            x.get("source_field_name") or x.get("field_name") or "",
            -1 if x.get("frame_idx") is None else int(x.get("frame_idx")),
            x.get("response_node_label") or 0,
            x.get("component") or "",
        )
    )
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
