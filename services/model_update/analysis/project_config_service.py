import json
from typing import Any, Dict, Iterable, Optional, Sequence

import numpy as np

from db import ensure_tables_exist, get_connection


def _normalize_dims(dims: Optional[dict]) -> Optional[Dict[str, Optional[float]]]:
    if dims is None:
        return None
    normalized: Dict[str, Optional[float]] = {}
    for axis in ("x", "y", "z"):
        if axis not in dims:
            continue
        value = dims.get(axis)
        normalized[axis] = None if value is None else float(value)
    return normalized


def _normalize_mapping(payload: Optional[dict]) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for key, value in dict(payload or {}).items():
        normalized[str(key)] = float(value)
    return normalized


def _normalize_extra(payload: Optional[dict]) -> Dict[str, Any]:
    return dict(payload or {})


def _empty_payload(project_id: int) -> dict:
    return {
        "project_id": int(project_id),
        "test_model_dims": {"x": None, "y": None, "z": None},
        "fem_model_dims": {"x": None, "y": None, "z": None},
        "coefficients": {},
        "extra_json": {},
    }


def _build_payload(row: Optional[dict], project_id: int) -> dict:
    if not row:
        return _empty_payload(project_id)
    if not hasattr(row, "get"):
        row = {
            "pid": row[0] if len(row) > 0 else project_id,
            "test_model_x": row[1] if len(row) > 1 else None,
            "test_model_y": row[2] if len(row) > 2 else None,
            "test_model_z": row[3] if len(row) > 3 else None,
            "fem_model_x": row[4] if len(row) > 4 else None,
            "fem_model_y": row[5] if len(row) > 5 else None,
            "fem_model_z": row[6] if len(row) > 6 else None,
            "coefficients_json": row[7] if len(row) > 7 else None,
            "extra_json": row[8] if len(row) > 8 else None,
        }
    return {
        "project_id": int(project_id),
        "test_model_dims": {
            "x": row.get("test_model_x"),
            "y": row.get("test_model_y"),
            "z": row.get("test_model_z"),
        },
        "fem_model_dims": {
            "x": row.get("fem_model_x"),
            "y": row.get("fem_model_y"),
            "z": row.get("fem_model_z"),
        },
        "coefficients": json.loads(row["coefficients_json"]) if row.get("coefficients_json") else {},
        "extra_json": json.loads(row["extra_json"]) if row.get("extra_json") else {},
    }


def _fetch_project_config(cursor, project_id: int) -> dict:
    cursor.execute(
        """
        SELECT pid, test_model_x, test_model_y, test_model_z,
               fem_model_x, fem_model_y, fem_model_z,
               coefficients_json, extra_json
        FROM t_mt_py_project_config
        WHERE pid = %s
        """,
        (int(project_id),),
    )
    return _build_payload(cursor.fetchone(), int(project_id))


def _upsert_project_config_with_cursor(
    cursor,
    project_id: int,
    *,
    test_model_dims: Optional[dict] = None,
    fem_model_dims: Optional[dict] = None,
    coefficients: Optional[dict] = None,
    extra_json: Optional[dict] = None,
) -> dict:
    existing = _fetch_project_config(cursor, int(project_id))

    resolved_test_dims = dict(existing["test_model_dims"])
    incoming_test_dims = _normalize_dims(test_model_dims)
    if incoming_test_dims is not None:
        resolved_test_dims.update(incoming_test_dims)

    resolved_fem_dims = dict(existing["fem_model_dims"])
    incoming_fem_dims = _normalize_dims(fem_model_dims)
    if incoming_fem_dims is not None:
        resolved_fem_dims.update(incoming_fem_dims)

    resolved_coefficients = dict(existing["coefficients"])
    resolved_coefficients.update(_normalize_mapping(coefficients))

    resolved_extra_json = dict(existing["extra_json"])
    resolved_extra_json.update(_normalize_extra(extra_json))

    cursor.execute(
        """
        INSERT INTO t_mt_py_project_config
        (pid, test_model_x, test_model_y, test_model_z,
         fem_model_x, fem_model_y, fem_model_z,
         coefficients_json, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            test_model_x = VALUES(test_model_x),
            test_model_y = VALUES(test_model_y),
            test_model_z = VALUES(test_model_z),
            fem_model_x = VALUES(fem_model_x),
            fem_model_y = VALUES(fem_model_y),
            fem_model_z = VALUES(fem_model_z),
            coefficients_json = VALUES(coefficients_json),
            extra_json = VALUES(extra_json),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            int(project_id),
            resolved_test_dims["x"],
            resolved_test_dims["y"],
            resolved_test_dims["z"],
            resolved_fem_dims["x"],
            resolved_fem_dims["y"],
            resolved_fem_dims["z"],
            json.dumps(resolved_coefficients, ensure_ascii=False),
            json.dumps(resolved_extra_json, ensure_ascii=False),
        ),
    )

    return {
        "project_id": int(project_id),
        "test_model_dims": resolved_test_dims,
        "fem_model_dims": resolved_fem_dims,
        "coefficients": resolved_coefficients,
        "extra_json": resolved_extra_json,
    }


def get_project_config(project_id: int) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        return _fetch_project_config(cursor, int(project_id))
    finally:
        cursor.close()
        conn.close()


def upsert_project_config(
    project_id: int,
    *,
    test_model_dims: Optional[dict] = None,
    fem_model_dims: Optional[dict] = None,
    coefficients: Optional[dict] = None,
    extra_json: Optional[dict] = None,
    cursor=None,
) -> dict:
    own_conn = None
    own_cursor = cursor
    if own_cursor is None:
        ensure_tables_exist()
        own_conn = get_connection()
        own_cursor = own_conn.cursor(dictionary=True)
    try:
        payload = _upsert_project_config_with_cursor(
            own_cursor,
            int(project_id),
            test_model_dims=test_model_dims,
            fem_model_dims=fem_model_dims,
            coefficients=coefficients,
            extra_json=extra_json,
        )
        if own_conn is not None:
            own_conn.commit()
        return payload
    except Exception:
        if own_conn is not None:
            own_conn.rollback()
        raise
    finally:
        if own_conn is not None:
            own_cursor.close()
            own_conn.close()


def _dims_from_bounds(bbox_min: Sequence[float], bbox_max: Sequence[float]) -> dict:
    lo = np.asarray(list(bbox_min), dtype=np.float64).reshape(3)
    hi = np.asarray(list(bbox_max), dtype=np.float64).reshape(3)
    span = hi - lo
    return {
        "x": float(span[0]),
        "y": float(span[1]),
        "z": float(span[2]),
    }


def _model_size_from_dims(dims: Optional[dict]) -> Optional[float]:
    resolved = _normalize_dims(dims) or {}
    values = [float(v) for v in resolved.values() if v is not None]
    if not values:
        return None
    return float(max(values))


def _load_test_model_dims_from_measuring_points(cursor, project_id: int) -> Optional[dict]:
    cursor.execute(
        """
        SELECT x_position, y_position, z_position
        FROM t_mt_measuring_point_info
        WHERE project_id = %s
        ORDER BY id, measuring_point_name
        """,
        (int(project_id),),
    )
    rows = cursor.fetchall() or []
    if not rows:
        return None
    coords = np.array(
        [[float(row["x_position"]), float(row["y_position"]), float(row["z_position"])] for row in rows],
        dtype=np.float64,
    )
    coords = coords.reshape((-1, 3))
    return _dims_from_bounds(coords.min(axis=0), coords.max(axis=0))


def _resolve_project_config_for_node_match(cursor, project_id: int) -> tuple[dict, bool]:
    config = _fetch_project_config(cursor, int(project_id))
    test_dims = dict(config["test_model_dims"])
    if all(test_dims.get(axis) is not None for axis in ("x", "y", "z")):
        return config, False

    measured_dims = _load_test_model_dims_from_measuring_points(cursor, int(project_id))
    if measured_dims is None:
        return config, False

    updated = _upsert_project_config_with_cursor(
        cursor,
        int(project_id),
        test_model_dims=measured_dims,
    )
    return updated, True


def _build_node_match_parameter_payload(cursor, project_id: int) -> tuple[dict, bool]:
    config, backfilled = _resolve_project_config_for_node_match(cursor, int(project_id))
    test_dims = dict(config["test_model_dims"])
    fem_dims = dict(config["fem_model_dims"])
    test_model = {
        "dims": test_dims,
        "model_size": _model_size_from_dims(test_dims),
    }
    fem_model = {
        "dims": fem_dims,
        "model_size": _model_size_from_dims(fem_dims),
    }

    test_model_size = test_model["model_size"]
    fem_model_size = fem_model["model_size"]
    if test_model_size is None:
        raise ValueError("test model dimensions are not available")
    if fem_model_size is None:
        raise ValueError("fem model dimensions are not available")

    return (
        {
            "project_id": int(project_id),
            "outer_contour_type": "axis_aligned_bbox",
            "test_model": test_model,
            "fem_model": fem_model,
            "tolerance": float(min(test_model_size, fem_model_size) * 1e-6),
            "maximum_node_point_distance": float(max(test_model_size, fem_model_size) * 0.05),
        },
        backfilled,
    )


def get_node_match_parameter_context(project_id: int, *, cursor=None) -> dict:
    own_conn = None
    own_cursor = cursor
    if own_cursor is None:
        ensure_tables_exist()
        own_conn = get_connection()
        own_cursor = own_conn.cursor(dictionary=True)
    try:
        payload, backfilled = _build_node_match_parameter_payload(own_cursor, int(project_id))
        if own_conn is not None and backfilled:
            own_conn.commit()
        return payload
    except Exception:
        if own_conn is not None:
            own_conn.rollback()
        raise
    finally:
        if own_conn is not None:
            own_cursor.close()
            own_conn.close()


def save_fem_model_dimensions(
    project_id: int,
    *,
    bbox_min: Sequence[float],
    bbox_max: Sequence[float],
    cursor=None,
) -> dict:
    dims = _dims_from_bounds(bbox_min, bbox_max)
    return upsert_project_config(
        int(project_id),
        fem_model_dims=dims,
        cursor=cursor,
    )


def save_test_model_dimensions(
    project_id: int,
    *,
    points: Iterable[Sequence[float]],
    cursor=None,
) -> dict:
    coords = np.asarray(list(points), dtype=np.float64)
    if coords.size == 0:
        return get_project_config(int(project_id)) if cursor is None else _empty_payload(int(project_id))
    coords = coords.reshape((-1, 3))
    dims = _dims_from_bounds(coords.min(axis=0), coords.max(axis=0))
    return upsert_project_config(
        int(project_id),
        test_model_dims=dims,
        cursor=cursor,
    )


def get_test_data_mode(project_id: int, *, cursor=None) -> Optional[str]:
    if cursor is not None:
        config = _fetch_project_config(cursor, int(project_id))
    else:
        config = get_project_config(int(project_id))
    value = str((config.get("extra_json") or {}).get("test_data_mode") or "").strip()
    return value or None


def save_test_data_mode(
    project_id: int,
    *,
    test_data_mode: str,
    test_data_source: Optional[str] = None,
    cursor=None,
) -> dict:
    extra = {"test_data_mode": str(test_data_mode).strip()}
    if test_data_source is not None:
        extra["test_data_source"] = str(test_data_source).strip()
    return upsert_project_config(
        int(project_id),
        extra_json=extra,
        cursor=cursor,
    )
