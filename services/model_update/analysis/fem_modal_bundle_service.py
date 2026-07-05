import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .project_config_service import _fetch_project_config, upsert_project_config
from .project_path_service import resolve_project_cal_subdir


_BUNDLE_DIR_NAME = "fem_modal_bundle"
_MANIFEST_NAME = "manifest.json"


def _bundle_dir(project_id: int) -> str:
    return resolve_project_cal_subdir(int(project_id), _BUNDLE_DIR_NAME)


def _manifest_path(project_id: int) -> str:
    return os.path.join(_bundle_dir(int(project_id)), _MANIFEST_NAME)


def _read_manifest(path: str) -> Optional[dict]:
    if not path or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return dict(payload) if isinstance(payload, dict) else None


def load_fem_modal_manifest(project_id: int, cursor=None) -> Optional[dict]:
    manifest = None
    if cursor is not None:
        config = _fetch_project_config(cursor, int(project_id))
        extra_json = dict(config.get("extra_json") or {})
        fem_modal_bundle = dict(extra_json.get("fem_modal_bundle") or {})
        manifest = _read_manifest(str(fem_modal_bundle.get("manifest_path") or ""))
    if manifest:
        return manifest
    return _read_manifest(_manifest_path(int(project_id)))


def list_fem_modal_frequencies(project_id: int, cursor=None) -> List[dict]:
    manifest = load_fem_modal_manifest(int(project_id), cursor=cursor)
    if not manifest:
        return []
    rows = []
    for item in list(manifest.get("modes") or []):
        rows.append(
            {
                "mode_no": int(item["mode_no"]),
                "frequency": None if item.get("frequency") is None else float(item["frequency"]),
            }
        )
    rows.sort(key=lambda row: row["mode_no"])
    return rows


def load_fem_mode_vectors_from_bundle(project_id: int, cursor=None) -> Tuple[Dict[int, Dict[Tuple[str, int], np.ndarray]], Dict[int, Optional[float]]]:
    manifest = load_fem_modal_manifest(int(project_id), cursor=cursor)
    if not manifest:
        return {}, {}

    default_instance = str(manifest.get("instance_name") or "BDF_MODEL")
    modes: Dict[int, Dict[Tuple[str, int], np.ndarray]] = {}
    freqs: Dict[int, Optional[float]] = {}
    for item in list(manifest.get("modes") or []):
        file_path = str(item.get("file_path") or "").strip()
        if not file_path or not os.path.isfile(file_path):
            continue
        data = np.load(file_path)
        mode_no = int(item["mode_no"])
        node_labels = np.asarray(data["node_labels"], dtype=np.int64)
        vectors = np.asarray(data["vectors"], dtype=np.float64)
        instance_name = str(item.get("instance_name") or default_instance)
        freqs[mode_no] = None if item.get("frequency") is None else float(item["frequency"])
        mode_map: Dict[Tuple[str, int], np.ndarray] = {}
        for idx, node_label in enumerate(node_labels.tolist()):
            mode_map[(instance_name, int(node_label))] = np.asarray(vectors[idx], dtype=np.float64).reshape(3)
        if mode_map:
            modes[mode_no] = mode_map
    return modes, freqs


def clear_fem_modal_bundle(project_id: int) -> str:
    bundle_dir = _bundle_dir(int(project_id))
    for path in Path(bundle_dir).glob("*"):
        if path.is_file():
            path.unlink()
    return bundle_dir


def save_fem_modal_manifest(
    project_id: int,
    manifest: dict,
    *,
    cursor=None,
) -> dict:
    path = _manifest_path(int(project_id))
    payload = dict(manifest or {})
    payload["project_id"] = int(project_id)
    payload["manifest_path"] = os.path.abspath(path)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)

    extra_json = {
        "fem_modal_bundle": {
            "manifest_path": os.path.abspath(path),
            "bundle_dir": os.path.abspath(_bundle_dir(int(project_id))),
            "mode_count": int(len(list(payload.get("modes") or []))),
            "instance_name": payload.get("instance_name"),
            "part_name": payload.get("part_name"),
        }
    }
    upsert_project_config(int(project_id), extra_json=extra_json, cursor=cursor)
    return payload
