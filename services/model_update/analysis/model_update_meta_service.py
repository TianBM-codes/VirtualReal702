from pathlib import Path
from typing import Optional
from config import _load_service_config

from db import ensure_tables_exist, get_connection


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _service_config_path() -> Path:
    return _repo_root() / "service_config.json"


def resolve_abaqus_command(abaqus: Optional[str] = None) -> str:
    explicit = str(abaqus or "").strip()
    if explicit:
        return explicit

    payload = _load_service_config()
    return str(payload.get("APP_ABAQUS_CMD") or "abaqus").strip() or "abaqus"


def resolve_nastran_command(nastran: Optional[str] = None) -> str:
    explicit = str(nastran or "").strip()
    if explicit:
        return explicit

    payload = _load_service_config()
    return str(payload.get("NASTRAN") or "nastran").strip() or "nastran"


def resolve_python3_command(python3: Optional[str] = None) -> Optional[str]:
    explicit = str(python3 or "").strip()
    if explicit:
        return explicit

    payload = _load_service_config()
    resolved = str(payload.get("APP_PYTHON3_CMD") or "").strip()
    return resolved or None


def resolve_bayesian_output_dir(output_dir: Optional[str] = None) -> Optional[str]:
    explicit = str(output_dir or "").strip()
    if explicit:
        return explicit

    payload = _load_service_config()
    resolved = str(payload.get("APP_BAYESIAN_OUTPUT_DIR") or "").strip()
    return resolved or None


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
        raise ValueError("parameter 不能为空")

    resolved_type = str(parameter_type or "").strip().upper()
    if not resolved_type:
        raise ValueError("type 不能为空")

    resolved_scatter = float(scatter)
    if resolved_scatter <= 0:
        raise ValueError("scatter 必须大于 0")

    upper_value = None if upper is None else float(upper)
    lower_value = None if lower is None else float(lower)
    if upper_value is not None and lower_value is not None and lower_value > upper_value:
        raise ValueError("lower 必须小于或等于 upper")

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
        raise ValueError("type 不能为空")

    resolved_dof = str(dof or "").strip().upper()
    if not resolved_dof:
        raise ValueError("dof 不能为空")

    step_name = str(step).strip() if step is not None else ""

    resolved_scatter = float(scatter)
    if resolved_scatter <= 0:
        raise ValueError("scatter 必须大于 0")

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
