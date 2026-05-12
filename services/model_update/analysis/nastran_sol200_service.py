import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


def _clone_property_with_material(prop: Any, *, new_pid: int, new_mid: int) -> Any:
    cloned = copy.deepcopy(prop)
    if hasattr(cloned, "pid"):
        cloned.pid = int(new_pid)
    if hasattr(cloned, "mid"):
        cloned.mid = int(new_mid)
        return cloned
    if hasattr(cloned, "mid1"):
        cloned.mid1 = int(new_mid)
        return cloned
    raise ValidationError(
        "property type does not support single-material E localization in phase 1",
        {"property_type": str(getattr(prop, "type", "")), "property_id": int(getattr(prop, "pid", new_pid))},
    )


def _material_copy_with_new_id(material: Any, *, new_mid: int) -> Any:
    cloned = copy.deepcopy(material)
    if hasattr(cloned, "mid"):
        cloned.mid = int(new_mid)
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
    input_bdf: str,
    parameters: Optional[List[Dict[str, Any]]] = None,
    parameter_preset: Optional[Dict[str, Any]] = None,
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
    ) -> dict:
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
            responses=list(responses or []),
            settings=dict(settings or {}),
        )
        payload["input_bdf"] = str(Path(input_bdf).expanduser().resolve())
        payload["parameter_preset_info"] = info
        return payload

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
    localized_input_bdf, resolved_parameters, preset_info = _resolve_phase1_input_and_parameters(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=parameters,
        parameter_preset=parameter_preset,
    )
    payload = generate_nastran_sol200_job(
        input_bdf=localized_input_bdf,
        output_bdf=output_bdf,
        parameters=resolved_parameters,
        responses=list(responses or []),
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
            } for item in list(responses or [])],
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
    localized_input_bdf, resolved_parameters, preset_info = _resolve_phase1_input_and_parameters(
        input_bdf=input_bdf,
        output_bdf=output_bdf,
        parameters=parameters,
        parameter_preset=parameter_preset,
    )
    payload = run_nastran_sol200_job(
        input_bdf=localized_input_bdf,
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
            } for item in list(responses or [])],
            parameter_columns=[dict(item) for item in resolved_parameters],
            source={
                "source_kind": "sol200_metadata",
                "bdf_path": str(Path(payload["output_bdf"]).expanduser().resolve()),
                "metadata_path": payload.get("generated_files", {}).get("metadata_json"),
            },
        )
    return payload


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
