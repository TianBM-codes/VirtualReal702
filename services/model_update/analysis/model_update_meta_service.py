import json
import os
from pathlib import Path
from typing import Optional

from db import ensure_tables_exist, get_connection


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _service_config_path() -> Path:
    return _repo_root() / "service_config.json"


def get_abaqus_config() -> dict:
    config_path = _service_config_path()
    if not config_path.exists():
        return {
            "abaqus_cmd": "abaqus",
            "configured": False,
            "source_file": str(config_path),
        }

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    abaqus_cmd = str(payload.get("APP_ABAQUS_CMD") or "abaqus").strip() or "abaqus"
    return {
        "abaqus_cmd": abaqus_cmd,
        "configured": bool(payload.get("APP_ABAQUS_CMD")),
        "source_file": str(config_path),
    }


def add_manual_parameter(
    *,
    project_id: int,
    parameter: str,
    parameter_type: str,
    scatter: float,
    upper: Optional[float] = None,
    lower: Optional[float] = None,
) -> dict:
    parameter_name = str(parameter or "").strip()
    if not parameter_name:
        raise ValueError("parameter is required")

    resolved_type = str(parameter_type or "").strip().upper()
    if not resolved_type:
        raise ValueError("type is required")

    resolved_scatter = float(scatter)
    if resolved_scatter <= 0:
        raise ValueError("scatter must be > 0")

    upper_value = None if upper is None else float(upper)
    lower_value = None if lower is None else float(lower)
    if upper_value is not None and lower_value is not None and lower_value > upper_value:
        raise ValueError("lower must be <= upper")

    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_manual_parameter
            (pid, parameter_name, parameter_type, scatter, upper_bound, lower_bound)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                parameter_type = VALUES(parameter_type),
                scatter = VALUES(scatter),
                upper_bound = VALUES(upper_bound),
                lower_bound = VALUES(lower_bound),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(project_id),
                parameter_name,
                resolved_type,
                resolved_scatter,
                upper_value,
                lower_value,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return {
        "project_id": int(project_id),
        "parameter": parameter_name,
        "type": resolved_type,
        "scatter": resolved_scatter,
        "upper": upper_value,
        "lower": lower_value,
    }


def add_manual_response(
    *,
    project_id: int,
    response_type: str,
    scatter: float,
    dof: str,
    step: Optional[str] = None,
) -> dict:
    resolved_type = str(response_type or "").strip().upper()
    if not resolved_type:
        raise ValueError("type is required")

    resolved_dof = str(dof or "").strip().upper()
    if not resolved_dof:
        raise ValueError("dof is required")

    step_name = str(step).strip() if step is not None else ""

    resolved_scatter = float(scatter)
    if resolved_scatter <= 0:
        raise ValueError("scatter must be > 0")

    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_manual_response
            (pid, response_type, step_name, dof, scatter)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                scatter = VALUES(scatter),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(project_id),
                resolved_type,
                step_name,
                resolved_dof,
                resolved_scatter,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return {
        "project_id": int(project_id),
        "type": resolved_type,
        "step": step_name or None,
        "dof": resolved_dof,
        "scatter": resolved_scatter,
    }
