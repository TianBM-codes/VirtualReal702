from __future__ import annotations

from typing import Dict, List


def extract_parameter_definition_rows(model) -> List[dict]:
    rows: List[dict] = []
    design_order = {item.name: int(item.order) for item in getattr(model, "design_parameters", [])}
    all_names = set(getattr(model, "parameters", {}).keys()) | set(design_order.keys())

    for name in sorted(all_names):
        definition = getattr(model, "parameters", {}).get(name)
        rows.append({
            "parameter_name": str(name),
            "expression": getattr(definition, "expression", None),
            "scalar_value": getattr(definition, "scalar_value", None),
            "is_design_parameter": str(name) in design_order,
            "design_order": design_order.get(str(name)),
            "extra_json": {
                "referenced_parameters": list(getattr(definition, "referenced_parameters", []) or []),
            },
        })
    return rows


def extract_parameter_target_rows(model) -> List[dict]:
    rows: List[dict] = []
    for part_name, part in sorted(model.parts.items()):
        for sec_idx, section in enumerate(getattr(part, "sections", []) or []):
            base = {
                "target_type": "cell",
                "set_name": str(section.elset_name or ""),
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": str(part_name),
                "source_keyword": f"{str(section.section_type).upper()} SECTION",
            }

            if getattr(section, "thickness_parameter", None):
                rows.append({
                    **base,
                    "parameter_name": str(section.thickness_parameter),
                    "source_path": f"parts/{part_name}/sections/{sec_idx}/thickness",
                    "component_name": "THICKNESS",
                    "extra_json": {
                        "material_name": section.material_name,
                        "thickness_expression": getattr(section, "thickness_expression", None),
                    },
                })

            dim_parameters = list(section.extra.get("dim_parameters", []) or [])
            dim_expressions = list(section.extra.get("dim_expressions", []) or [])
            for dim_idx, parameter_name in enumerate(dim_parameters, start=1):
                if not parameter_name:
                    continue
                expr = dim_expressions[dim_idx - 1] if dim_idx - 1 < len(dim_expressions) else None
                rows.append({
                    **base,
                    "parameter_name": str(parameter_name),
                    "source_path": f"parts/{part_name}/sections/{sec_idx}/dims/{dim_idx - 1}",
                    "component_name": f"DIM{dim_idx}",
                    "extra_json": {
                        "material_name": section.material_name,
                        "section_shape": section.extra.get("section_shape"),
                        "expression": expr,
                    },
                })
    return rows


def build_parameter_target_map(model) -> Dict[str, List[dict]]:
    mapping: Dict[str, List[dict]] = {}
    for row in extract_parameter_target_rows(model):
        mapping.setdefault(str(row["parameter_name"]), []).append(row)
    return mapping


def extract_design_response_rows(model) -> List[dict]:
    rows: List[dict] = []
    for resp_idx, response in enumerate(getattr(model, "design_responses", []) or [], start=1):
        for req_idx, request in enumerate(getattr(response, "requests", []) or [], start=1):
            rows.append({
                "response_no": resp_idx,
                "request_no": req_idx,
                "step_name": response.step_name,
                "frequency": int(response.frequency),
                "region_type": str(request.region_type).upper(),
                "set_name": str(request.set_name or ""),
                "variables": list(request.variables or []),
                "extra_json": dict(getattr(response, "extra", {}) or {}),
            })
    return rows
