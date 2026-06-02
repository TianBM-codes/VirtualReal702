import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pyNastran.bdf.bdf import BDF
from db import ensure_tables_exist, get_connection
from src.l3.core.errors import ValidationError

from ..importers.op2_service import (
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


# This module is the dedicated orchestration layer for the phase-1 Nastran
# SOL200 workflow. The lower layers still own card generation, solver launch,
# and matrix parsing; this service keeps the public SOL200 path centralized so
# future phase-2/3 work can extend one place instead of scattering logic.


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
    if token != "FREQ":
        raise ValidationError(
            "unsupported SOL200 response type",
            {"response_type": value, "allowed": ["FREQ"]},
        )
    return token


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
    extra_json: Optional[Dict[str, Any]] = None,
) -> dict:
    ensure_tables_exist()
    resolved_name = str(response_name or "").strip()
    if not resolved_name:
        raise ValidationError("response_name is required", {"response_name": response_name})
    resolved_type = _normalize_sol200_response_type(response_type)
    resolved_mode_number = int(mode_number) if mode_number is not None else None
    if resolved_type == "FREQ" and resolved_mode_number is None:
        raise ValidationError(
            "mode_number is required for SOL200 FREQ response",
            {"response_type": resolved_type, "mode_number": mode_number},
        )

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
        "extra_json": dict(extra_json or {}),
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
    if response_type not in {"FREQ", "MODAL_FREQUENCY"}:
        return None, "response_is_not_modal_frequency"
    extra = dict(response_row.get("extra_json") or {})
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


def sync_sol200_config_from_catalog(
    *,
    project_id: int,
    overwrite: bool = True,
    parameter_source: str = "selected_parameter",
    response_source: str = "response_catalog",
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
        if bool(row.get("enabled", True)) and _scope_contains(row.get("solver_scope"), "SOL200")
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
                extra_json={
                    "source_table": "t_mt_py_fem_response_catalog",
                    **dict(item.get("extra_json") or {}),
                },
            )
        )

    return {
        "project_id": int(project_id),
        "overwrite": bool(overwrite),
        "parameter_source": resolved_parameter_source,
        "response_source": resolved_response_source,
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
    return [
        {
            "name": item["response_name"],
            "type": item["response_type"],
            "mode_number": item.get("mode_number"),
        }
        for item in list(payload.get("responses") or [])
    ]


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


def _build_all_used_material_e_rho_parameters(
    *,
    input_bdf: str,
    preset: Dict[str, Any],
) -> List[Dict[str, Any]]:
    model = _load_bdf_model(input_bdf)
    lower_scale = float(preset.get("lower_scale", 0.8))
    upper_scale = float(preset.get("upper_scale", 1.2))
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
    lower_scale = float(preset.get("lower_scale", 0.8))
    upper_scale = float(preset.get("upper_scale", 1.2))
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
                    "supported_presets": ["all_used_material_e_rho", "all_elements_e"],
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
    if preset_name != "all_elements_e":
        return input_bdf, _resolve_phase1_parameters(
            input_bdf=input_bdf,
            parameters=parameters,
            parameter_preset=parameter_preset,
        ), {}

    if parameters:
        raise ValidationError(
            "all_elements_e preset must not be mixed with manual parameters in phase 1",
            {"parameters_count": len(parameters or []), "preset": parameter_preset},
        )
    if output_bdf:
        localized_output = str(Path(output_bdf).expanduser().resolve().with_name(
            Path(output_bdf).expanduser().resolve().stem + ".localized_source.bdf"
        ))
        localized_input_bdf, preset_parameters, info = _localize_elements_e_parameters(
            input_bdf=input_bdf,
            output_bdf=localized_output,
            preset=parameter_preset,
        )
        return localized_input_bdf, preset_parameters, info

    base_input = Path(input_bdf).expanduser().resolve()
    localized_output = str(base_input.with_name(f"{base_input.stem}_sol200_localized_source.bdf"))
    localized_input_bdf, preset_parameters, info = _localize_elements_e_parameters(
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
    if preset_name == "all_elements_e":
        localized_preview_path = str(
            Path(input_bdf).expanduser().resolve().with_name(
                Path(input_bdf).expanduser().resolve().stem + "_sol200_preview_localized_source.bdf"
            )
        )
        localized_input_bdf, resolved_parameters, info = _localize_elements_e_parameters(
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
            response_rows=[{
                "response_name": item.get("name"),
                "response_type": item.get("type"),
                "mode_number": item.get("mode_number"),
            } for item in list(resolved_responses or [])],
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
            response_rows=[{
                "response_name": item.get("name"),
                "response_type": item.get("type"),
                "mode_number": item.get("mode_number"),
            } for item in list(resolved_responses or [])],
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
) -> dict:
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
    if not op2_path:
        raise ValidationError(
            "SOL200 solve completed but no OP2 file was found for sensitivity import",
            {
                "project_id": int(project_id),
                "batch_no": str(batch_no),
                "case_name": str(case_name),
                "output_bdf": run_payload.get("output_bdf"),
                "artifacts_summary": (run_payload.get("solver") or {}).get("artifacts_summary"),
            },
        )

    metadata_json = _pick_first_existing_path([
        (run_payload.get("generated_files") or {}).get("metadata_json"),
    ])
    bdf_path = _pick_first_existing_path([run_payload.get("output_bdf")]) or str(
        Path(str(run_payload.get("output_bdf") or "")).expanduser().resolve()
    )

    store_payload = store_sol200_sensitivity(
        project_id=int(project_id),
        batch_no=str(batch_no),
        case_name=str(case_name),
        op2_path=op2_path,
        matrix_path=None,
        bdf_path=bdf_path,
        metadata_json=metadata_json,
        parameter_names=parameter_names,
        response_names=response_names,
    )
    return {
        "workflow": "nastran_sol200_run_and_store",
        "project_id": int(project_id),
        "batch_no": str(batch_no),
        "case_name": str(case_name),
        "input_bdf": run_payload.get("input_bdf"),
        "output_bdf": run_payload.get("output_bdf"),
        "op2_path": op2_path,
        "metadata_json": metadata_json,
        "run": run_payload,
        "store": store_payload,
        "warnings": list(run_payload.get("warnings") or []),
    }


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
