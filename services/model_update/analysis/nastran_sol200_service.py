import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from pyNastran.bdf.bdf import BDF
from db import ensure_tables_exist, get_connection
from src.l3.core.errors import ValidationError

from ..importers.op2_service import (
    _build_op2_parameter_columns_with_mappings,
    _register_external_sensitivity_result_group,
    _write_element_cloud_result_to_workspace,
    export_sensitivity_to_vtu,
    preview_op2_sensitivity,
    store_op2_sensitivity_cloud,
    store_op2_sensitivity,
)
from .solver_service import (
    generate_nastran_sol200_job,
    preview_nastran_sol200_job,
    run_nastran_sol200_job,
)
from .console_log_service import safe_write_console_event
from .modal_mac_service import (
    build_modal_mac_matrix_from_displacement_sensitivity,
    compute_project_modal_mac,
    compute_project_modal_mac_from_fem_mode_map,
    expand_modal_mac_responses_to_sol200_displacements,
)
from .project_status_service import update_work_condition_project_status


# This module is the dedicated orchestration layer for the phase-1 Nastran
# SOL200 workflow. The lower layers still own card generation, solver launch,
# and matrix parsing; this service keeps the public SOL200 path centralized so
# future phase-2/3 work can extend one place instead of scattering logic.

DEFAULT_PARAMETER_LOWER_SCALE = 0.01
DEFAULT_PARAMETER_UPPER_SCALE = 1.0e6
_WORKFLOW_STATUS_RUNNING = 0
_WORKFLOW_STATUS_DONE = 1
_WORKFLOW_STATUS_FAILED = 2


def _set_project_sensitivity_status(project_id: int, status: int) -> None:
    update_work_condition_project_status(
        int(project_id),
        sensitivity_status=int(status),
    )


def _log_project_workflow_failure(project_id: int, title: str, exc: Exception, lines: Optional[Sequence[object]] = None) -> None:
    detail_lines = [str(item) for item in list(lines or []) if str(item or "").strip()]
    detail_lines.append(f"错误: {str(exc)}")
    safe_write_console_event(int(project_id), title, detail_lines)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _scope_contains(scope_value, expected: str) -> bool:
    token = str(expected or "").strip().upper()
    if not token:
        return False
    raw = scope_value
    if raw in (None, ""):
        raw = ["SENSITIVITY", "UPDATE", "SOL200", "BAYESIAN", "DSA"]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, list) else [raw]
        except Exception:
            raw = [part.strip() for part in raw.split(",")]
    values = list(raw or [])
    return token in {str(item or "").strip().upper() for item in values}


def _normalize_sol200_parameter_type(value: Any) -> str:
    token = str(value or "").strip().upper()
    mapping = {
        "T": "H",
        "H": "H",
        "E": "E",
        "RHO": "RHO",
    }
    resolved = mapping.get(token)
    if not resolved:
        raise ValidationError(
            "unsupported SOL200 parameter type",
            {"parameter_type": value, "allowed": ["E", "RHO", "H", "T"]},
        )
    return resolved


def _normalize_sol200_response_type(value: Any) -> str:
    token = str(value or "").strip().upper()
    mapping = {
        "FREQ": "FREQ",
        "MODAL_FREQUENCY": "FREQ",
        "DISP": "DISP",
        "MODAL_DISPLACEMENT": "DISP",
        "NODAL_DISPLACEMENT": "DISP",
        "MODAL_MAC": "MODAL_MAC",
    }
    resolved = mapping.get(token)
    if not resolved:
        raise ValidationError(
            "unsupported SOL200 response type",
            {"response_type": value, "allowed": ["FREQ", "MODAL_FREQUENCY", "DISP", "MODAL_DISPLACEMENT", "NODAL_DISPLACEMENT", "MODAL_MAC"]},
        )
    return resolved


def _normalize_modal_response_category(value: Any) -> Optional[str]:
    token = str(value or "").strip().upper()
    if not token:
        return None
    if token in {"FREQ", "MODAL_FREQUENCY"}:
        return "MODAL_FREQUENCY"
    if token == "MODAL_MAC":
        return "MODAL_MAC"
    raise ValidationError(
        "unsupported modal response category",
        {"response_category": value, "allowed": ["MODAL_FREQUENCY", "MODAL_MAC"]},
    )


def _response_matches_modal_category(response_row: Dict[str, Any], response_category: Optional[str]) -> bool:
    normalized = _normalize_modal_response_category(response_category)
    if normalized is None:
        return True
    response_type = str(response_row.get("response_type") or response_row.get("type") or "").strip().upper()
    if normalized == "MODAL_FREQUENCY":
        return response_type in {"FREQ", "MODAL_FREQUENCY"}
    return response_type == "MODAL_MAC"


def create_sol200_parameter_config_entry(
    *,
    project_id: int,
    parameter_name: str,
    parameter_type: str,
    initial: float,
    lower: Optional[float] = None,
    upper: Optional[float] = None,
    property_id: Optional[int] = None,
    material_id: Optional[int] = None,
    element_id: Optional[int] = None,
    extra_json: Optional[Dict[str, Any]] = None,
) -> dict:
    ensure_tables_exist()
    resolved_name = str(parameter_name or "").strip()
    if not resolved_name:
        raise ValidationError("parameter_name is required", {"parameter_name": parameter_name})
    resolved_type = _normalize_sol200_parameter_type(parameter_type)
    resolved_initial = float(initial)
    resolved_lower = resolved_initial if lower is None else float(lower)
    resolved_upper = resolved_initial if upper is None else float(upper)
    if resolved_lower > resolved_upper:
        raise ValidationError(
            "lower must be less than or equal to upper",
            {"lower": resolved_lower, "upper": resolved_upper},
        )

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT COALESCE(MAX(parameter_no), 0) AS max_no
            FROM t_mt_py_fem_sol200_parameter_config
            WHERE pid = %s
            """,
            (int(project_id),),
        )
        parameter_no = int((cursor.fetchone() or {}).get("max_no") or 0) + 1
        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_sol200_parameter_config
            (pid, parameter_no, parameter_name, parameter_type, property_id, material_id, element_id,
             initial_value, lower_bound, upper_bound, extra_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(project_id),
                int(parameter_no),
                resolved_name,
                resolved_type,
                int(property_id) if property_id is not None else None,
                int(material_id) if material_id is not None else None,
                int(element_id) if element_id is not None else None,
                resolved_initial,
                resolved_lower,
                resolved_upper,
                _json_dumps(dict(extra_json or {})),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    safe_write_console_event(
        int(project_id),
        "SOL200 参数配置创建完成",
        [
            f"参数名: {resolved_name}",
            f"参数类型: {resolved_type}",
            f"初始值: {resolved_initial}",
            f"范围: [{resolved_lower}, {resolved_upper}]",
        ],
    )
    return {
        "project_id": int(project_id),
        "parameter_no": int(parameter_no),
        "parameter_name": resolved_name,
        "parameter_type": resolved_type,
        "property_id": int(property_id) if property_id is not None else None,
        "material_id": int(material_id) if material_id is not None else None,
        "element_id": int(element_id) if element_id is not None else None,
        "initial": resolved_initial,
        "lower": resolved_lower,
        "upper": resolved_upper,
        "extra_json": dict(extra_json or {}),
    }


def list_sol200_parameter_config_entries(project_id: int) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT parameter_no, parameter_name, parameter_type, property_id, material_id, element_id,
                   initial_value, lower_bound, upper_bound, extra_json
            FROM t_mt_py_fem_sol200_parameter_config
            WHERE pid = %s
            ORDER BY parameter_no ASC
            """,
            (int(project_id),),
        )
        rows = []
        for row in cursor.fetchall() or []:
            extra_json = row.get("extra_json")
            if isinstance(extra_json, str):
                try:
                    extra_json = json.loads(extra_json)
                except Exception:
                    extra_json = {}
            rows.append({
                "parameter_no": int(row["parameter_no"]),
                "parameter_name": str(row["parameter_name"]),
                "parameter_type": str(row["parameter_type"]),
                "property_id": int(row["property_id"]) if row.get("property_id") is not None else None,
                "material_id": int(row["material_id"]) if row.get("material_id") is not None else None,
                "element_id": int(row["element_id"]) if row.get("element_id") is not None else None,
                "initial": float(row["initial_value"]),
                "lower": float(row["lower_bound"]) if row.get("lower_bound") is not None else None,
                "upper": float(row["upper_bound"]) if row.get("upper_bound") is not None else None,
                "extra_json": extra_json or {},
            })
        return {
            "project_id": int(project_id),
            "parameter_count": len(rows),
            "parameters": rows,
        }
    finally:
        cursor.close()
        conn.close()


def clear_sol200_parameter_config_entries(project_id: int) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = %s",
            (int(project_id),),
        )
        deleted_count = int(cursor.rowcount or 0)
        conn.commit()
        return {
            "project_id": int(project_id),
            "deleted_count": deleted_count,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_sol200_response_config_entry(
    *,
    project_id: int,
    response_name: str,
    response_type: str,
    mode_number: Optional[int] = None,
    node_id: Optional[int] = None,
    component: Optional[str] = None,
    extra_json: Optional[Dict[str, Any]] = None,
) -> dict:
    ensure_tables_exist()
    resolved_name = str(response_name or "").strip()
    if not resolved_name:
        raise ValidationError("response_name is required", {"response_name": response_name})
    resolved_type = _normalize_sol200_response_type(response_type)
    resolved_mode_number = int(mode_number) if mode_number is not None else None
    resolved_extra = dict(extra_json or {})
    if node_id is None:
        node_id = resolved_extra.get("node_id", resolved_extra.get("fem_node_label"))
    if component is None:
        component = resolved_extra.get("component", resolved_extra.get("dof"))
    resolved_node_id = int(node_id) if node_id is not None else None
    resolved_component = str(component).strip().upper() if component is not None else None
    if resolved_type == "FREQ" and resolved_mode_number is None:
        raise ValidationError(
            "mode_number is required for SOL200 FREQ response",
            {"response_type": resolved_type, "mode_number": mode_number},
        )
    if resolved_type == "MODAL_MAC" and resolved_mode_number is None:
        raise ValidationError(
            "mode_number is required for SOL200 MODAL_MAC response",
            {"response_type": resolved_type, "mode_number": mode_number},
        )
    if resolved_type == "DISP":
        missing = []
        if resolved_mode_number is None:
            missing.append("mode_number")
        if resolved_node_id is None:
            missing.append("node_id")
        if not resolved_component:
            missing.append("component")
        if missing:
            raise ValidationError(
                "mode_number, node_id and component are required for SOL200 DISP response",
                {"missing_fields": missing, "response_type": resolved_type},
            )
        resolved_extra["node_id"] = resolved_node_id
        resolved_extra["component"] = resolved_component

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT COALESCE(MAX(response_no), 0) AS max_no
            FROM t_mt_py_fem_sol200_response_config
            WHERE pid = %s
            """,
            (int(project_id),),
        )
        response_no = int((cursor.fetchone() or {}).get("max_no") or 0) + 1
        cursor.execute(
            """
            INSERT INTO t_mt_py_fem_sol200_response_config
            (pid, response_no, response_name, response_type, mode_number, extra_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                int(project_id),
                int(response_no),
                resolved_name,
                resolved_type,
                resolved_mode_number,
                _json_dumps(resolved_extra),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    safe_write_console_event(
        int(project_id),
        "SOL200 响应配置创建完成",
        [
            f"响应名: {resolved_name}",
            f"响应类型: {resolved_type}",
            f"模态阶次: {resolved_mode_number if resolved_mode_number is not None else '-'}",
        ],
    )
    return {
        "project_id": int(project_id),
        "response_no": int(response_no),
        "response_name": resolved_name,
        "response_type": resolved_type,
        "mode_number": resolved_mode_number,
        "node_id": resolved_node_id,
        "component": resolved_component,
        "extra_json": resolved_extra,
    }


def list_sol200_response_config_entries(project_id: int) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT response_no, response_name, response_type, mode_number, extra_json
            FROM t_mt_py_fem_sol200_response_config
            WHERE pid = %s
            ORDER BY response_no ASC
            """,
            (int(project_id),),
        )
        rows = []
        for row in cursor.fetchall() or []:
            extra_json = row.get("extra_json")
            if isinstance(extra_json, str):
                try:
                    extra_json = json.loads(extra_json)
                except Exception:
                    extra_json = {}
            rows.append({
                "response_no": int(row["response_no"]),
                "response_name": str(row["response_name"]),
                "response_type": str(row["response_type"]),
                "mode_number": int(row["mode_number"]) if row.get("mode_number") is not None else None,
                "node_id": int(extra_json["node_id"]) if isinstance(extra_json, dict) and extra_json.get("node_id") is not None else None,
                "component": str(extra_json["component"]) if isinstance(extra_json, dict) and extra_json.get("component") is not None else None,
                "extra_json": extra_json or {},
            })
        return {
            "project_id": int(project_id),
            "response_count": len(rows),
            "responses": rows,
        }
    finally:
        cursor.close()
        conn.close()


def clear_sol200_response_config_entries(project_id: int) -> dict:
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = %s",
            (int(project_id),),
        )
        deleted_count = int(cursor.rowcount or 0)
        conn.commit()
        return {
            "project_id": int(project_id),
            "deleted_count": deleted_count,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _normalize_sync_mac_threshold(mac_threshold: Optional[float]) -> Optional[float]:
    if mac_threshold is None:
        return None
    resolved = float(mac_threshold)
    if resolved < 0.0:
        raise ValidationError("mac_threshold must be >= 0", {"mac_threshold": mac_threshold})
    if resolved <= 1.0:
        return resolved * 100.0
    if resolved <= 100.0:
        return resolved
    raise ValidationError("mac_threshold must be <= 100", {"mac_threshold": mac_threshold})


def _extract_numeric_suffix(text: Any, prefixes: Sequence[str]) -> Optional[int]:
    token = str(text or "").strip().upper()
    for prefix in prefixes:
        normalized_prefix = str(prefix).strip().upper()
        if token.startswith(normalized_prefix):
            suffix = token[len(normalized_prefix):].strip("_:-")
            if suffix.isdigit():
                return int(suffix)
    return None


def _infer_material_id(parameter_row: Dict[str, Any]) -> Optional[int]:
    extra = dict(parameter_row.get("extra_json") or {})
    for key in ("material_id", "source_material_id", "mid"):
        value = extra.get(key)
        if value is not None:
            return int(value)
    return _extract_numeric_suffix(parameter_row.get("set_name"), ("MAT1_", "MAT_", "MID_"))


def _infer_property_id(parameter_row: Dict[str, Any]) -> Optional[int]:
    extra = dict(parameter_row.get("extra_json") or {})
    for key in ("property_id", "source_property_id", "pid"):
        value = extra.get(key)
        if value is not None:
            return int(value)
    return _extract_numeric_suffix(parameter_row.get("set_name"), ("PROP_", "PID_", "PSHELL_"))


def _map_selected_parameter_to_sol200(parameter_row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    quantity_code = _normalize_sol200_parameter_type(parameter_row.get("quantity_code"))
    current_value = parameter_row.get("current_value")
    if current_value is None:
        return None, "current_value_missing"
    payload = {
        "name": str(parameter_row.get("parameter_name") or "").strip(),
        "type": quantity_code,
        "initial": float(current_value),
        "lower": parameter_row.get("lower"),
        "upper": parameter_row.get("upper"),
    }
    if quantity_code in {"E", "RHO"}:
        material_id = _infer_material_id(parameter_row)
        if material_id is None:
            return None, "material_id_unresolved"
        payload["material_id"] = int(material_id)
    elif quantity_code == "H":
        property_id = _infer_property_id(parameter_row)
        if property_id is None:
            return None, "property_id_unresolved"
        payload["property_id"] = int(property_id)
    return payload, None


def _map_catalog_response_to_sol200(response_row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    response_type = str(response_row.get("response_type") or "").strip().upper()
    extra = dict(response_row.get("extra_json") or {})
    if response_type in {"FREQ", "MODAL_FREQUENCY"}:
        mode_number = extra.get("mode_number")
        if mode_number is None:
            mode_number = extra.get("fem_mode_no")
        if mode_number is None:
            mode_number = response_row.get("test_mode_no")
        if mode_number is None:
            return None, "mode_number_missing"
        return {
            "name": str(response_row.get("response_name") or f"FREQ_MODE_{int(mode_number)}").strip(),
            "type": "FREQ",
            "mode_number": int(mode_number),
            "extra_json": extra,
        }, None

    if response_type in {"DISP", "MODAL_DISPLACEMENT", "NODAL_DISPLACEMENT"}:
        mode_number = extra.get("mode_number")
        if mode_number is None:
            mode_number = extra.get("fem_mode_no")
        if mode_number is None:
            mode_number = response_row.get("test_mode_no")
        node_id = (
            response_row.get("fem_node_label")
            or extra.get("node_id")
            or extra.get("fem_node_label")
        )
        component = response_row.get("component") or extra.get("component") or extra.get("dof")
        if mode_number is None:
            return None, "mode_number_missing"
        if node_id is None:
            return None, "node_id_missing"
        if component is None:
            return None, "component_missing"
        return {
            "name": str(
                response_row.get("response_name")
                or f"DISP_MODE_{int(mode_number)}_N{int(node_id)}_{str(component).strip().upper()}"
            ).strip(),
            "type": "DISP",
            "mode_number": int(mode_number),
            "node_id": int(node_id),
            "component": str(component).strip().upper(),
            "extra_json": extra,
        }, None

    if response_type == "MODAL_MAC":
        mode_number = extra.get("mode_number")
        if mode_number is None:
            mode_number = extra.get("fem_mode_no")
        if mode_number is None:
            return None, "mode_number_missing"
        return {
            "name": str(response_row.get("response_name") or f"MAC_MODE_{int(mode_number)}").strip(),
            "type": "MODAL_MAC",
            "mode_number": int(mode_number),
            "extra_json": extra,
        }, None

    return None, "response_is_not_supported_sol200_response"


def sync_sol200_config_from_catalog(
    *,
    project_id: int,
    overwrite: bool = True,
    parameter_source: str = "selected_parameter",
    response_source: str = "response_catalog",
    response_category: Optional[str] = None,
    mac_threshold: Optional[float] = None,
    max_freq_error_ratio: Optional[float] = 0.2,
    matching_method: str = "greedy",
) -> dict:
    ensure_tables_exist()
    resolved_parameter_source = str(parameter_source or "selected_parameter").strip().lower()
    resolved_response_source = str(response_source or "response_catalog").strip().lower()
    if resolved_parameter_source != "selected_parameter":
        raise ValidationError(
            "unsupported parameter_source",
            {"parameter_source": parameter_source, "allowed": ["selected_parameter"]},
        )
    if resolved_response_source not in {"response_catalog", "modal_match"}:
        raise ValidationError(
            "unsupported response_source",
            {"response_source": response_source, "allowed": ["response_catalog", "modal_match"]},
        )

    from . import inp_service as _inp

    parameter_payload = _inp.list_optimization_parameters(int(project_id))
    parameter_rows = [
        row for row in list(parameter_payload.get("parameters") or [])
        if _scope_contains(row.get("usage_scope"), "UPDATE")
    ]

    if resolved_response_source == "modal_match":
        _inp.create_modal_frequency_response_catalog_from_match(
            int(project_id),
            overwrite=True,
            mac_threshold=_normalize_sync_mac_threshold(mac_threshold),
            max_freq_error_ratio=max_freq_error_ratio,
            matching_method=str(matching_method or "greedy"),
        )
    response_payload = _inp.get_fe_response_catalog(int(project_id))
    response_rows = [
        row for row in list(response_payload.get("responses") or [])
        if bool(row.get("enabled", True))
        and _scope_contains(row.get("solver_scope"), "SOL200")
        and _response_matches_modal_category(row, response_category)
    ]

    mapped_parameters: List[Dict[str, Any]] = []
    skipped_parameters: List[dict] = []
    for row in parameter_rows:
        mapped, reason = _map_selected_parameter_to_sol200(row)
        if mapped is None:
            skipped_parameters.append({
                "parameter_name": row.get("parameter_name"),
                "quantity_code": row.get("quantity_code"),
                "reason": reason,
            })
            continue
        mapped_parameters.append(mapped)

    mapped_responses: List[Dict[str, Any]] = []
    skipped_responses: List[dict] = []
    for row in response_rows:
        mapped, reason = _map_catalog_response_to_sol200(row)
        if mapped is None:
            skipped_responses.append({
                "response_name": row.get("response_name"),
                "response_type": row.get("response_type"),
                "reason": reason,
            })
            continue
        mapped_responses.append(mapped)

    if not mapped_parameters:
        raise ValidationError(
            "no SOL200-compatible parameters could be resolved from selected_parameter",
            {"project_id": int(project_id), "skipped_preview": skipped_parameters[:20]},
        )
    if not mapped_responses:
        raise ValidationError(
            "no SOL200-compatible responses could be resolved from response catalog",
            {"project_id": int(project_id), "skipped_preview": skipped_responses[:20]},
        )

    if overwrite:
        clear_sol200_parameter_config_entries(int(project_id))
        clear_sol200_response_config_entries(int(project_id))

    created_parameters = []
    for item in mapped_parameters:
        created_parameters.append(
            create_sol200_parameter_config_entry(
                project_id=int(project_id),
                parameter_name=item["name"],
                parameter_type=item["type"],
                initial=float(item["initial"]),
                lower=item.get("lower"),
                upper=item.get("upper"),
                property_id=item.get("property_id"),
                material_id=item.get("material_id"),
                element_id=item.get("element_id"),
                extra_json={
                    "source_table": "t_mt_py_fem_selected_parameter",
                    **({k: v for k, v in item.items() if k not in {"name", "type", "initial", "lower", "upper"}}),
                },
            )
        )

    created_responses = []
    for item in mapped_responses:
        created_responses.append(
            create_sol200_response_config_entry(
                project_id=int(project_id),
                response_name=item["name"],
                response_type=item["type"],
                mode_number=item.get("mode_number"),
                node_id=item.get("node_id"),
                component=item.get("component"),
                extra_json={
                    "source_table": "t_mt_py_fem_dynamic_response_catalog",
                    **dict(item.get("extra_json") or {}),
                },
            )
        )

    return {
        "project_id": int(project_id),
        "overwrite": bool(overwrite),
        "parameter_source": resolved_parameter_source,
        "response_source": resolved_response_source,
        "response_category": _normalize_modal_response_category(response_category),
        "mac_threshold": _normalize_sync_mac_threshold(mac_threshold),
        "max_freq_error_ratio": None if max_freq_error_ratio is None else float(max_freq_error_ratio),
        "matching_method": str(matching_method or "greedy"),
        "parameter_count": len(created_parameters),
        "response_count": len(created_responses),
        "parameters_preview": created_parameters[:20],
        "responses_preview": created_responses[:20],
        "skipped_parameters_preview": skipped_parameters[:20],
        "skipped_responses_preview": skipped_responses[:20],
    }


def sync_generate_run_and_store_sol200_workflow(
    *,
    project_id: int,
    batch_no: str = "1",
    case_name: str = "nastran_sol200",
    input_bdf: str,
    output_bdf: Optional[str] = None,
    overwrite: bool = True,
    response_source: str = "response_catalog",
    response_category: Optional[str] = None,
    mac_threshold: Optional[float] = None,
    max_freq_error_ratio: Optional[float] = 0.2,
    matching_method: str = "greedy",
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    write_cloud_result: bool = True,
    cloud_result_group: Optional[str] = None,
    cloud_step_name: str = "Sensitivity",
    cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    try:
        resolved_settings = dict(settings or {})
        sync_payload = sync_sol200_config_from_catalog(
            project_id=int(project_id),
            overwrite=bool(overwrite),
            parameter_source="selected_parameter",
            response_source=str(response_source or "response_catalog"),
            response_category=response_category,
            mac_threshold=mac_threshold,
            max_freq_error_ratio=max_freq_error_ratio,
            matching_method=str(matching_method or "greedy"),
        )
        generate_payload = generate_sol200_workflow(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            input_bdf=input_bdf,
            output_bdf=output_bdf,
            settings=resolved_settings,
        )
        run_payload = run_sol200_and_store_workflow(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            input_bdf=input_bdf,
            output_bdf=output_bdf,
            settings=resolved_settings,
            nastran=nastran,
            run_solver=run_solver,
            timeout_sec=timeout_sec,
            extra_args=list(extra_args or []),
            write_cloud_result=write_cloud_result,
            cloud_result_group=cloud_result_group,
            cloud_step_name=cloud_step_name,
            cloud_field_name=cloud_field_name,
        )
        resolved_output_bdf = str(
            Path(
                str(
                    output_bdf
                    or generate_payload.get("output_bdf")
                    or run_payload.get("output_bdf")
                    or ""
                )
            ).expanduser().resolve()
        )
        return {
            "workflow": "nastran_sol200_sync_generate_run_and_store",
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "case_name": str(case_name),
            "input_bdf": str(Path(input_bdf).expanduser().resolve()),
            "output_bdf": resolved_output_bdf,
            "response_source": str(response_source or "response_catalog"),
            "response_category": _normalize_modal_response_category(response_category),
            "sync_config": sync_payload,
            "generate": generate_payload,
            "run_and_store": run_payload,
        }
    except Exception as exc:
        try:
            _set_project_sensitivity_status(project_id, _WORKFLOW_STATUS_FAILED)
        except Exception:
            pass
        _log_project_workflow_failure(
            project_id,
            "SOL200 sync-generate-run workflow failed",
            exc,
            [
                f"batch_no: {str(batch_no)}",
                f"case_name: {str(case_name)}",
                f"input_bdf: {str(input_bdf)}",
                f"response_source: {str(response_source or 'response_catalog')}",
            ],
        )
        raise


def _load_project_sol200_parameters(project_id: int) -> List[Dict[str, Any]]:
    payload = list_sol200_parameter_config_entries(project_id)
    return [
        {
            "name": item["parameter_name"],
            "type": item["parameter_type"],
            "property_id": item.get("property_id"),
            "material_id": item.get("material_id"),
            "element_id": item.get("element_id"),
            "initial": item["initial"],
            "lower": item.get("lower"),
            "upper": item.get("upper"),
        }
        for item in list(payload.get("parameters") or [])
    ]


def _load_project_sol200_responses(project_id: int) -> List[Dict[str, Any]]:
    payload = list_sol200_response_config_entries(project_id)
    rows = []
    for item in list(payload.get("responses") or []):
        extra = dict(item.get("extra_json") or {})
        rows.append({
            "name": item["response_name"],
            "type": item["response_type"],
            "mode_number": item.get("mode_number"),
            "node_id": item.get("node_id") or extra.get("node_id") or extra.get("fem_node_label"),
            "component": item.get("component") or extra.get("component") or extra.get("dof"),
            "extra_json": extra,
        })
    return rows


def _load_project_modal_mac_catalog_rows(project_id: int) -> List[Dict[str, Any]]:
    from . import inp_service as _inp

    payload = _inp.get_fe_response_catalog(int(project_id))
    rows = []
    for item in list(payload.get("responses") or []):
        response_type = str(item.get("response_type") or "").strip().upper()
        if response_type != "MODAL_MAC":
            continue
        if not bool(item.get("enabled", True)):
            continue
        rows.append(dict(item))
    return rows


def _sol200_response_metadata_rows(responses: Sequence[Dict[str, Any]]) -> List[dict]:
    rows = []
    for item in list(responses or []):
        extra = dict(item.get("extra_json") or {})
        node_id = item.get("node_id")
        if node_id is None:
            node_id = extra.get("node_id", extra.get("fem_node_label"))
        component = item.get("component")
        if component is None:
            component = extra.get("component", extra.get("dof"))
        rows.append({
            "response_name": item.get("name"),
            "response_type": item.get("type"),
            "mode_number": item.get("mode_number"),
            "node_id": int(node_id) if node_id is not None else None,
            "component": str(component).strip().upper() if component is not None else None,
            "unit": extra.get("unit"),
        })
    return rows


def _resolve_sol200_config_sources(
    *,
    project_id: Optional[int],
    parameters: Optional[List[Dict[str, Any]]],
    parameter_preset: Optional[Dict[str, Any]],
    responses: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    resolved_parameters = [dict(item) for item in (parameters or [])]
    resolved_responses = [dict(item) for item in (responses or [])]
    if project_id is not None and not resolved_parameters and not parameter_preset:
        resolved_parameters = _load_project_sol200_parameters(int(project_id))
    if project_id is not None and not resolved_responses:
        resolved_responses = _load_project_sol200_responses(int(project_id))
    return resolved_parameters, resolved_responses


def _load_bdf_model(input_bdf: str) -> BDF:
    model = BDF(debug=False)
    model.read_bdf(input_bdf, xref=True)
    return model


def _property_material_id(prop: Any) -> Optional[int]:
    for name in ("mid", "mid1"):
        value = getattr(prop, name, None)
        if hasattr(value, "mid"):
            return int(value.mid)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _element_material_id(element: Any, model: BDF) -> Optional[int]:
    prop = getattr(element, "pid_ref", None)
    if prop is None:
        pid = getattr(element, "pid", None)
        if pid is None:
            return None
        prop = model.properties.get(int(pid))
    if prop is None:
        return None
    return _property_material_id(prop)


def _material_scalar(material: Any, field: str) -> Optional[float]:
    candidate_names = {
        "E": ("e", "E"),
        "RHO": ("rho", "Rho"),
    }.get(str(field).upper(), ())
    for name in candidate_names:
        value = getattr(material, name, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _property_thickness_scalar(prop: Any) -> Optional[float]:
    ptype = str(getattr(prop, "type", "")).upper()
    if ptype == "PSHELL":
        value = getattr(prop, "t", None)
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None
    return None


def _build_all_used_material_e_rho_parameters(
    *,
    input_bdf: str,
    preset: Dict[str, Any],
) -> List[Dict[str, Any]]:
    model = _load_bdf_model(input_bdf)
    lower_scale = float(preset.get("lower_scale", DEFAULT_PARAMETER_LOWER_SCALE))
    upper_scale = float(preset.get("upper_scale", DEFAULT_PARAMETER_UPPER_SCALE))
    include_e = bool(preset.get("include_e", True))
    include_rho = bool(preset.get("include_rho", True))
    if not include_e and not include_rho:
        raise ValidationError(
            "parameter_preset must enable at least one of include_e/include_rho",
            {"parameter_preset": preset},
        )

    used_material_ids = sorted({
        int(mid)
        for element in model.elements.values()
        for mid in [_element_material_id(element, model)]
        if mid is not None
    })
    if not used_material_ids:
        raise ValidationError(
            "no used materials were resolved from input_bdf",
            {"input_bdf": input_bdf},
        )

    parameters: List[Dict[str, Any]] = []
    skipped: List[dict] = []
    for mid in used_material_ids:
        material = model.materials.get(int(mid))
        if material is None or str(getattr(material, "type", "")).upper() != "MAT1":
            skipped.append({"material_id": int(mid), "reason": "only MAT1 is supported in phase 1"})
            continue
        if include_e:
            e_value = _material_scalar(material, "E")
            if e_value is None:
                skipped.append({"material_id": int(mid), "reason": "MAT1.E is missing"})
            else:
                parameters.append({
                    "name": f"E{int(mid)}",
                    "type": "E",
                    "material_id": int(mid),
                    "initial": float(e_value),
                    "lower": float(e_value * lower_scale),
                    "upper": float(e_value * upper_scale),
                })
        if include_rho:
            rho_value = _material_scalar(material, "RHO")
            if rho_value is None:
                skipped.append({"material_id": int(mid), "reason": "MAT1.RHO is missing"})
            else:
                parameters.append({
                    "name": f"R{int(mid)}",
                    "type": "RHO",
                    "material_id": int(mid),
                    "initial": float(rho_value),
                    "lower": float(rho_value * lower_scale),
                    "upper": float(rho_value * upper_scale),
                })
    if not parameters:
        raise ValidationError(
            "parameter_preset did not produce any supported parameters",
            {"input_bdf": input_bdf, "parameter_preset": preset, "skipped": skipped[:20]},
        )
    return parameters


def _clone_property_with_material(prop: Any, *, new_pid: int, new_mid: int) -> Any:
    cloned = copy.deepcopy(prop)
    if hasattr(cloned, "pid"):
        cloned.pid = int(new_pid)
    if hasattr(cloned, "mid"):
        cloned.mid = int(new_mid)
        return cloned
    if hasattr(cloned, "mid1"):
        cloned.mid1 = int(new_mid)
        if hasattr(cloned, "mid2"):
            cloned.mid2 = int(new_mid)
        if hasattr(cloned, "mid3"):
            cloned.mid3 = None
        return cloned
    raise ValidationError(
        "property type does not support single-material E localization in phase 1",
        {"property_type": str(getattr(prop, "type", "")), "property_id": int(getattr(prop, "pid", new_pid))},
    )


def _clone_property_with_thickness(prop: Any, *, new_pid: int) -> Any:
    cloned = copy.deepcopy(prop)
    if hasattr(cloned, "pid"):
        cloned.pid = int(new_pid)
    ptype = str(getattr(cloned, "type", "")).upper()
    if ptype == "PSHELL":
        if getattr(cloned, "t", None) is None:
            raise ValidationError(
                "PSHELL thickness is missing during per-element thickness localization",
                {"property_id": int(getattr(prop, "pid", new_pid))},
            )
        return cloned
    raise ValidationError(
        "property type does not support single-thickness localization in phase 1",
        {"property_type": ptype, "property_id": int(getattr(prop, "pid", new_pid))},
    )


def _material_copy_with_new_id(material: Any, *, new_mid: int) -> Any:
    cloned = copy.deepcopy(material)
    if hasattr(cloned, "mid"):
        cloned.mid = int(new_mid)
        if getattr(cloned, "g", None) is not None:
            cloned.g = None
        return cloned
    raise ValidationError(
        "material type does not support cloning in phase 1",
        {"material_type": str(getattr(material, "type", "")), "material_id": int(getattr(material, "mid", new_mid))},
    )


def _localize_elements_e_parameters(
    *,
    input_bdf: str,
    output_bdf: str,
    preset: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    model = BDF(debug=False)
    model.read_bdf(input_bdf, xref=False)
    lower_scale = float(preset.get("lower_scale", DEFAULT_PARAMETER_LOWER_SCALE))
    upper_scale = float(preset.get("upper_scale", DEFAULT_PARAMETER_UPPER_SCALE))
    requested_element_ids = {
        int(item) for item in (preset.get("element_ids") or [])
    } if preset.get("element_ids") else None

    next_pid = (max(model.properties.keys()) if model.properties else 0) + 1
    next_mid = (max(model.materials.keys()) if model.materials else 0) + 1
    parameters: List[Dict[str, Any]] = []
    localized_count = 0
    skipped: List[dict] = []

    for eid, element in sorted(model.elements.items()):
        if requested_element_ids is not None and int(eid) not in requested_element_ids:
            continue
        pid = getattr(element, "pid", None)
        if pid is None:
            skipped.append({"element_id": int(eid), "reason": "element has no property id"})
            continue
        prop = model.properties.get(int(pid))
        if prop is None:
            skipped.append({"element_id": int(eid), "reason": f"property {int(pid)} not found"})
            continue
        source_mid = _property_material_id(prop)
        if source_mid is None:
            skipped.append({"element_id": int(eid), "reason": f"property {int(pid)} has no supported material reference"})
            continue
        material = model.materials.get(int(source_mid))
        if material is None or str(getattr(material, "type", "")).upper() != "MAT1":
            skipped.append({"element_id": int(eid), "reason": f"material {int(source_mid)} is not a supported MAT1"})
            continue
        e_value = _material_scalar(material, "E")
        if e_value is None:
            skipped.append({"element_id": int(eid), "reason": f"material {int(source_mid)} has no E value"})
            continue

        new_mid = int(next_mid)
        next_mid += 1
        new_pid = int(next_pid)
        next_pid += 1

        cloned_material = _material_copy_with_new_id(material, new_mid=new_mid)
        cloned_property = _clone_property_with_material(prop, new_pid=new_pid, new_mid=new_mid)
        model.materials[new_mid] = cloned_material
        model.properties[new_pid] = cloned_property
        element.pid = int(new_pid)
        localized_count += 1
        parameters.append({
            "name": f"E{int(eid)}",
            "type": "E",
            "element_id": int(eid),
            "property_id": int(new_pid),
            "material_id": int(new_mid),
            "source_property_id": int(pid),
            "source_material_id": int(source_mid),
            "initial": float(e_value),
            "lower": float(e_value * lower_scale),
            "upper": float(e_value * upper_scale),
        })

    if not parameters:
        raise ValidationError(
            "parameter_preset did not produce any supported per-element E parameters",
            {"input_bdf": input_bdf, "parameter_preset": preset, "skipped": skipped[:20]},
        )

    output_path = Path(output_bdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.write_bdf(str(output_path), interspersed=False)
    info = {
        "localized_input_bdf": str(output_path),
        "localized_element_count": int(localized_count),
        "parameter_count": len(parameters),
        "skipped_preview": skipped[:20],
    }
    return str(output_path), parameters, info


def _localize_elements_h_parameters(
    *,
    input_bdf: str,
    output_bdf: str,
    preset: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    model = BDF(debug=False)
    model.read_bdf(input_bdf, xref=False)
    lower_scale = float(preset.get("lower_scale", DEFAULT_PARAMETER_LOWER_SCALE))
    upper_scale = float(preset.get("upper_scale", DEFAULT_PARAMETER_UPPER_SCALE))
    requested_element_ids = {
        int(item) for item in (preset.get("element_ids") or [])
    } if preset.get("element_ids") else None

    next_pid = (max(model.properties.keys()) if model.properties else 0) + 1
    parameters: List[Dict[str, Any]] = []
    localized_count = 0
    skipped: List[dict] = []

    for eid, element in sorted(model.elements.items()):
        if requested_element_ids is not None and int(eid) not in requested_element_ids:
            continue
        pid = getattr(element, "pid", None)
        if pid is None:
            skipped.append({"element_id": int(eid), "reason": "element has no property id"})
            continue
        prop = model.properties.get(int(pid))
        if prop is None:
            skipped.append({"element_id": int(eid), "reason": f"property {int(pid)} not found"})
            continue
        thickness = _property_thickness_scalar(prop)
        if thickness is None:
            skipped.append(
                {
                    "element_id": int(eid),
                    "property_id": int(pid),
                    "property_type": str(getattr(prop, "type", "")),
                    "reason": "property has no supported shell thickness",
                }
            )
            continue

        new_pid = int(next_pid)
        next_pid += 1
        cloned_property = _clone_property_with_thickness(prop, new_pid=new_pid)
        model.properties[new_pid] = cloned_property
        element.pid = int(new_pid)
        localized_count += 1
        parameters.append({
            "name": f"H{int(eid)}",
            "type": "H",
            "element_id": int(eid),
            "property_id": int(new_pid),
            "source_property_id": int(pid),
            "initial": float(thickness),
            "lower": float(thickness * lower_scale),
            "upper": float(thickness * upper_scale),
        })

    if not parameters:
        raise ValidationError(
            "parameter_preset did not produce any supported per-element H parameters",
            {"input_bdf": input_bdf, "parameter_preset": preset, "skipped": skipped[:20]},
        )

    output_path = Path(output_bdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.write_bdf(str(output_path), interspersed=False)
    info = {
        "localized_input_bdf": str(output_path),
        "localized_element_count": int(localized_count),
        "parameter_count": len(parameters),
        "skipped_preview": skipped[:20],
    }
    return str(output_path), parameters, info


def _resolve_phase1_parameters(
    *,
    input_bdf: str,
    parameters: Optional[List[Dict[str, Any]]],
    parameter_preset: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    resolved = [dict(item) for item in (parameters or [])]
    if parameter_preset:
        preset_name = str(parameter_preset.get("preset") or "").strip().lower()
        if preset_name == "all_used_material_e_rho":
            resolved.extend(_build_all_used_material_e_rho_parameters(
                input_bdf=input_bdf,
                preset=parameter_preset,
            ))
        else:
            raise ValidationError(
                "unsupported SOL200 parameter preset",
                {
                    "preset": parameter_preset.get("preset"),
                    "supported_presets": ["all_used_material_e_rho", "all_elements_e", "all_elements_h"],
                },
            )
    if not resolved:
        raise ValidationError(
            "SOL200 parameters are required",
            {"parameters": parameters, "parameter_preset": parameter_preset},
        )
    return resolved


def _resolve_phase1_input_and_parameters(
    *,
    input_bdf: str,
    output_bdf: Optional[str],
    parameters: Optional[List[Dict[str, Any]]],
    parameter_preset: Optional[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    if not parameter_preset:
        return input_bdf, _resolve_phase1_parameters(
            input_bdf=input_bdf,
            parameters=parameters,
            parameter_preset=None,
        ), {}

    preset_name = str(parameter_preset.get("preset") or "").strip().lower()
    if preset_name not in {"all_elements_e", "all_elements_h"}:
        return input_bdf, _resolve_phase1_parameters(
            input_bdf=input_bdf,
            parameters=parameters,
            parameter_preset=parameter_preset,
        ), {}

    if parameters:
        raise ValidationError(
            "per-element parameter presets must not be mixed with manual parameters in phase 1",
            {"parameters_count": len(parameters or []), "preset": parameter_preset},
        )
    if output_bdf:
        localized_output = str(Path(output_bdf).expanduser().resolve().with_name(
            Path(output_bdf).expanduser().resolve().stem + ".localized_source.bdf"
        ))
        if preset_name == "all_elements_e":
            localized_input_bdf, preset_parameters, info = _localize_elements_e_parameters(
                input_bdf=input_bdf,
                output_bdf=localized_output,
                preset=parameter_preset,
            )
        else:
            localized_input_bdf, preset_parameters, info = _localize_elements_h_parameters(
                input_bdf=input_bdf,
                output_bdf=localized_output,
                preset=parameter_preset,
            )
        return localized_input_bdf, preset_parameters, info

    base_input = Path(input_bdf).expanduser().resolve()
    localized_output = str(base_input.with_name(f"{base_input.stem}_sol200_localized_source.bdf"))
    if preset_name == "all_elements_e":
        localized_input_bdf, preset_parameters, info = _localize_elements_e_parameters(
            input_bdf=input_bdf,
            output_bdf=localized_output,
            preset=parameter_preset,
        )
    else:
        localized_input_bdf, preset_parameters, info = _localize_elements_h_parameters(
            input_bdf=input_bdf,
            output_bdf=localized_output,
            preset=parameter_preset,
        )
    return localized_input_bdf, preset_parameters, info


def preview_sol200_workflow(
    *,
    project_id: Optional[int] = None,
    input_bdf: str,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    ) -> dict:
    resolved_input_parameters, resolved_responses = _resolve_sol200_config_sources(
        project_id=project_id,
        parameters=parameters,
        parameter_preset=parameter_preset,
        responses=responses,
    )
    preset_name = str((parameter_preset or {}).get("preset") or "").strip().lower()
    if preset_name in {"all_elements_e", "all_elements_h"}:
        localized_preview_path = str(
            Path(input_bdf).expanduser().resolve().with_name(
                Path(input_bdf).expanduser().resolve().stem + "_sol200_preview_localized_source.bdf"
            )
        )
        if preset_name == "all_elements_e":
            localized_input_bdf, resolved_parameters, info = _localize_elements_e_parameters(
                input_bdf=input_bdf,
                output_bdf=localized_preview_path,
                preset=parameter_preset or {},
            )
        else:
            localized_input_bdf, resolved_parameters, info = _localize_elements_h_parameters(
                input_bdf=input_bdf,
                output_bdf=localized_preview_path,
                preset=parameter_preset or {},
            )
        payload = preview_nastran_sol200_job(
            input_bdf=localized_input_bdf,
            parameters=resolved_parameters,
            responses=list(resolved_responses or []),
            settings=dict(settings or {}),
        )
        payload["input_bdf"] = str(Path(input_bdf).expanduser().resolve())
        payload["parameter_preset_info"] = info
        payload["config_source"] = "database" if project_id is not None and not list(responses or []) else "request"
        return payload

    resolved_parameters = _resolve_phase1_parameters(
        input_bdf=input_bdf,
        parameters=resolved_input_parameters,
        parameter_preset=parameter_preset,
    )
    payload = preview_nastran_sol200_job(
        input_bdf=input_bdf,
        parameters=resolved_parameters,
        responses=list(resolved_responses or []),
        settings=dict(settings or {}),
    )
    payload["config_source"] = "database" if project_id is not None and not list(responses or []) and not list(parameters or []) and not parameter_preset else "request"
    return payload


def generate_sol200_workflow(
    *,
    project_id: Optional[int] = None,
    batch_no: str = "1",
    case_name: str = "nastran_sol200",
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    resolved_input_parameters, resolved_responses = _resolve_sol200_config_sources(
        project_id=project_id,
        parameters=parameters,
        parameter_preset=parameter_preset,
        responses=responses,
    )
    localized_input_bdf, resolved_parameters, preset_info = _resolve_phase1_input_and_parameters(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_input_parameters,
        parameter_preset=parameter_preset,
    )
    payload = generate_nastran_sol200_job(
        input_bdf=localized_input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_parameters,
        responses=list(resolved_responses or []),
        settings=dict(settings or {}),
    )
    payload["input_bdf"] = str(Path(input_bdf).expanduser().resolve())
    if preset_info:
        payload["parameter_preset_info"] = preset_info
        payload.setdefault("generated_files", {})["localized_input_bdf"] = localized_input_bdf
        metadata_json = payload.get("generated_files", {}).get("metadata_json")
        if metadata_json and Path(metadata_json).exists():
            metadata = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
            metadata["source_input_bdf"] = str(Path(input_bdf).expanduser().resolve())
            metadata["localized_input_bdf"] = localized_input_bdf
            metadata["parameter_preset_info"] = preset_info
            Path(metadata_json).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["config_source"] = "database" if project_id is not None and not list(responses or []) and not list(parameters or []) and not parameter_preset else "request"
    if project_id is not None:
        from . import sensitivity_service as _sens

        _sens.persist_sensitivity_metadata(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            response_rows=_sol200_response_metadata_rows(resolved_responses),
            parameter_columns=[dict(item) for item in resolved_parameters],
            source={
                "source_kind": "sol200_metadata",
                "bdf_path": str(Path(payload["output_bdf"]).expanduser().resolve()),
                "metadata_path": payload.get("generated_files", {}).get("metadata_json"),
            },
        )
    return payload


def run_sol200_workflow(
    *,
    project_id: Optional[int] = None,
    batch_no: str = "1",
    case_name: str = "nastran_sol200",
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
) -> dict:
    resolved_input_parameters, resolved_responses = _resolve_sol200_config_sources(
        project_id=project_id,
        parameters=parameters,
        parameter_preset=parameter_preset,
        responses=responses,
    )
    localized_input_bdf, resolved_parameters, preset_info = _resolve_phase1_input_and_parameters(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_input_parameters,
        parameter_preset=parameter_preset,
    )
    payload = run_nastran_sol200_job(
        input_bdf=localized_input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_parameters,
        responses=list(resolved_responses or []),
        settings=dict(settings or {}),
        nastran=nastran,
        run_solver=run_solver,
        timeout_sec=timeout_sec,
        extra_args=list(extra_args or []),
    )
    payload["service"] = "nastran_sol200_phase1"
    payload["input_bdf"] = str(Path(input_bdf).expanduser().resolve())
    if preset_info:
        payload["parameter_preset_info"] = preset_info
        payload.setdefault("generated_files", {})["localized_input_bdf"] = localized_input_bdf
        metadata_json = payload.get("generated_files", {}).get("metadata_json")
        if metadata_json and Path(metadata_json).exists():
            metadata = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
            metadata["source_input_bdf"] = str(Path(input_bdf).expanduser().resolve())
            metadata["localized_input_bdf"] = localized_input_bdf
            metadata["parameter_preset_info"] = preset_info
            Path(metadata_json).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["config_source"] = "database" if project_id is not None and not list(responses or []) and not list(parameters or []) and not parameter_preset else "request"
    if project_id is not None:
        from . import sensitivity_service as _sens

        _sens.persist_sensitivity_metadata(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            response_rows=_sol200_response_metadata_rows(resolved_responses),
            parameter_columns=[dict(item) for item in resolved_parameters],
            source={
                "source_kind": "sol200_metadata",
                "bdf_path": str(Path(payload["output_bdf"]).expanduser().resolve()),
                "metadata_path": payload.get("generated_files", {}).get("metadata_json"),
            },
        )
    return payload


def _pick_first_existing_path(candidates: Sequence[Optional[str]]) -> Optional[str]:
    for candidate in list(candidates or []):
        text = str(candidate or "").strip()
        if not text:
            continue
        path = Path(text).expanduser().resolve()
        if path.exists() and path.is_file():
            return str(path)
    return None


def _resolve_generated_sol200_op2_path(run_payload: Dict[str, Any]) -> Optional[str]:
    solver = dict(run_payload.get("solver") or {})
    summary = dict(solver.get("artifacts_summary") or {})
    op2_files = [str(item) for item in list(summary.get("op2_files") or []) if str(item or "").strip()]
    resolved = _pick_first_existing_path(op2_files)
    if resolved:
        return resolved

    output_bdf = str(run_payload.get("output_bdf") or "").strip()
    if output_bdf:
        output_path = Path(output_bdf).expanduser().resolve()
        fallback = output_path.with_suffix(".op2")
        if fallback.exists() and fallback.is_file():
            return str(fallback)
    return None


def _resolve_generated_sol200_matrix_path(run_payload: Dict[str, Any]) -> Optional[str]:
    generated_files = dict(run_payload.get("generated_files") or {})
    solver = dict(run_payload.get("solver") or {})
    summary = dict(solver.get("artifacts_summary") or {})
    artifacts = dict(solver.get("artifacts") or {})

    explicit_csv = _pick_first_existing_path([
        generated_files.get("sensitivity_csv"),
    ])
    if explicit_csv:
        return explicit_csv

    assign_name = str(generated_files.get("sensitivity_csv_assign_name") or "").strip()
    if assign_name:
        assign_candidate = _pick_first_existing_path([
            artifacts.get(assign_name),
            str(Path(str(run_payload.get("output_bdf") or "")).expanduser().resolve().with_name(assign_name))
            if str(run_payload.get("output_bdf") or "").strip() else None,
        ])
        if assign_candidate:
            return assign_candidate

    unit11_candidates = [str(item) for item in list(summary.get("unit11_candidates") or []) if str(item or "").strip()]
    resolved_unit11 = _pick_first_existing_path(unit11_candidates)
    if resolved_unit11:
        return resolved_unit11
    return None


def run_sol200_and_store_workflow(
    *,
    project_id: int,
    batch_no: str = "1",
    case_name: str = "nastran_sol200",
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
    write_cloud_result: bool = False,
    cloud_result_group: Optional[str] = None,
    cloud_step_name: str = "Sensitivity",
    cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    try:
        _set_project_sensitivity_status(project_id, _WORKFLOW_STATUS_RUNNING)
    except Exception:
        pass
    try:
        run_payload = run_sol200_workflow(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            input_bdf=input_bdf,
            output_bdf=output_bdf,
            parameters=parameters,
            parameter_preset=parameter_preset,
            responses=responses,
            settings=settings,
            nastran=nastran,
            run_solver=run_solver,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
        )

        op2_path = _resolve_generated_sol200_op2_path(run_payload)
        matrix_path = _resolve_generated_sol200_matrix_path(run_payload)
        if not op2_path and not matrix_path:
            raise ValidationError(
                "SOL200 solve completed but no OP2 or matrix result file was found for sensitivity import",
                {
                    "project_id": int(project_id),
                    "batch_no": str(batch_no),
                    "case_name": str(case_name),
                    "output_bdf": run_payload.get("output_bdf"),
                    "generated_files": run_payload.get("generated_files"),
                    "artifacts_summary": (run_payload.get("solver") or {}).get("artifacts_summary"),
                },
            )

        metadata_json = _pick_first_existing_path([
            (run_payload.get("generated_files") or {}).get("metadata_json"),
        ])
        bdf_path = _pick_first_existing_path([run_payload.get("output_bdf")]) or str(
            Path(str(run_payload.get("output_bdf") or "")).expanduser().resolve()
        )

        if write_cloud_result:
            store_payload = store_sol200_sensitivity_cloud(
                project_id=int(project_id),
                batch_no=str(batch_no),
                case_name=str(case_name),
                op2_path=op2_path,
                matrix_path=matrix_path,
                bdf_path=bdf_path,
                metadata_json=metadata_json,
                parameter_names=parameter_names,
                response_names=response_names,
                cloud_result_group=cloud_result_group,
                cloud_step_name=cloud_step_name,
                cloud_field_name=cloud_field_name,
            )
        else:
            store_payload = store_sol200_sensitivity(
                project_id=int(project_id),
                batch_no=str(batch_no),
                case_name=str(case_name),
                op2_path=op2_path,
                matrix_path=matrix_path,
                bdf_path=bdf_path,
                metadata_json=metadata_json,
                parameter_names=parameter_names,
                response_names=response_names,
            )
        update_work_condition_project_status(
            int(project_id),
            sensitivity_status=_WORKFLOW_STATUS_DONE,
        )
        return {
            "workflow": "nastran_sol200_run_and_store",
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "case_name": str(case_name),
            "input_bdf": run_payload.get("input_bdf"),
            "output_bdf": run_payload.get("output_bdf"),
            "op2_path": op2_path,
            "matrix_path": matrix_path,
            "metadata_json": metadata_json,
            "run": run_payload,
            "store": store_payload,
            "write_cloud_result": bool(write_cloud_result),
            "warnings": list(run_payload.get("warnings") or []),
        }
    except Exception as exc:
        try:
            _set_project_sensitivity_status(project_id, _WORKFLOW_STATUS_FAILED)
        except Exception:
            pass
        _log_project_workflow_failure(
            project_id,
            "SOL200灵敏度求解失败",
            exc,
            [
                f"批次号: {str(batch_no)}",
                f"案例名: {str(case_name)}",
                f"输入BDF: {str(input_bdf)}",
            ],
        )
        raise


def run_sol200_modal_mac_and_store_workflow(
    *,
    project_id: int,
    batch_no: str = "1",
    case_name: str = "nastran_sol200_modal_mac",
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
    write_cloud_result: bool = False,
    cloud_result_group: Optional[str] = None,
    cloud_step_name: str = "Sensitivity",
    cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    from . import sensitivity_service as _sens
    from .project_path_service import resolve_project_workspace
    from ..importers.op2_service import build_modal_import_payload, preview_op2_sensitivity
    from .solver_service import run_nastran_sol103_job
    try:
        _set_project_sensitivity_status(project_id, _WORKFLOW_STATUS_RUNNING)
    except Exception:
        pass
    try:
        resolved_parameters, resolved_responses = _resolve_sol200_config_sources(
            project_id=int(project_id),
            parameters=parameters,
            parameter_preset=parameter_preset,
            responses=responses,
        )
        original_input_bdf = str(Path(input_bdf).expanduser().resolve())
        if responses:
            modal_mac_rows = [
                {
                    "response_name": str(item.get("name") or "").strip(),
                    "response_type": str(item.get("type") or "").strip().upper(),
                    "mode_number": item.get("mode_number"),
                    "extra_json": dict(item.get("extra_json") or {}),
                }
                for item in list(resolved_responses or [])
                if str(item.get("type") or "").strip().upper() == "MODAL_MAC"
            ]
        else:
            modal_mac_rows = _load_project_modal_mac_catalog_rows(int(project_id))
        if not modal_mac_rows:
            raise ValidationError(
                "no MODAL_MAC responses are available for SOL200 sensitivity extraction",
                {"project_id": int(project_id)},
            )

        expanded = expand_modal_mac_responses_to_sol200_displacements(
            project_id=int(project_id),
            response_rows=modal_mac_rows,
        )
        expanded_responses = list(expanded.get("expanded_rows") or [])
        if response_names:
            selected_names = {str(item or "").strip() for item in list(response_names or []) if str(item or "").strip()}
            modal_mac_rows = [row for row in modal_mac_rows if str(row.get("response_name") or "").strip() in selected_names]
            expanded_responses = [
                row for row in expanded_responses
                if str(dict(row.get("extra_json") or {}).get("source_response_name") or "").strip() in selected_names
            ]
        if not expanded_responses:
            raise ValidationError(
                "no expanded modal displacement responses were generated for MODAL_MAC",
                {"project_id": int(project_id), "response_names": list(response_names or [])},
            )

        run_payload = run_sol200_workflow(
            project_id=None,
            batch_no=str(batch_no),
            case_name=str(case_name),
            input_bdf=input_bdf,
            output_bdf=output_bdf,
            parameters=resolved_parameters,
            parameter_preset=parameter_preset,
            responses=expanded_responses,
            settings=settings,
            nastran=nastran,
            run_solver=run_solver,
            timeout_sec=timeout_sec,
            extra_args=extra_args,
        )
        op2_path = _resolve_generated_sol200_op2_path(run_payload)
        matrix_path = _resolve_generated_sol200_matrix_path(run_payload)
        if not op2_path and not matrix_path:
            raise ValidationError(
                "SOL200 modal-mac solve completed but no matrix result file was found",
                {
                    "project_id": int(project_id),
                    "batch_no": str(batch_no),
                    "output_bdf": run_payload.get("output_bdf"),
                    "generated_files": run_payload.get("generated_files"),
                },
            )

        metadata_json = _pick_first_existing_path([
            (run_payload.get("generated_files") or {}).get("metadata_json"),
        ])
        effective_parameter_columns = [dict(item or {}) for item in (resolved_parameters or [])]
        localized_input_bdf = _pick_first_existing_path([
            (run_payload.get("generated_files") or {}).get("localized_input_bdf"),
        ])
        if (not effective_parameter_columns) and metadata_json and Path(metadata_json).exists():
            try:
                metadata_payload = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
                effective_parameter_columns = [
                    dict(item or {}) for item in list(metadata_payload.get("parameters") or [])
                ]
                if not localized_input_bdf:
                    localized_input_bdf = _pick_first_existing_path([
                        metadata_payload.get("localized_input_bdf"),
                    ])
            except Exception:
                effective_parameter_columns = []
        base_input_bdf_for_fd = str(
            Path(localized_input_bdf or original_input_bdf).expanduser().resolve()
        )
        bdf_path = _pick_first_existing_path([run_payload.get("output_bdf")]) or str(
            Path(str(run_payload.get("output_bdf") or "")).expanduser().resolve()
        )
        if matrix_path:
            matrix_file = Path(str(matrix_path)).expanduser().resolve()
            if (not matrix_file.exists()) or matrix_file.stat().st_size <= 0:
                matrix_path = None
        expanded_response_names = [str(item.get("name") or "").strip() for item in expanded_responses]
        preview = preview_op2_sensitivity(
            project_id=None,
            batch_no=str(batch_no),
            op2_path=op2_path,
            matrix_path=matrix_path,
            bdf_path=bdf_path,
            metadata_json=metadata_json,
            parameter_names=parameter_names,
            response_names=expanded_response_names,
        )
        finite_difference_payload = None
        try:
            mac_matrix_payload = build_modal_mac_matrix_from_displacement_sensitivity(
                project_id=int(project_id),
                mac_response_rows=modal_mac_rows,
                parameter_columns=preview.get("parameter_columns") or [],
                displacement_response_rows=preview.get("response_rows") or [],
                displacement_matrix=preview.get("matrix_preview") or [],
                mac_scale=100.0,
            )
        except ValidationError as exc:
            current_mac_by_pair = {}
            for row in modal_mac_rows:
                extra = dict(row.get("extra_json") or {})
                fem_mode_no = int(extra.get("fem_mode_no", row.get("mode_number")))
                test_mode_no = int(extra["test_mode_no"])
                current_mac_by_pair[(test_mode_no, fem_mode_no)] = float(
                    compute_project_modal_mac(
                        project_id=int(project_id),
                        test_mode_no=test_mode_no,
                        fem_mode_no=fem_mode_no,
                        mac_scale=100.0,
                    )["mac"]
                )

            parameter_columns_for_fd = [dict(item or {}) for item in (effective_parameter_columns or [])]
            fd_rows = []
            fd_matrix_rows = []
            fd_details = []
            from .bayesian_service import _update_bdf_parameter_values

            for response_row in modal_mac_rows:
                extra = dict(response_row.get("extra_json") or {})
                fem_mode_no = int(extra.get("fem_mode_no", response_row.get("mode_number")))
                test_mode_no = int(extra["test_mode_no"])
                base_value = float(current_mac_by_pair[(test_mode_no, fem_mode_no)])
                derivatives = []
                for param_index, param_meta in enumerate(parameter_columns_for_fd):
                    initial_value = float(param_meta.get("initial", param_meta.get("initial_value")))
                    delta = max(abs(initial_value) * 5.0e-2, 1.0)
                    perturbed_values = [float(item.get("initial", item.get("initial_value"))) for item in parameter_columns_for_fd]
                    perturbed_values[param_index] = initial_value + delta
                    perturbed_bdf = str(
                        Path(base_input_bdf_for_fd).with_name(f"{Path(base_input_bdf_for_fd).stem}_fd_p{param_index + 1}.bdf")
                    )
                    _update_bdf_parameter_values(
                        input_bdf=base_input_bdf_for_fd,
                        parameter_columns=parameter_columns_for_fd,
                        updated_parameter_values=perturbed_values,
                        output_bdf=perturbed_bdf,
                    )
                    sol103_out_bdf = str(Path(perturbed_bdf).with_name(f"{Path(perturbed_bdf).stem}_sol103.bdf"))
                    sol103_payload = run_nastran_sol103_job(
                        input_bdf=perturbed_bdf,
                        output_bdf=sol103_out_bdf,
                        settings={
                            "dynamic.vectors": max(int(fem_mode_no), 1),
                            "dynamic.fmax": float((settings or {}).get("dynamic.fmax", 200.0)),
                            "dynamic.norm": str((settings or {}).get("dynamic.norm", "MASS")),
                            "result.target": "OP2",
                            "post": -1,
                        },
                        nastran=nastran,
                        run_solver=True,
                        timeout_sec=timeout_sec,
                        extra_args=list(extra_args or []),
                    )
                    summary = dict((sol103_payload.get("solver") or {}).get("artifacts_summary") or {})
                    op2_files = [str(item) for item in list(summary.get("op2_files") or []) if str(item or "").strip()]
                    perturbed_op2 = _pick_first_existing_path(op2_files)
                    if not perturbed_op2:
                        raise ValidationError(
                            "finite-difference SOL103 rerun did not produce an op2 file",
                            {"parameter_name": param_meta.get("name"), "artifacts_summary": summary},
                        )
                    modal_payload = build_modal_import_payload(
                        op2_path=perturbed_op2,
                        bdf_path=perturbed_bdf,
                        subcase_id=None,
                        mode_numbers=[int(fem_mode_no)],
                        all_subcases=True,
                    )
                    modes = list(modal_payload.get("modes") or [])
                    if not modes:
                        raise ValidationError(
                            "finite-difference SOL103 rerun did not return any modal payload",
                            {"parameter_name": param_meta.get("name"), "op2_path": perturbed_op2},
                        )
                    fem_mode_map = {}
                    for node_item in list(modes[0].get("nodes") or []):
                        instance_name = str(node_item.get("instance_name") or "BDF_MODEL").strip() or "BDF_MODEL"
                        fem_node_map_key = (instance_name, int(node_item["fem_node_label"]))
                        fem_mode_map[fem_node_map_key] = np.asarray(
                            [
                                float(node_item.get("u1", (node_item.get("vector") or [0.0, 0.0, 0.0])[0])),
                                float(node_item.get("u2", (node_item.get("vector") or [0.0, 0.0, 0.0])[1])),
                                float(node_item.get("u3", (node_item.get("vector") or [0.0, 0.0, 0.0])[2])),
                            ],
                            dtype=np.float64,
                        )
                    perturbed_mac = float(
                        compute_project_modal_mac_from_fem_mode_map(
                            project_id=int(project_id),
                            test_mode_no=int(test_mode_no),
                            fem_mode_map=fem_mode_map,
                            mac_scale=100.0,
                        )["mac"]
                    )
                    derivatives.append((perturbed_mac - base_value) / delta)
                    fd_details.append(
                        {
                            "response_name": response_row.get("response_name"),
                            "parameter_name": param_meta.get("name"),
                            "base_value": base_value,
                            "perturbed_value": perturbed_mac,
                            "delta": delta,
                            "derivative": derivatives[-1],
                            "perturbed_bdf": perturbed_bdf,
                            "perturbed_op2": perturbed_op2,
                        }
                    )
                fd_rows.append(
                    {
                        "response_name": str(response_row.get("response_name") or f"MAC_MODE_FE{fem_mode_no}_TEST{test_mode_no}"),
                        "response_type": "MODAL_MAC",
                        "mode_number": int(fem_mode_no),
                        "unit": "percent",
                    }
                )
                fd_matrix_rows.append([float(value) for value in derivatives])
            mac_matrix_payload = {
                "response_rows": fd_rows,
                "parameter_columns": parameter_columns_for_fd,
                "matrix": fd_matrix_rows,
                "mac_scale": 100.0,
            }
            finite_difference_payload = {
                "enabled": True,
                "reason": str(exc),
                "details": fd_details,
            }
        matrix_payload = {
            **mac_matrix_payload,
            "source": {
                "source_kind": "sol200_modal_mac",
                "op2_path": op2_path,
                "matrix_path": matrix_path,
                "bdf_path": bdf_path,
                "metadata_path": metadata_json,
            },
        }
        stored = _sens._persist_sensitivity_matrix(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            matrix_payload=matrix_payload,
        )
        if write_cloud_result:
            workspace = _sens._workspace_path(resolve_project_workspace(int(project_id)))
            matrix_payload["workspace"] = workspace
            matrix_payload["parameter_columns"] = _build_op2_parameter_columns_with_mappings(
                workspace=workspace,
                bdf_path=bdf_path,
                parameter_columns=matrix_payload.get("parameter_columns") or [],
            )
            cloud_result = _write_element_cloud_result_to_workspace(
                workspace=workspace,
                batch_no=str(batch_no),
                matrix_payload=matrix_payload,
                result_group=cloud_result_group,
                step_name=cloud_step_name,
                field_name=cloud_field_name,
            )
            _register_external_sensitivity_result_group(
                project_id=int(project_id),
                preview_source=dict(matrix_payload.get("source") or {}),
                cloud_result=cloud_result,
            )
            stored = {
                **stored,
                "workspace": workspace,
                "cloud_result": cloud_result,
            }
        update_work_condition_project_status(
            int(project_id),
            sensitivity_status=_WORKFLOW_STATUS_DONE,
        )
        return {
            "workflow": "nastran_sol200_modal_mac_run_and_store",
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "case_name": str(case_name),
            "input_bdf": run_payload.get("input_bdf"),
            "output_bdf": run_payload.get("output_bdf"),
            "op2_path": op2_path,
            "matrix_path": matrix_path,
            "metadata_json": metadata_json,
            "expanded_modal_mac": expanded,
            "displacement_response_count": len(expanded_responses),
            "modal_mac_response_count": len(mac_matrix_payload.get("response_rows") or []),
            "run": run_payload,
            "preview": preview,
            "finite_difference_fallback": finite_difference_payload,
            "store": stored,
            "warnings": list(run_payload.get("warnings") or []) + list(preview.get("warnings") or []),
        }
    except Exception as exc:
        try:
            _set_project_sensitivity_status(project_id, _WORKFLOW_STATUS_FAILED)
        except Exception:
            pass
        _log_project_workflow_failure(
            project_id,
            "SOL200模态MAC灵敏度求解失败",
            exc,
            [
                f"批次号: {str(batch_no)}",
                f"案例名: {str(case_name)}",
                f"输入BDF: {str(input_bdf)}",
            ],
        )
        raise


def sync_run_sol200_modal_mac_and_store_workflow(
    *,
    project_id: int,
    batch_no: str = "1",
    case_name: str = "nastran_sol200_modal_mac",
    input_bdf: str,
    output_bdf: Optional[str] = None,
    overwrite: bool = True,
    response_source: str = "response_catalog",
    response_category: Optional[str] = None,
    mac_threshold: Optional[float] = None,
    max_freq_error_ratio: Optional[float] = 0.2,
    matching_method: str = "greedy",
    settings: Optional[Dict[str, Any]] = None,
    nastran: Optional[str] = None,
    run_solver: bool = True,
    timeout_sec: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    write_cloud_result: bool = False,
    cloud_result_group: Optional[str] = None,
    cloud_step_name: str = "Sensitivity",
    cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    try:
        resolved_settings = dict(settings or {})
        sync_payload = sync_sol200_config_from_catalog(
            project_id=int(project_id),
            overwrite=bool(overwrite),
            parameter_source="selected_parameter",
            response_source=str(response_source or "response_catalog"),
            response_category="MODAL_MAC",
            mac_threshold=mac_threshold,
            max_freq_error_ratio=max_freq_error_ratio,
            matching_method=str(matching_method or "greedy"),
        )
        run_payload = run_sol200_modal_mac_and_store_workflow(
            project_id=int(project_id),
            batch_no=str(batch_no),
            case_name=str(case_name),
            input_bdf=input_bdf,
            output_bdf=output_bdf,
            parameters=None,
            parameter_preset=None,
            responses=None,
            settings=resolved_settings,
            nastran=nastran,
            run_solver=run_solver,
            timeout_sec=timeout_sec,
            extra_args=list(extra_args or []),
            parameter_names=None,
            response_names=None,
            write_cloud_result=write_cloud_result,
            cloud_result_group=cloud_result_group,
            cloud_step_name=cloud_step_name,
            cloud_field_name=cloud_field_name,
        )
        return {
            "workflow": "nastran_sol200_modal_mac_sync_run_and_store",
            "project_id": int(project_id),
            "batch_no": str(batch_no),
            "case_name": str(case_name),
            "input_bdf": str(Path(input_bdf).expanduser().resolve()),
            "output_bdf": str(
                Path(
                    str(
                        output_bdf
                        or run_payload.get("output_bdf")
                        or ""
                    )
                ).expanduser().resolve()
            ) if str(output_bdf or run_payload.get("output_bdf") or "").strip() else None,
            "response_source": str(response_source or "response_catalog"),
            "response_category": str(response_category or "MODAL_MAC"),
            "sync_config": sync_payload,
            "run_and_store": run_payload,
        }
    except Exception as exc:
        try:
            _set_project_sensitivity_status(project_id, _WORKFLOW_STATUS_FAILED)
        except Exception:
            pass
        _log_project_workflow_failure(
            project_id,
            "SOL200 modal-mac sync-run workflow failed",
            exc,
            [
                f"batch_no: {str(batch_no)}",
                f"case_name: {str(case_name)}",
                f"input_bdf: {str(input_bdf)}",
                f"response_source: {str(response_source or 'response_catalog')}",
            ],
        )
        raise


def preview_sol200_sensitivity(
    *,
    project_id: Optional[int] = None,
    batch_no: str = "1",
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
) -> dict:
    payload = preview_op2_sensitivity(
        project_id=int(project_id) if project_id is not None else None,
        batch_no=str(batch_no),
        op2_path=op2_path,
        matrix_path=matrix_path,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
    )
    payload["service"] = "nastran_sol200_phase1"
    return payload


def store_sol200_sensitivity(
    *,
    project_id: int,
    batch_no: str,
    case_name: str,
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
) -> dict:
    payload = store_op2_sensitivity(
        project_id=int(project_id),
        batch_no=str(batch_no),
        case_name=str(case_name),
        op2_path=op2_path,
        matrix_path=matrix_path,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
    )
    payload["service"] = "nastran_sol200_phase1"
    return payload


def store_sol200_sensitivity_cloud(
    *,
    project_id: int,
    batch_no: str,
    case_name: str,
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
    cloud_result_group: Optional[str] = None,
    cloud_step_name: str = "Sensitivity",
    cloud_field_name: str = "SENSITIVITY_CLOUD",
) -> dict:
    payload = store_op2_sensitivity_cloud(
        project_id=int(project_id),
        batch_no=str(batch_no),
        case_name=str(case_name),
        op2_path=op2_path,
        matrix_path=matrix_path,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
        cloud_result_group=cloud_result_group,
        cloud_step_name=cloud_step_name,
        cloud_field_name=cloud_field_name,
    )
    payload["service"] = "nastran_sol200_phase1"
    return payload


def export_sol200_sensitivity_vtu(
    *,
    project_id: int,
    batch_no: str,
    input_bdf: str,
    output_vtu: str,
    response_name: str,
    metadata_json: Optional[str] = None,
) -> dict:
    payload = export_sensitivity_to_vtu(
        project_id=int(project_id),
        batch_no=str(batch_no),
        input_bdf=input_bdf,
        output_vtu=output_vtu,
        response_name=response_name,
        metadata_json=metadata_json,
    )
    payload["service"] = "nastran_sol200_phase1"
    return payload
