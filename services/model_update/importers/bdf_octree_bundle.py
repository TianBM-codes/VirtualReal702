import json
from pathlib import Path

import numpy as np

from BDFParserPyNastran import BDFParser
from services.model_update.analysis.fem_catalog_service import _build_octree
from services.model_update.importers.bdf_service import _build_bdf_octree_node_data, _build_bdf_property_set_capabilities


BDF_PATH = r"D:\your\model.bdf"
PROJECT_ID = 25
OUTPUT_DIR = r"D:\temp\bdf_bundle"


def _default(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    raise TypeError(type(x).__name__)


def _compact_capability_rows(rows):
    compact_rows = []
    detail_rows = {}
    for row in rows:
        extra = dict(row.get("extra_json") or {})
        detail_key = "|".join([
            str(row["quantity_code"]),
            str(row["set_scope"]),
            str(row["set_type"]),
            str(row["set_name"]),
            str(row.get("instance_name") or ""),
            str(row.get("part_name") or ""),
        ])
        compact_extra = {}
        if extra.get("property_id") is not None:
            compact_extra["property_id"] = int(extra["property_id"])
        if extra.get("material_id") is not None:
            compact_extra["material_id"] = int(extra["material_id"])
        compact_extra["detail_key"] = detail_key

        detail_rows[detail_key] = {
            "element_labels": [int(x) for x in (extra.get("element_labels") or [])],
            "target_keys": [str(x) for x in (extra.get("target_keys") or [])],
            "target_keys_by_label": {
                str(key): [str(x) for x in (value or [])]
                for key, value in dict(extra.get("target_keys_by_label") or {}).items()
            },
            "element_values": {
                str(key): value for key, value in dict(extra.get("element_values") or {}).items()
            },
        }

        compact_rows.append({
            "quantity_code": row["quantity_code"],
            "set_name": row["set_name"],
            "set_type": row["set_type"],
            "set_scope": row["set_scope"],
            "instance_name": row.get("instance_name"),
            "part_name": row.get("part_name"),
            "set_role": row["set_role"],
            "element_family": row.get("element_family"),
            "section_type": row.get("section_type"),
            "material_name": row.get("material_name"),
            "member_count": int(row.get("member_count", 0)),
            "supports_global": bool(row.get("supports_global")),
            "supports_local": bool(row.get("supports_local")),
            "current_value": row.get("current_value"),
            "extra_json": compact_extra,
        })
    return compact_rows, detail_rows


if __name__ == "__main__":
    bdf_path = str(Path(BDF_PATH).expanduser().resolve())
    out_dir = Path(OUTPUT_DIR).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parser = BDFParser(bdf_path)
    parser.parse()

    node_data = _build_bdf_octree_node_data(parser)
    capabilities, capability_details = _compact_capability_rows(_build_bdf_property_set_capabilities(parser))
    tree = _build_octree(node_data["point_coords"])

    stem = Path(bdf_path).stem
    npz_path = out_dir / f"{stem}.node_octree.npz"
    json_path = out_dir / f"{stem}.octree_bundle.json"
    detail_path = out_dir / f"{stem}.octree_capability_detail.json"

    np.savez_compressed(
        str(npz_path),
        node_bbox=tree["node_bbox"],
        node_children=tree["node_children"],
        node_is_leaf=tree["node_is_leaf"],
        point_indices=tree["point_indices"],
        point_offsets=tree["point_offsets"],
        point_coords=node_data["point_coords"].astype(np.float32),
        point_labels=node_data["point_labels"].astype(np.int64),
        point_instances=node_data["point_instances"].astype("U128"),
        point_parts=node_data["point_parts"].astype("U128"),
        bbox_min=node_data["bbox_min"].astype(np.float64),
        bbox_max=node_data["bbox_max"].astype(np.float64),
    )

    payload = {
        "project_id": PROJECT_ID,
        "source_file_path": bdf_path,
        "cache_file_path": str(npz_path),
        "capability_detail_file": str(detail_path),
        "node_count": int(len(node_data["point_labels"])),
        "instance_count": int(len(set(str(x) for x in node_data["point_instances"]))),
        "bbox_min": node_data["bbox_min"].tolist(),
        "bbox_max": node_data["bbox_max"].tolist(),
        "quantity_set_capabilities": capabilities,
        "quantity_set_capability_count": len(capabilities),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    detail_path.write_text(json.dumps({"details_by_key": capability_details}, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")

    print("npz :", npz_path)
    print("json:", json_path)
    print("detail:", detail_path)
    print("capability_count:", len(capabilities))
