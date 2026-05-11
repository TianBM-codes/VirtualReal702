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
    return "DESVAR   {idx:<8}{name:<8}{xinit:>8}{xlb:>8}{xub:>8}{delxv:>8}".format(
        idx=str(index),
        name=name[:8],
        xinit=format_float_like_bas(float(initial)),
        xlb=format_float_like_bas(float(lower)) if lower is not None else "",
        xub=format_float_like_bas(float(upper)) if upper is not None else "",
        delxv="",
    ).rstrip()


def _build_parameter_relation_lines(index: int, parameter: Dict[str, Any]) -> List[str]:
    ptype = str(parameter.get("type") or "").upper()
    if ptype == "H":
        property_id = parameter.get("property_id")
        if property_id is None:
            raise ValidationError("H parameter requires property_id", {"parameter": parameter})
        return [
            f"DVPREL1  {index:<8}PSHELL  {int(property_id):<8}4",
            f"         {index:<8}{'1.0':>8}",
        ]
    if ptype == "E":
        material_id = parameter.get("material_id")
        if material_id is None:
            raise ValidationError("E parameter requires material_id", {"parameter": parameter})
        return [
            f"DVMREL1  {index:<8}MAT1    {int(material_id):<8}E",
            f"         {index:<8}{'1.0':>8}",
        ]
    if ptype == "RHO":
        material_id = parameter.get("material_id")
        if material_id is None:
            raise ValidationError("RHO parameter requires material_id", {"parameter": parameter})
        return [
            f"DVMREL1  {index:<8}MAT1    {int(material_id):<8}RHO",
            f"         {index:<8}{'1.0':>8}",
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
        f"DRESP1   {index:<8}{name[:8]:<8}FREQ                    {int(mode_number)}",
        f"DCONSTR  1       {index:<8}{'1E30':>8}{'1E30':>8}",
    ]


def build_sol200_controls(settings: Optional[Dict[str, Any]] = None) -> List[str]:
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

    return [
        "SOL 200",
        "CEND",
        f"METHOD = {eigrl['sid']}",
        displacement_line,
        "DESSUB = 1",
        "DSAPRT(NOPRINT,EXPORT,END=SENS)",
        "",
        "SUBCASE 1",
        "  ANALYSIS = MODES",
        "",
        "BEGIN BULK",
        f"PARAM   POST          {post}",
        "PARAM   GRDPNT         0",
        f"PARAM   K6ROT   {k6rot_text}",
        f"PARAM   COUPMASS      {coupmass}",
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
    ]


def build_sol200_lines(
    *,
    input_bdf: str,
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    settings = _normalize_sol200_settings(settings)
    if not parameters:
        raise ValidationError("SOL200 parameters are required", {"parameters": parameters})
    if not responses:
        raise ValidationError("SOL200 responses are required", {"responses": responses})

    lines = read_lines(input_bdf)
    _, bulk_lines = split_bdf(lines)

    control_lines = build_sol200_controls(settings)
    desvar_lines: List[str] = []
    relation_lines: List[str] = []
    response_lines: List[str] = []

    for index, parameter in enumerate(parameters, start=1):
        desvar_lines.append(_format_desvar_line(index, parameter))
        relation_lines.extend(_build_parameter_relation_lines(index, parameter))

    for index, response in enumerate(responses, start=1):
        response_lines.extend(_build_response_lines(index, response))

    filtered_bulk_lines = filter_sol200_bulk_lines(bulk_lines)
    output_lines = control_lines + desvar_lines + relation_lines + response_lines + filtered_bulk_lines
    return {
        "control_lines": control_lines,
        "desvar_lines": desvar_lines,
        "relation_lines": relation_lines,
        "response_lines": response_lines,
        "filtered_bulk_lines": filtered_bulk_lines,
        "output_lines": output_lines,
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
    parameters: List[Dict[str, Any]],
    responses: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = build_sol200_lines(
        input_bdf=input_bdf,
        parameters=parameters,
        responses=responses,
        settings=settings,
    )
    Path(output_bdf).parent.mkdir(parents=True, exist_ok=True)
    write_lines(output_bdf, payload["output_lines"])
    return payload
