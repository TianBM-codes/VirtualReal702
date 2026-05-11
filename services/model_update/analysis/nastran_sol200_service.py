from typing import Any, Dict, List, Optional, Sequence

from pyNastran.bdf.bdf import BDF
from src.l3.core.errors import ValidationError

from ..importers.op2_service import (
    export_sensitivity_to_vtu,
    preview_op2_sensitivity,
    store_op2_sensitivity,
)
from .solver_service import (
    generate_nastran_sol200_job,
    preview_nastran_sol200_job,
    run_nastran_sol200_job,
)


# This module is the dedicated orchestration layer for the phase-1 Nastran
# SOL200 workflow. The lower layers still own card generation, solver launch,
# and matrix parsing; this service keeps the public SOL200 path centralized so
# future phase-2/3 work can extend one place instead of scattering logic.


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


def _resolve_phase1_parameters(
    *,
    input_bdf: str,
    parameters: Optional[List[Dict[str, Any]]],
    parameter_preset: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    resolved = [dict(item) for item in (parameters or [])]
    if parameter_preset:
        preset_name = str(parameter_preset.get("preset") or "").strip().lower()
        if preset_name != "all_used_material_e_rho":
            raise ValidationError(
                "unsupported SOL200 parameter preset",
                {
                    "preset": parameter_preset.get("preset"),
                    "supported_presets": ["all_used_material_e_rho"],
                },
            )
        resolved.extend(_build_all_used_material_e_rho_parameters(
            input_bdf=input_bdf,
            preset=parameter_preset,
        ))
    if not resolved:
        raise ValidationError(
            "SOL200 parameters are required",
            {"parameters": parameters, "parameter_preset": parameter_preset},
        )
    return resolved


def preview_sol200_workflow(
    *,
    input_bdf: str,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    resolved_parameters = _resolve_phase1_parameters(
        input_bdf=input_bdf,
        parameters=parameters,
        parameter_preset=parameter_preset,
    )
    return preview_nastran_sol200_job(
        input_bdf=input_bdf,
        parameters=resolved_parameters,
        responses=list(responses or []),
        settings=dict(settings or {}),
    )


def generate_sol200_workflow(
    *,
    input_bdf: str,
    output_bdf: Optional[str] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> dict:
    resolved_parameters = _resolve_phase1_parameters(
        input_bdf=input_bdf,
        parameters=parameters,
        parameter_preset=parameter_preset,
    )
    return generate_nastran_sol200_job(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_parameters,
        responses=list(responses or []),
        settings=dict(settings or {}),
    )


def run_sol200_workflow(
    *,
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
    resolved_parameters = _resolve_phase1_parameters(
        input_bdf=input_bdf,
        parameters=parameters,
        parameter_preset=parameter_preset,
    )
    payload = run_nastran_sol200_job(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_parameters,
        responses=list(responses or []),
        settings=dict(settings or {}),
        nastran=nastran,
        run_solver=run_solver,
        timeout_sec=timeout_sec,
        extra_args=list(extra_args or []),
    )
    payload["service"] = "nastran_sol200_phase1"
    return payload


def preview_sol200_sensitivity(
    *,
    op2_path: Optional[str] = None,
    matrix_path: Optional[str] = None,
    bdf_path: Optional[str] = None,
    metadata_json: Optional[str] = None,
    parameter_names: Optional[Sequence[str]] = None,
    response_names: Optional[Sequence[str]] = None,
) -> dict:
    payload = preview_op2_sensitivity(
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
