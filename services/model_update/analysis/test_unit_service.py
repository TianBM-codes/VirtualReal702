from db import ensure_tables_exist, get_connection
from src.l3.core.errors import ValidationError

from .project_config_service import (
    DYNAMIC_DISPLACEMENT_DISPLAY_SCALE_KEY,
    STATIC_DISPLACEMENT_DISPLAY_SCALE_KEY,
    get_test_display_scale_factors,
    save_test_model_dimensions,
    upsert_project_config,
)

_SUPPORTED_UNIT_SYSTEMS = {"MKS", "MMKS"}


def _normalize_unit_system(value: str) -> str:
    token = str(value or "").strip().upper()
    if token not in _SUPPORTED_UNIT_SYSTEMS:
        raise ValidationError(
            "unsupported test unit system",
            {"unit_system": value, "allowed": sorted(_SUPPORTED_UNIT_SYSTEMS)},
        )
    return token


def _resolve_unit_scale(from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return 1.0
    if from_unit == "MKS" and to_unit == "MMKS":
        return 1000.0
    if from_unit == "MMKS" and to_unit == "MKS":
        return 0.001
    raise ValidationError(
        "unsupported unit conversion",
        {"from_unit": from_unit, "to_unit": to_unit, "allowed": ["MKS<->MMKS"]},
    )


def _load_test_points_for_dims(cursor, project_id: int) -> list[tuple[float, float, float]]:
    cursor.execute(
        """
        SELECT x, y, z
        FROM t_mt_py_test_node
        WHERE pid = %s
        ORDER BY nid
        """,
        (int(project_id),),
    )
    rows = cursor.fetchall() or []
    if rows:
        return [
            (float(row["x"]), float(row["y"]), float(row["z"]))
            for row in rows
            if row.get("x") is not None and row.get("y") is not None and row.get("z") is not None
        ]

    cursor.execute(
        """
        SELECT x_position, y_position, z_position
        FROM t_mt_measuring_point_info
        WHERE project_id = %s
        ORDER BY id
        """,
        (int(project_id),),
    )
    rows = cursor.fetchall() or []
    return [
        (float(row["x_position"]), float(row["y_position"]), float(row["z_position"]))
        for row in rows
        if row.get("x_position") is not None and row.get("y_position") is not None and row.get("z_position") is not None
    ]


def convert_test_unit_system(*, project_id: int, from_unit: str, to_unit: str) -> dict:
    ensure_tables_exist()
    resolved_from_unit = _normalize_unit_system(from_unit)
    resolved_to_unit = _normalize_unit_system(to_unit)
    scale = _resolve_unit_scale(resolved_from_unit, resolved_to_unit)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        test_node_rows_updated = 0
        measuring_point_rows_updated = 0
        if scale != 1.0:
            cursor.execute(
                """
                UPDATE t_mt_py_test_node
                SET x = x * %s,
                    y = y * %s,
                    z = z * %s,
                    origin_x = origin_x * %s,
                    origin_y = origin_y * %s,
                    origin_z = origin_z * %s
                WHERE pid = %s
                """,
                (scale, scale, scale, scale, scale, scale, int(project_id)),
            )
            test_node_rows_updated = int(getattr(cursor, "rowcount", 0) or 0)

            cursor.execute(
                """
                UPDATE t_mt_measuring_point_info
                SET x_position = x_position * %s,
                    y_position = y_position * %s,
                    z_position = z_position * %s,
                    x_position_ori = x_position_ori * %s,
                    y_position_ori = y_position_ori * %s,
                    z_position_ori = z_position_ori * %s
                WHERE project_id = %s
                """,
                (scale, scale, scale, scale, scale, scale, int(project_id)),
            )
            measuring_point_rows_updated = int(getattr(cursor, "rowcount", 0) or 0)

        scales = get_test_display_scale_factors(int(project_id), cursor=cursor)
        upsert_project_config(
            int(project_id),
            coefficients={
                DYNAMIC_DISPLACEMENT_DISPLAY_SCALE_KEY: float(scales["dynamic"]) * scale,
                STATIC_DISPLACEMENT_DISPLAY_SCALE_KEY: float(scales["static"]) * scale,
            },
            extra_json={"test_unit_system": resolved_to_unit},
            cursor=cursor,
        )

        points = _load_test_points_for_dims(cursor, int(project_id))
        if points:
            project_config = save_test_model_dimensions(
                int(project_id),
                points=points,
                cursor=cursor,
            )
        else:
            project_config = upsert_project_config(
                int(project_id),
                cursor=cursor,
            )

        conn.commit()
        return {
            "project_id": int(project_id),
            "from_unit": resolved_from_unit,
            "to_unit": resolved_to_unit,
            "scale": float(scale),
            "test_node_rows_updated": test_node_rows_updated,
            "measuring_point_rows_updated": measuring_point_rows_updated,
            "display_scales": get_test_display_scale_factors(int(project_id), cursor=cursor),
            "project_config": project_config,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
