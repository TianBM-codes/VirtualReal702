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


if __name__ == "__main__":
    bdf_path = str(Path(BDF_PATH).expanduser().resolve())
    out_dir = Path(OUTPUT_DIR).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parser = BDFParser(bdf_path)
    parser.parse()

    node_data = _build_bdf_octree_node_data(parser)
    capabilities = _build_bdf_property_set_capabilities(parser)
    tree = _build_octree(node_data["point_coords"])

    stem = Path(bdf_path).stem
    npz_path = out_dir / f"{stem}.node_octree.npz"
    json_path = out_dir / f"{stem}.octree_bundle.json"

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
        "node_count": int(len(node_data["point_labels"])),
        "instance_count": int(len(set(str(x) for x in node_data["point_instances"]))),
        "bbox_min": node_data["bbox_min"].tolist(),
        "bbox_max": node_data["bbox_max"].tolist(),
        "quantity_set_capabilities": capabilities,
        "quantity_set_capability_count": len(capabilities),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")

    print("npz :", npz_path)
    print("json:", json_path)
    print("capability_count:", len(capabilities))
