from __future__ import annotations

import hashlib
import os
from typing import Dict, Iterable, Optional, Tuple

import h5py
import numpy as np

from .project_config_service import _fetch_project_config, upsert_project_config
from .project_path_service import resolve_project_cal_subdir


_BUNDLE_DIR_NAME = "fem_capability_bundle"
_DETAIL_FILE_NAME = "capability_details.h5"


def _bundle_dir(project_id: int) -> str:
    return resolve_project_cal_subdir(int(project_id), _BUNDLE_DIR_NAME)


def capability_detail_h5_path(project_id: int) -> str:
    return os.path.join(_bundle_dir(int(project_id)), _DETAIL_FILE_NAME)


def capability_detail_key(
    *,
    quantity_code: str,
    set_name: str,
    set_type: str,
    set_scope: str,
    instance_name: Optional[str],
    part_name: Optional[str],
) -> str:
    raw_key = "|".join(
        [
            str(quantity_code or "").strip(),
            str(set_scope or "").strip(),
            str(set_type or "").strip(),
            str(set_name or "").strip(),
            str(instance_name or "").strip(),
            str(part_name or "").strip(),
        ]
    )
    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()


def split_capability_rows(capability_rows: Iterable[dict]) -> Tuple[list[dict], Dict[str, dict]]:
    compact_rows: list[dict] = []
    detail_rows: Dict[str, dict] = {}

    for row in capability_rows or []:
        item = dict(row or {})
        extra = dict(item.get("extra_json") or {})
        detail_payload = {}

        element_labels = [int(x) for x in (extra.get("element_labels") or [])]
        if element_labels:
            detail_payload["element_labels"] = element_labels

        element_values = {
            str(key): float(value)
            for key, value in dict(extra.get("element_values") or {}).items()
            if value is not None
        }
        if element_values:
            detail_payload["element_values"] = element_values

        compact_extra = {
            key: value
            for key, value in extra.items()
            if key not in {"element_labels", "target_keys", "target_keys_by_label", "element_values"}
        }

        if detail_payload:
            detail_key = capability_detail_key(
                quantity_code=str(item.get("quantity_code") or ""),
                set_name=str(item.get("set_name") or ""),
                set_type=str(item.get("set_type") or ""),
                set_scope=str(item.get("set_scope") or ""),
                instance_name=item.get("instance_name"),
                part_name=item.get("part_name"),
            )
            compact_extra["detail_key"] = detail_key
            detail_rows[detail_key] = detail_payload

        item["extra_json"] = compact_extra
        compact_rows.append(item)

    return compact_rows, detail_rows


def write_capability_detail_h5(
    project_id: int,
    detail_rows: Dict[str, dict],
    *,
    cursor=None,
) -> Optional[str]:
    path = capability_detail_h5_path(int(project_id))
    if not detail_rows:
        if os.path.isfile(path):
            os.remove(path)
        upsert_project_config(
            int(project_id),
            extra_json={"fem_capability_bundle": {"detail_h5_path": None, "detail_count": 0}},
            cursor=cursor,
        )
        return None

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with h5py.File(path, "w") as h5f:
        h5f.attrs["version"] = 1
        details_group = h5f.require_group("details")
        for detail_key, payload in sorted(detail_rows.items()):
            grp = details_group.require_group(str(detail_key))

            element_labels = np.asarray(payload.get("element_labels") or [], dtype=np.int64)
            grp.create_dataset("element_labels", data=element_labels)

            element_values = dict(payload.get("element_values") or {})
            if element_values:
                value_labels = np.asarray([int(key) for key in element_values.keys()], dtype=np.int64)
                value_values = np.asarray([float(value) for value in element_values.values()], dtype=np.float64)
            else:
                value_labels = np.zeros((0,), dtype=np.int64)
                value_values = np.zeros((0,), dtype=np.float64)
            grp.create_dataset("element_value_labels", data=value_labels)
            grp.create_dataset("element_value_values", data=value_values)

    upsert_project_config(
        int(project_id),
        extra_json={
            "fem_capability_bundle": {
                "detail_h5_path": os.path.abspath(path),
                "detail_count": int(len(detail_rows)),
            }
        },
        cursor=cursor,
    )
    return os.path.abspath(path)


def _configured_detail_h5_path(project_id: int, *, cursor=None) -> Optional[str]:
    config = _fetch_project_config(cursor, int(project_id)) if cursor is not None else None
    extra_json = dict((config or {}).get("extra_json") or {})
    bundle = dict(extra_json.get("fem_capability_bundle") or {})
    path = str(bundle.get("detail_h5_path") or "").strip()
    if path and os.path.isfile(path):
        return os.path.abspath(path)
    default_path = capability_detail_h5_path(int(project_id))
    if os.path.isfile(default_path):
        return os.path.abspath(default_path)
    return None


def load_capability_detail(project_id: int, detail_key: str, *, cursor=None) -> dict:
    path = _configured_detail_h5_path(int(project_id), cursor=cursor)
    if not path or not detail_key:
        return {}

    with h5py.File(path, "r") as h5f:
        details_group = h5f.get("details")
        if details_group is None or str(detail_key) not in details_group:
            return {}
        grp = details_group[str(detail_key)]
        element_labels = [int(value) for value in np.asarray(grp.get("element_labels", ()), dtype=np.int64).tolist()]
        value_labels = np.asarray(grp.get("element_value_labels", ()), dtype=np.int64)
        value_values = np.asarray(grp.get("element_value_values", ()), dtype=np.float64)
        element_values = {
            str(int(label)): float(value)
            for label, value in zip(value_labels.tolist(), value_values.tolist())
        }
        payload = {"element_labels": element_labels}
        if element_values:
            payload["element_values"] = element_values
        return payload
