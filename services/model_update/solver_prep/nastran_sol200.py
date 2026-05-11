from pathlib import Path
from typing import Any, Dict, List, Optional

from src.l3.core.errors import ValidationError

from .nastran_sol103 import (
    build_displacement_request,
    build_eigrl_fields,
    format_float_like_bas,
    read_lines,
    resolve_post_value,
    resolve_result_target,
    split_bdf,
    write_lines,
)

SKIP_SOL200_PARAM_NAMES = {
    "POST",
    "K6ROT",
    "GRDPNT",
    "COUPMASS",
}


def _normalize_sol200_settings(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resolved = dict(settings or {})
    if resolved.get("dynamic.norm") in (None, ""):
        resolved["dynamic.norm"] = "MASS"
    if str(resolved.get("dynamic.norm", "")).strip().upper() not in {"2", "MASS"}:
        raise ValidationError(
            "SOL200 sensitivity requires MASS-normalized eigenvectors",
            {
                "dynamic.norm": resolved.get("dynamic.norm"),
                "allowed_values": [2, "MASS"],
            },
        )
    if resolved.get("result.target") in (None, ""):
        resolved["result.target"] = "OP2"
    return resolved


def _resolve_sol200_deck_mode(settings: Optional[Dict[str, Any]] = None) -> str:
    token = str((settings or {}).get("sol200.deck_mode", "inline")).strip().lower()
    if token not in {"inline", "include"}:
        raise ValidationError(
            "sol200.deck_mode must be one of: inline, include",
            {"sol200.deck_mode": (settings or {}).get("sol200.deck_mode")},
        )
    return token


def _resolve_sol200_csv_output(
    output_bdf: Optional[str],
    settings: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    resolved_settings = settings or {}
    enabled = bool(resolved_settings.get("sol200.sensitivity_csv", False))
    if not enabled:
        return None
    explicit = resolved_settings.get("sol200.sensitivity_csv_path")
    if explicit:
        return str(Path(str(explicit)).expanduser().resolve())
    if output_bdf:
        output_path = Path(output_bdf).expanduser().resolve()
        return str(output_path.with_suffix(".sens.csv"))
    return None


def _normalize_card_name(line: str) -> str:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("$"):
        return ""
    token = stripped.split(",", 1)[0].split()[0]
    return token.rstrip("*").upper()


def _parse_param_name(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if stripped.upper().startswith("PARAM,"):
        parts = [p.strip().upper() for p in stripped.split(",")]
        if len(parts) >= 2:
            return parts[1]
    parts = stripped.split()
    if len(parts) >= 2 and parts[0].upper() == "PARAM":
        return parts[1].upper()
    return ""


def _is_continuation_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    return stripped[0] in {"+", "*", ","}


def _format_desvar_line(index: int, parameter: Dict[str, Any]) -> str:
    name = str(parameter.get("name") or "").strip()
    if not name:
        raise ValidationError("SOL200 parameter name is required", {"parameter": parameter})
    initial = parameter.get("initial")
    if initial is None:
        raise ValidationError("SOL200 parameter initial is required", {"parameter": parameter})
    lower = parameter.get("lower")
    upper = parameter.get("upper")
    fields = [
        "DESVAR",
        str(int(index)),
        name,
        format_float_like_bas(float(initial)),
        format_float_like_bas(float(lower)) if lower is not None else "",
        format_float_like_bas(float(upper)) if upper is not None else "",
    ]
    return ",".join(fields).rstrip(",")


def _format_relation_header_line(
    *,
    card_name: str,
    relation_id: int,
    target_type: str,
    target_id: int,
    field_name: str,
) -> str:
    return (
        f"{card_name:<8}"
        f"{int(relation_id):>8}"
        f"{target_type:<8}"
        f"{int(target_id):>8}"
        f"{field_name:<8}"
        f"{'':>8}"
        f"{'':>8}"
        f"{'':>8}"
    ).rstrip()


def _format_relation_continuation_line(*, desvar_id: int, coefficient: float = 1.0) -> str:
    return f"{'':<8}{int(desvar_id):>8}{format_float_like_bas(float(coefficient)):>8}".rstrip()


def _build_parameter_relation_lines(index: int, parameter: Dict[str, Any]) -> List[str]:
    ptype = str(parameter.get("type") or "").upper()
    if ptype == "H":
        property_id = parameter.get("property_id")
        if property_id is None:
            raise ValidationError("H parameter requires property_id", {"parameter": parameter})
        return [
            _format_relation_header_line(
                card_name="DVPREL1",
                relation_id=index,
                target_type="PSHELL",
                target_id=int(property_id),
                field_name="T",
            ),
            _format_relation_continuation_line(desvar_id=index),
        ]
    if ptype == "E":
        material_id = parameter.get("material_id")
        if material_id is None:
            raise ValidationError("E parameter requires material_id", {"parameter": parameter})
        return [
            _format_relation_header_line(
                card_name="DVMREL1",
                relation_id=index,
                target_type="MAT1",
                target_id=int(material_id),
                field_name="E",
            ),
            _format_relation_continuation_line(desvar_id=index),
        ]
    if ptype == "RHO":
        material_id = parameter.get("material_id")
        if material_id is None:
            raise ValidationError("RHO parameter requires material_id", {"parameter": parameter})
        return [
            _format_relation_header_line(
                card_name="DVMREL1",
                relation_id=index,
                target_type="MAT1",
                target_id=int(material_id),
                field_name="RHO",
            ),
            _format_relation_continuation_line(desvar_id=index),
        ]
    raise ValidationError(
        "unsupported SOL200 parameter type",
        {"supported_types": ["H", "E", "RHO"], "parameter_type": ptype},
    )


def _build_response_lines(index: int, response: Dict[str, Any]) -> List[str]:
    rtype = str(response.get("type") or "").upper()
    if rtype != "FREQ":
        raise ValidationError(
            "unsupported SOL200 response type",
            {"supported_types": ["FREQ"], "response_type": rtype},
        )
    mode_number = response.get("mode_number")
    if mode_number is None:
        raise ValidationError("FREQ response requires mode_number", {"response": response})
    name = str(response.get("name") or f"FREQ_MODE_{int(mode_number)}").strip()
    return [
        f"DRESP1,{int(index)},{name},FREQ,STRUC,,{int(mode_number)}",
        f"DCONSTR,1,{int(index)},1.0E30,1.0E30",
    ]


def build_sol200_controls(
    settings: Optional[Dict[str, Any]] = None,
    *,
    sensitivity_csv_path: Optional[str] = None,
) -> List[str]:
    settings = _normalize_sol200_settings(settings)
    eigrl = build_eigrl_fields(settings)
    result_target = resolve_result_target(settings.get("result.target", "OP2"))
    displacement_line = build_displacement_request(settings.get("displacement", "ALL"), result_target)
    post = resolve_post_value(settings, result_target=result_target, default_post=-5)

    k6rot = float(settings.get("fem.k6rot", -1.0))
    if k6rot < 0:
        k6rot_text = "10.0"
    else:
        k6rot_text = format_float_like_bas(k6rot)

    lumped = bool(settings.get("compute.lumped", True))
    coupmass = "-1" if lumped else "1"

    lines: List[str] = []
    if sensitivity_csv_path:
        csv_text = str(Path(sensitivity_csv_path)).replace("\\", "/")
        lines.append(f"ASSIGN USERFILE='{csv_text}' FORM=FORMATTED STATUS=UNKNOWN UNIT=52")

    lines.extend([
        "SOL 200",
        "CEND",
        f"METHOD = {eigrl['sid']}",
        displacement_line,
        "DESSUB = 1",
        "DSAPRT(FORMATTED,EXPORT,END=SENS)" if sensitivity_csv_path else "DSAPRT(NOPRINT,EXPORT,END=SENS)",
        "",
        "SUBCASE 1",
        "  ANALYSIS = MODES",
        "",
        "BEGIN BULK",
        f"PARAM   POST          {post}",
        "PARAM   GRDPNT         0",
        f"PARAM   K6ROT   {k6rot_text}",
        f"PARAM   COUPMASS      {coupmass}",
        "PARAM,XYUNIT,52" if sensitivity_csv_path else "",
        "EIGRL   {sid:>8}{v1:>8}{v2:>8}{nd:>8}{blank:>8}{maxset:>8}{shfscl:>8}{norm:>8}".format(
            sid=eigrl["sid"],
            v1=eigrl["v1"],
            v2=eigrl["v2"],
            nd=eigrl["nd"],
            blank="",
            maxset=eigrl["maxset"],
            shfscl=eigrl["shfscl"],
            norm=eigrl["normalization"],
        ),
    ])
    return [line for line in lines if line != ""]


def build_sol200_design_lines(
    *,
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    if not parameters:
        raise ValidationError("SOL200 parameters are required", {"parameters": parameters})
    if not responses:
        raise ValidationError("SOL200 responses are required", {"responses": responses})

    desvar_lines: List[str] = []
    relation_lines: List[str] = []
    response_lines: List[str] = []

    for index, parameter in enumerate(parameters, start=1):
        desvar_lines.append(_format_desvar_line(index, parameter))
        relation_lines.extend(_build_parameter_relation_lines(index, parameter))

    for index, response in enumerate(responses, start=1):
        response_lines.extend(_build_response_lines(index, response))

    design_lines = [
        "$ -----------------------------------------------------------------------------",
        "$ Phase-1 SOL200 design model include",
        "$ -----------------------------------------------------------------------------",
        *desvar_lines,
        *relation_lines,
        *response_lines,
    ]
    return {
        "desvar_lines": desvar_lines,
        "relation_lines": relation_lines,
        "response_lines": response_lines,
        "design_lines": design_lines,
    }


def _format_include_line(main_bdf_path: str, design_bdf_path: str) -> str:
    main_path = Path(main_bdf_path).resolve()
    design_path = Path(design_bdf_path).resolve()
    rel_path = design_path.relative_to(main_path.parent) if design_path.parent == main_path.parent else Path(
        design_path.as_posix()
    )
    rel_text = str(rel_path).replace("\\", "/")
    if not rel_text.startswith("."):
        rel_text = f"./{rel_text}"
    return f"INCLUDE '{rel_text}'"


def build_sol200_lines(
    *,
    input_bdf: str,
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
    output_bdf: Optional[str] = None,
    output_design_bdf: Optional[str] = None,
) -> Dict[str, Any]:
    settings = _normalize_sol200_settings(settings)
    deck_mode = _resolve_sol200_deck_mode(settings)
    sensitivity_csv_path = _resolve_sol200_csv_output(output_bdf, settings)

    lines = read_lines(input_bdf)
    _, bulk_lines = split_bdf(lines)

    control_lines = build_sol200_controls(settings, sensitivity_csv_path=sensitivity_csv_path)
    design_payload = build_sol200_design_lines(parameters=parameters, responses=responses)
    filtered_bulk_lines = filter_sol200_bulk_lines(bulk_lines)
    desvar_lines = design_payload["desvar_lines"]
    relation_lines = design_payload["relation_lines"]
    response_lines = design_payload["response_lines"]
    design_lines = design_payload["design_lines"]

    if deck_mode == "include":
        if not output_bdf or not output_design_bdf:
            raise ValidationError(
                "include deck mode requires output_bdf and output_design_bdf",
                {
                    "output_bdf": output_bdf,
                    "output_design_bdf": output_design_bdf,
                },
            )
        include_line = _format_include_line(output_bdf, output_design_bdf)
        output_lines = control_lines + [include_line, ""] + filtered_bulk_lines
    else:
        output_lines = control_lines + design_lines + filtered_bulk_lines
    return {
        "deck_mode": deck_mode,
        "control_lines": control_lines,
        "desvar_lines": desvar_lines,
        "relation_lines": relation_lines,
        "response_lines": response_lines,
        "design_lines": design_lines,
        "filtered_bulk_lines": filtered_bulk_lines,
        "output_lines": output_lines,
        "sensitivity_csv_path": sensitivity_csv_path,
    }


def filter_sol200_bulk_lines(bulk_lines: List[str]) -> List[str]:
    filtered: List[str] = []
    skipping_continuation = False
    skip_prefixes = ("EIG", "DES", "DCO", "DRE", "DVM", "DVP")
    for line in bulk_lines:
        stripped = line.lstrip()
        if not stripped:
            filtered.append(line)
            skipping_continuation = False
            continue
        if stripped.startswith("$"):
            filtered.append(line)
            continue
        if skipping_continuation and _is_continuation_line(line):
            continue
        card = _normalize_card_name(line)
        if any(card.startswith(prefix) for prefix in skip_prefixes):
            skipping_continuation = True
            continue
        if card == "PARAM":
            pname = _parse_param_name(line)
            if pname in SKIP_SOL200_PARAM_NAMES:
                skipping_continuation = True
                continue
        skipping_continuation = False
        filtered.append(line)
    return filtered


def convert_to_sol200(
    *,
    input_bdf: str,
    output_bdf: str,
    output_design_bdf: Optional[str] = None,
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    deck_mode = _resolve_sol200_deck_mode(settings)
    resolved_design_bdf = output_design_bdf
    if deck_mode == "include" and not resolved_design_bdf:
        resolved_design_bdf = str(Path(output_bdf).with_name("design_model.bdf"))
    payload = build_sol200_lines(
        input_bdf=input_bdf,
        parameters=parameters,
        responses=responses,
        settings=settings,
        output_bdf=output_bdf,
        output_design_bdf=resolved_design_bdf,
    )
    Path(output_bdf).parent.mkdir(parents=True, exist_ok=True)
    write_lines(output_bdf, payload["output_lines"])
    if deck_mode == "include" and resolved_design_bdf:
        Path(resolved_design_bdf).parent.mkdir(parents=True, exist_ok=True)
        write_lines(resolved_design_bdf, payload["design_lines"])
        payload["output_design_bdf"] = str(Path(resolved_design_bdf).resolve())
    return payload
