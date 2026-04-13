from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from inp import parse_inp
from inp.lexer import KeywordBlock, tokenize


SECTION_KEYWORDS = {
    "SOLID SECTION",
    "SHELL SECTION",
    "BEAM SECTION",
    "MEMBRANE SECTION",
}

MATERIAL_SUB_KEYWORDS = {
    "ELASTIC",
    "PLASTIC",
    "DENSITY",
    "CONDUCTIVITY",
    "EXPANSION",
    "SPECIFIC HEAT",
    "HYPERELASTIC",
    "HYPERFOAM",
    "DAMAGE INITIATION",
    "DAMAGE EVOLUTION",
    "DAMAGE STABILIZATION",
    "CREEP",
    "VISCOUS",
    "RATE DEPENDENT",
    "CYCLIC HARDENING",
    "DRUCKER PRAGER",
    "DRUCKER PRAGER HARDENING",
    "CAP PLASTICITY",
    "MOHR COULOMB PLASTICITY",
    "CONCRETE DAMAGED PLASTICITY",
    "CONCRETE TENSION STIFFENING",
    "CONCRETE COMPRESSION HARDENING",
    "BRITTLE CRACKING",
    "VISCOELASTIC",
    "LATENT HEAT",
    "USER MATERIAL",
    "CONNECTOR BEHAVIOR",
    "CONNECTOR ELASTICITY",
}

PARAMETER_KINDS = ("thickness", "elastic_modulus", "density")
MATERIAL_PARAMETER_KINDS = {"elastic_modulus", "density"}
QUANTITY_ALIASES = {
    "H": "thickness",
    "T": "thickness",
    "THICKNESS": "thickness",
    "E": "elastic_modulus",
    "ELASTIC_MODULUS": "elastic_modulus",
    "ELASTICMODULUS": "elastic_modulus",
    "YOUNG": "elastic_modulus",
    "YOUNGS_MODULUS": "elastic_modulus",
    "RHO": "density",
    "DENSITY": "density",
}


@dataclass
class SectionRecord:
    part_name: str
    section_global_index: int
    section_local_index: int
    section_type: str
    section_elset_name: str
    material_name: str
    element_labels: List[int]
    available_parameters: Dict[str, float]


@dataclass
class SelectedCandidate:
    candidate_id: str
    parameter_name: str
    mode: str
    parameter_kind: str
    part_name: str
    section_global_index: int
    section_local_index: int
    section_type: str
    source_elset_name: str
    material_name: str
    element_labels: List[int]
    default_value: float
    plan_order: int


@dataclass
class CandidateBuildSpec:
    candidate: SelectedCandidate
    parameter_name: str
    generated_elset_name: str
    generated_material_name: Optional[str]


class UniqueNameRegistry:
    def __init__(self, existing: Iterable[str]) -> None:
        self._names: Set[str] = {str(name).strip().upper() for name in existing if str(name).strip()}

    def generate(self, prefix: str) -> str:
        base = _sanitize_identifier(prefix)
        if not base:
            base = "AUTO"
        candidate = base[:72]
        counter = 1
        while candidate.upper() in self._names:
            suffix = f"_{counter}"
            candidate = f"{base[: max(1, 72 - len(suffix))]}{suffix}"
            counter += 1
        self._names.add(candidate.upper())
        return candidate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_identifier(text: str) -> str:
    chars = []
    for ch in str(text):
        if ch.isalnum() or ch == "_":
            chars.append(ch.upper())
        else:
            chars.append("_")
    cleaned = "".join(chars).strip("_")
    return cleaned or "AUTO"


def _format_number(value: float) -> str:
    return f"{float(value):.12g}"


def _preferred_parameter_name(parameter_kind: str, candidate_id: str) -> str:
    tag = {
        "thickness": "THK",
        "elastic_modulus": "E",
        "density": "RHO",
    }[parameter_kind]
    return f"P_{tag}_{candidate_id}"


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(items), size):
        yield list(items[idx : idx + size])


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _dump_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_label_tokens(text: str) -> List[int]:
    labels: List[int] = []
    for raw in str(text).replace("\n", ",").split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            step = 1 if end >= start else -1
            labels.extend(range(start, end + step, step))
            continue
        labels.append(int(token))
    return labels


def _load_label_selection(text: Optional[str], filepath: Optional[str]) -> Optional[List[int]]:
    labels: List[int] = []
    if text:
        labels.extend(_parse_label_tokens(text))
    if filepath:
        path = Path(filepath)
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return sorted(set(labels)) if labels else []
        if path.suffix.lower() == ".json":
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload = payload.get("element_labels", [])
            labels.extend(int(item) for item in payload)
        else:
            labels.extend(_parse_label_tokens(raw))
    if not labels:
        return None
    return sorted(set(labels))


def _normalize_parameter_kind(kind: str) -> str:
    token = str(kind or "").strip().upper().replace("-", "_").replace(" ", "_")
    if token in QUANTITY_ALIASES:
        return QUANTITY_ALIASES[token]
    if str(kind or "").strip() in PARAMETER_KINDS:
        return str(kind).strip()
    raise ValueError(f"不支持的物理量/参数类型: {kind}")


def _normalize_parameter_kinds(kinds: Optional[Sequence[str]]) -> Optional[List[str]]:
    if kinds is None:
        return None
    return [_normalize_parameter_kind(item) for item in kinds]


def _candidate_payload(
    candidate_id: str,
    mode: str,
    parameter_kind: str,
    part_name: str,
    section_global_index: int,
    section_local_index: int,
    section_type: str,
    source_elset_name: str,
    material_name: str,
    element_labels: Sequence[int],
    default_value: float,
    parameter_name: Optional[str] = None,
) -> dict:
    labels = [int(value) for value in element_labels]
    return {
        "candidate_id": str(candidate_id),
        "parameter_name": str(parameter_name or _preferred_parameter_name(parameter_kind, str(candidate_id))),
        "mode": str(mode),
        "parameter_kind": str(parameter_kind),
        "part_name": str(part_name),
        "section_global_index": int(section_global_index),
        "section_local_index": int(section_local_index),
        "section_type": str(section_type),
        "source_elset_name": str(source_elset_name),
        "material_name": str(material_name),
        "element_labels": labels,
        "element_count": len(labels),
        "default_value": float(default_value),
    }


def _choose_default_part_name(model, explicit_part_name: Optional[str]) -> str:
    if explicit_part_name:
        return str(explicit_part_name)
    part_names = list(getattr(model, "parts", {}).keys())
    if len(part_names) == 1:
        return str(part_names[0])
    raise ValueError("模型包含多个 Part，显式请求必须提供 part_name。")


def _collect_section_records(model) -> Tuple[List[SectionRecord], Dict[Tuple[str, int], SectionRecord], List[dict]]:
    records: List[SectionRecord] = []
    element_map: Dict[Tuple[str, int], SectionRecord] = {}
    ambiguities: List[dict] = []
    section_global_index = 0

    for part_name, part in model.parts.items():
        for section_local_index, section in enumerate(getattr(part, "sections", []) or []):
            elset = part.elsets.get(section.elset_name)
            element_labels = list(getattr(elset, "elem_labels", []) or [])
            material = model.materials.get(section.material_name)
            available_parameters = _available_parameters_for_section(section, material)

            record = SectionRecord(
                part_name=str(part_name),
                section_global_index=section_global_index,
                section_local_index=section_local_index,
                section_type=str(section.section_type),
                section_elset_name=str(section.elset_name),
                material_name=str(section.material_name),
                element_labels=element_labels,
                available_parameters=available_parameters,
            )
            records.append(record)

            for elem_label in element_labels:
                key = (record.part_name, int(elem_label))
                if key in element_map:
                    ambiguities.append(
                        {
                            "part_name": record.part_name,
                            "element_label": int(elem_label),
                            "first_section_global_index": element_map[key].section_global_index,
                            "second_section_global_index": record.section_global_index,
                        }
                    )
                    continue
                element_map[key] = record

            section_global_index += 1

    return records, element_map, ambiguities


def _available_parameters_for_section(section, material) -> Dict[str, float]:
    values: Dict[str, float] = {}

    if (
        str(getattr(section, "section_type", "")).upper() in {"SHELL", "MEMBRANE"}
        and not bool(getattr(section, "extra", {}).get("composite"))
        and getattr(section, "thickness", None) is not None
    ):
        values["thickness"] = float(section.thickness)

    if material is not None:
        elastic = getattr(material, "elastic", None)
        elastic_rows = list(getattr(elastic, "data", []) or [])
        if elastic_rows and elastic_rows[0]:
            try:
                values["elastic_modulus"] = float(elastic_rows[0][0])
            except (TypeError, ValueError):
                pass

        density_rows = list(getattr(material, "density_data", []) or [])
        if density_rows and density_rows[0]:
            try:
                values["density"] = float(density_rows[0][0])
            except (TypeError, ValueError):
                pass

    return values


def _build_plan_payload(
    inp_path: Path,
    mode: str,
    selected_elements: Optional[List[int]],
    parameter_kinds: Optional[Sequence[str]],
) -> dict:
    model = parse_inp(str(inp_path))
    records, element_map, ambiguities = _collect_section_records(model)
    if ambiguities:
        raise ValueError(
            "存在单元被多个 Section 同时命中的情况，当前脚本无法自动判定唯一归属。"
        )

    selected_set = set(selected_elements or [])
    selected_by_section: Dict[int, List[int]] = defaultdict(list)
    skipped_elements: List[int] = []

    if selected_elements is None:
        for record in records:
            if record.available_parameters and record.element_labels:
                selected_by_section[record.section_global_index].extend(record.element_labels)
    else:
        any_record = next(iter(records), None)
        if any_record is None:
            raise ValueError("INP 中没有可识别的 Section。")
        for element_label in sorted(selected_set):
            hit = None
            for part_name in model.parts.keys():
                hit = element_map.get((str(part_name), int(element_label)))
                if hit is not None:
                    break
            if hit is None:
                skipped_elements.append(int(element_label))
                continue
            selected_by_section[hit.section_global_index].append(int(element_label))

    normalized_kinds = _normalize_parameter_kinds(parameter_kinds) or list(PARAMETER_KINDS)
    kinds_filter = set(normalized_kinds)
    include_global = mode in {"global", "both"}
    include_local = mode in {"local", "both"}

    candidates: List[dict] = []
    candidate_index = 1
    for record in records:
        labels = sorted(set(selected_by_section.get(record.section_global_index, [])))
        if not labels:
            continue
        available = {k: v for k, v in record.available_parameters.items() if k in kinds_filter}
        if not available:
            continue

        if include_global:
            for parameter_kind, default_value in available.items():
                candidates.append(
                    _candidate_payload(
                        candidate_id=f"C{candidate_index:04d}",
                        parameter_kind=parameter_kind,
                        mode="global",
                        part_name=record.part_name,
                        section_global_index=record.section_global_index,
                        section_local_index=record.section_local_index,
                        section_type=record.section_type,
                        source_elset_name=record.section_elset_name,
                        material_name=record.material_name,
                        element_labels=labels,
                        default_value=default_value,
                    )
                )
                candidate_index += 1

        if include_local:
            for parameter_kind, default_value in available.items():
                for elem_label in labels:
                    candidates.append(
                        _candidate_payload(
                            candidate_id=f"C{candidate_index:04d}",
                            parameter_kind=parameter_kind,
                            mode="local",
                            part_name=record.part_name,
                            section_global_index=record.section_global_index,
                            section_local_index=record.section_local_index,
                            section_type=record.section_type,
                            source_elset_name=record.section_elset_name,
                            material_name=record.material_name,
                            element_labels=[int(elem_label)],
                            default_value=default_value,
                        )
                    )
                    candidate_index += 1

    sections_summary = [
        {
            "part_name": record.part_name,
            "section_global_index": record.section_global_index,
            "section_local_index": record.section_local_index,
            "section_type": record.section_type,
            "source_elset_name": record.section_elset_name,
            "material_name": record.material_name,
            "element_count": len(record.element_labels),
            "available_parameters": record.available_parameters,
        }
        for record in records
    ]

    return {
        "tool": "abaqus_sensitivity_tool",
        "version": 1,
        "generated_at": _utc_now_iso(),
        "source_inp": str(inp_path.resolve()),
        "selection": {
            "selected_elements": selected_elements,
            "skipped_elements": skipped_elements,
            "mode": mode,
            "parameter_kinds": sorted(kinds_filter),
        },
        "sections": sections_summary,
        "candidates": candidates,
    }


def _record_by_section_index(records: Sequence[SectionRecord]) -> Dict[int, SectionRecord]:
    return {record.section_global_index: record for record in records}


def _find_records_by_elset(records: Sequence[SectionRecord], elset_name: str, part_name: Optional[str]) -> List[SectionRecord]:
    wanted_elset = str(elset_name).strip().upper()
    wanted_part = str(part_name).strip() if part_name else None
    hits = []
    for record in records:
        if record.section_elset_name.upper() != wanted_elset:
            continue
        if wanted_part and record.part_name != wanted_part:
            continue
        hits.append(record)
    return hits


def _group_explicit_labels_by_section(
    records: Sequence[SectionRecord],
    element_map: Dict[Tuple[str, int], SectionRecord],
    model,
    element_labels: Sequence[int],
    part_name: Optional[str],
) -> Dict[int, List[int]]:
    grouped: Dict[int, List[int]] = defaultdict(list)
    resolved_part_name = _choose_default_part_name(model, part_name)

    for label in element_labels:
        record = element_map.get((resolved_part_name, int(label)))
        if record is None:
            raise ValueError(f"单元 {label} 在 part {resolved_part_name} 中未找到对应 Section。")
        grouped[record.section_global_index].append(int(label))
    return grouped


def _build_explicit_plan_payload(inp_path: Path, request_items: Sequence[dict], metadata: Optional[dict] = None) -> dict:
    model = parse_inp(str(inp_path))
    records, element_map, ambiguities = _collect_section_records(model)
    if ambiguities:
        raise ValueError("源 INP 存在 Section 歧义，无法构建显式参数请求。")

    record_by_index = _record_by_section_index(records)
    candidates: List[dict] = []
    user_counter = 1

    for item_idx, item in enumerate(request_items, start=1):
        quantity = item.get("quantity", item.get("parameter_kind"))
        parameter_kind = _normalize_parameter_kind(str(quantity))
        explicit_part_name = item.get("part_name")
        mode = str(item.get("mode", "global")).strip().lower()
        if mode not in {"global", "local"}:
            raise ValueError(f"请求 #{item_idx} 的 mode 无效: {mode}")

        base_parameter_name = item.get("parameter_name")
        if base_parameter_name:
            base_parameter_name = _sanitize_identifier(str(base_parameter_name))

        grouped: Dict[int, List[int]]
        if item.get("elset_name"):
            hits = _find_records_by_elset(records, str(item["elset_name"]), explicit_part_name)
            if not hits:
                raise ValueError(f"请求 #{item_idx} 指定的 elset 未找到: {item['elset_name']}")
            if len(hits) > 1:
                raise ValueError(f"请求 #{item_idx} 的 elset 命中多个 Part，请补充 part_name: {item['elset_name']}")
            record = hits[0]
            labels = [int(label) for label in item.get("element_labels", record.element_labels)]
            record_label_set = set(record.element_labels)
            unknown = [label for label in labels if label not in record_label_set]
            if unknown:
                raise ValueError(
                    f"请求 #{item_idx} 中有单元不属于 elset {record.section_elset_name}: {unknown}"
                )
            grouped = {record.section_global_index: sorted(set(labels))}
        elif item.get("element_labels"):
            grouped = _group_explicit_labels_by_section(
                records=records,
                element_map=element_map,
                model=model,
                element_labels=[int(label) for label in item["element_labels"]],
                part_name=explicit_part_name,
            )
        else:
            raise ValueError(f"请求 #{item_idx} 必须提供 elset_name 或 element_labels。")

        for section_global_index, grouped_labels in grouped.items():
            record = record_by_index[section_global_index]
            if parameter_kind not in record.available_parameters:
                raise ValueError(
                    f"请求 #{item_idx} 的 Section {record.section_elset_name} 不支持参数 {parameter_kind}。"
                )

            if mode == "global":
                candidate_id = f"USR{user_counter:04d}"
                user_counter += 1
                candidates.append(
                    _candidate_payload(
                        candidate_id=candidate_id,
                        parameter_kind=parameter_kind,
                        mode=mode,
                        part_name=record.part_name,
                        section_global_index=record.section_global_index,
                        section_local_index=record.section_local_index,
                        section_type=record.section_type,
                        source_elset_name=record.section_elset_name,
                        material_name=record.material_name,
                        element_labels=sorted(set(grouped_labels)),
                        default_value=record.available_parameters[parameter_kind],
                        parameter_name=base_parameter_name,
                    )
                )
                continue

            for local_idx, elem_label in enumerate(sorted(set(grouped_labels)), start=1):
                candidate_id = f"USR{user_counter:04d}"
                user_counter += 1
                parameter_name = None
                if base_parameter_name:
                    parameter_name = base_parameter_name if len(grouped_labels) == 1 else f"{base_parameter_name}_{local_idx}"
                candidates.append(
                    _candidate_payload(
                        candidate_id=candidate_id,
                        parameter_kind=parameter_kind,
                        mode=mode,
                        part_name=record.part_name,
                        section_global_index=record.section_global_index,
                        section_local_index=record.section_local_index,
                        section_type=record.section_type,
                        source_elset_name=record.section_elset_name,
                        material_name=record.material_name,
                        element_labels=[int(elem_label)],
                        default_value=record.available_parameters[parameter_kind],
                        parameter_name=parameter_name,
                    )
                )

    return {
        "tool": "abaqus_sensitivity_tool",
        "version": 1,
        "generated_at": _utc_now_iso(),
        "source_inp": str(inp_path.resolve()),
        "selection": {
            "selected_elements": None,
            "skipped_elements": [],
            "mode": "explicit",
            "parameter_kinds": sorted({item["parameter_kind"] for item in candidates}),
        },
        "request_metadata": metadata or {},
        "sections": [
            {
                "part_name": record.part_name,
                "section_global_index": record.section_global_index,
                "section_local_index": record.section_local_index,
                "section_type": record.section_type,
                "source_elset_name": record.section_elset_name,
                "material_name": record.material_name,
                "element_count": len(record.element_labels),
                "available_parameters": record.available_parameters,
            }
            for record in records
        ],
        "candidates": candidates,
    }


def _load_candidate_ids(text: Optional[str], filepath: Optional[str]) -> List[str]:
    ids: List[str] = []
    if text:
        ids.extend(token.strip() for token in text.split(",") if token.strip())
    if filepath:
        path = Path(filepath)
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            if path.suffix.lower() == ".json":
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    payload = payload.get("candidate_ids", [])
                ids.extend(str(item).strip() for item in payload if str(item).strip())
            else:
                ids.extend(token.strip() for token in raw.replace("\n", ",").split(",") if token.strip())
    return ids


def _select_candidates(plan: dict, args) -> List[SelectedCandidate]:
    raw_candidates = list(plan.get("candidates", []) or [])
    by_id = {item["candidate_id"]: item for item in raw_candidates}
    explicit_ids = _load_candidate_ids(args.candidate_ids, args.candidate_file)

    selected_raw: List[dict]
    if explicit_ids:
        missing = [candidate_id for candidate_id in explicit_ids if candidate_id not in by_id]
        if missing:
            raise ValueError(f"候选项不存在: {', '.join(missing)}")
        selected_raw = [by_id[candidate_id] for candidate_id in explicit_ids]
    else:
        filtered = raw_candidates
        if getattr(args, "mode_filter", None):
            filtered = [item for item in filtered if item["mode"] == args.mode_filter]
        if getattr(args, "parameter_kind_filter", None):
            wanted = set(_normalize_parameter_kinds(args.parameter_kind_filter) or [])
            filtered = [item for item in filtered if item["parameter_kind"] in wanted]
        if not filtered:
            raise ValueError("没有候选项命中过滤条件。")
        if not getattr(args, "all_candidates", False) and not getattr(args, "allow_filtered_without_all", False):
            raise ValueError("build 需要明确指定 --all-candidates，或通过 --candidate-ids / --candidate-file 指定候选项。")
        selected_raw = filtered

    selected: List[SelectedCandidate] = []
    for order, item in enumerate(selected_raw):
        selected.append(
            SelectedCandidate(
                candidate_id=str(item["candidate_id"]),
                parameter_name=str(item.get("parameter_name") or _preferred_parameter_name(str(item["parameter_kind"]), str(item["candidate_id"]))),
                mode=str(item["mode"]),
                parameter_kind=str(item["parameter_kind"]),
                part_name=str(item["part_name"]),
                section_global_index=int(item["section_global_index"]),
                section_local_index=int(item["section_local_index"]),
                section_type=str(item["section_type"]),
                source_elset_name=str(item["source_elset_name"]),
                material_name=str(item["material_name"]),
                element_labels=[int(value) for value in item["element_labels"]],
                default_value=float(item["default_value"]),
                plan_order=order,
            )
        )
    return selected


def _apply_conflict_policy(
    candidates: List[SelectedCandidate],
    policy: str,
) -> List[SelectedCandidate]:
    occupied: Dict[Tuple[str, int], str] = {}
    kept: List[SelectedCandidate] = []

    for candidate in candidates:
        overlaps = []
        for elem_label in candidate.element_labels:
            key = (candidate.part_name, int(elem_label))
            if key in occupied:
                overlaps.append((elem_label, occupied[key]))

        if overlaps:
            if policy == "keep-first":
                continue
            details = ", ".join(f"{elem}->{winner}" for elem, winner in overlaps)
            raise ValueError(f"候选项冲突: {candidate.candidate_id} 与已选候选项重复覆盖同一单元: {details}")

        for elem_label in candidate.element_labels:
            occupied[(candidate.part_name, int(elem_label))] = candidate.candidate_id
        kept.append(candidate)

    return kept


def _collect_section_blocks(blocks: Sequence[KeywordBlock]) -> List[int]:
    return [idx for idx, block in enumerate(blocks) if block.keyword in SECTION_KEYWORDS]


def _collect_material_segments(blocks: Sequence[KeywordBlock]) -> Tuple[Dict[int, Tuple[str, int, int]], Dict[str, Tuple[int, int]]]:
    by_start: Dict[int, Tuple[str, int, int]] = {}
    by_name: Dict[str, Tuple[int, int]] = {}
    idx = 0
    while idx < len(blocks):
        block = blocks[idx]
        if block.keyword != "MATERIAL":
            idx += 1
            continue
        name = str(block.params.get("name", "")).strip()
        start = idx
        idx += 1
        while idx < len(blocks) and blocks[idx].keyword in MATERIAL_SUB_KEYWORDS:
            idx += 1
        end = idx
        by_start[start] = (name, start, end)
        if name:
            by_name[name] = (start, end)
    return by_start, by_name


def _find_parameter_insert_index(blocks: Sequence[KeywordBlock]) -> int:
    for idx, block in enumerate(blocks):
        if block.keyword in {"PARAMETER", "DESIGN PARAMETER"}:
            return idx
    idx = 0
    while idx < len(blocks) and blocks[idx].keyword in {"HEADING", "PREPRINT"}:
        idx += 1
    return idx


def _render_block(block: KeywordBlock, params_override: Optional[Dict[str, str]] = None, data_override: Optional[List[str]] = None) -> List[str]:
    params = dict(block.params)
    if params_override:
        params.update(params_override)

    line = f"*{block.keyword}"
    for key, value in params.items():
        if value == "":
            line += f", {str(key).upper()}"
        else:
            line += f", {str(key).upper()}={value}"
    return [line, *(data_override if data_override is not None else list(block.data_lines))]


def _render_explicit_block(keyword: str, params: Dict[str, str], data_lines: List[str]) -> List[str]:
    block = KeywordBlock(
        keyword=keyword,
        params=params,
        data_lines=data_lines,
        source_file="",
        source_line=0,
    )
    return _render_block(block)


def _format_elset_lines(name: str, labels: Sequence[int]) -> List[str]:
    data_lines = []
    for chunk in _chunks([str(int(label)) for label in sorted(labels)], 16):
        data_lines.append(", ".join(chunk))
    return _render_explicit_block("ELSET", {"elset": name}, data_lines)


def _replace_first_csv_token(data_lines: Sequence[str], replacement: str) -> List[str]:
    if not data_lines:
        return [f" {replacement},"]
    updated = list(data_lines)
    parts = updated[0].split(",")
    parts[0] = f" {replacement}"
    updated[0] = ",".join(parts)
    return updated


def _render_parameter_blocks(model, new_parameter_specs: Sequence[CandidateBuildSpec], design_parameter_mode: str) -> List[str]:
    existing_rows: List[str] = []
    for name, definition in getattr(model, "parameters", {}).items():
        expression = getattr(definition, "expression", None)
        scalar_value = getattr(definition, "scalar_value", None)
        if expression:
            existing_rows.append(f"{name}={expression}")
        elif scalar_value is not None:
            existing_rows.append(f"{name}={_format_number(float(scalar_value))}")

    new_rows = [
        f"{spec.parameter_name}={_format_number(spec.candidate.default_value)}"
        for spec in new_parameter_specs
    ]

    design_names = [spec.parameter_name for spec in new_parameter_specs]
    if design_parameter_mode == "merge-existing":
        existing_design = [item.name for item in getattr(model, "design_parameters", []) or []]
        for name in existing_design:
            if name not in design_names:
                design_names.append(name)
    elif design_parameter_mode == "none":
        design_names = []

    lines: List[str] = []
    if existing_rows or new_rows:
        lines.append("*PARAMETER")
        lines.extend(existing_rows)
        lines.extend(new_rows)
    if design_names:
        lines.append("*DESIGN PARAMETER")
        for chunk in _chunks(design_names, 8):
            lines.append(",".join(chunk))
    return lines


def _render_section_variant(
    source_block: KeywordBlock,
    target_elset_name: str,
    parameter_kind: Optional[str],
    parameter_name: Optional[str],
    generated_material_name: Optional[str],
) -> List[str]:
    params_override: Dict[str, str] = {"elset": target_elset_name}
    data_override = list(source_block.data_lines)

    if generated_material_name:
        params_override["material"] = generated_material_name

    if parameter_kind == "thickness":
        if not parameter_name:
            raise ValueError("thickness 参数缺少 parameter_name。")
        data_override = _replace_first_csv_token(data_override, f"<{parameter_name}>")

    return _render_block(source_block, params_override=params_override, data_override=data_override)


def _render_cloned_material_segment(source_segment: Sequence[KeywordBlock], spec: CandidateBuildSpec) -> List[str]:
    lines: List[str] = []
    modified = False

    for idx, block in enumerate(source_segment):
        params_override = {"name": spec.generated_material_name} if idx == 0 else None
        data_override = list(block.data_lines)

        if spec.candidate.parameter_kind == "elastic_modulus" and block.keyword == "ELASTIC" and not modified:
            data_override = _replace_first_csv_token(data_override, f"<{spec.parameter_name}>")
            modified = True
        elif spec.candidate.parameter_kind == "density" and block.keyword == "DENSITY" and not modified:
            data_override = _replace_first_csv_token(data_override, f"<{spec.parameter_name}>")
            modified = True

        lines.extend(_render_block(block, params_override=params_override, data_override=data_override))

    if spec.candidate.parameter_kind in MATERIAL_PARAMETER_KINDS and not modified:
        raise ValueError(
            f"材料 {spec.candidate.material_name} 中未找到可替换的 {spec.candidate.parameter_kind} 关键字块。"
        )
    return lines


def _build_output_from_plan(plan: dict, args) -> Tuple[Path, Path, dict]:
    inp_path = Path(plan["source_inp"]).resolve()
    model = parse_inp(str(inp_path))
    blocks, _ = tokenize(str(inp_path))
    section_block_indices = _collect_section_blocks(blocks)
    records, _, ambiguities = _collect_section_records(model)
    if ambiguities:
        raise ValueError("源 INP 存在 Section 歧义，无法安全重写。")
    if len(section_block_indices) != len(records):
        raise ValueError("Section 解析数量与原始关键字块数量不一致，无法安全重写。")

    selected_candidates = _select_candidates(plan, args)
    selected_candidates = _apply_conflict_policy(selected_candidates, args.conflict_policy)
    if not selected_candidates:
        raise ValueError("没有可构建的候选项。")

    parameter_registry = UniqueNameRegistry(getattr(model, "parameters", {}).keys())
    material_registry = UniqueNameRegistry(getattr(model, "materials", {}).keys())
    elset_registries = {
        str(part_name): UniqueNameRegistry(getattr(part, "elsets", {}).keys())
        for part_name, part in model.parts.items()
    }

    selected_specs: List[CandidateBuildSpec] = []
    selected_by_section: Dict[int, List[CandidateBuildSpec]] = defaultdict(list)
    for candidate in selected_candidates:
        parameter_name = parameter_registry.generate(candidate.parameter_name)
        generated_elset_name = elset_registries[candidate.part_name].generate(f"AUTO_{candidate.candidate_id}")
        generated_material_name = None
        if candidate.parameter_kind in MATERIAL_PARAMETER_KINDS:
            generated_material_name = material_registry.generate(f"AUTO_MAT_{candidate.candidate_id}")

        spec = CandidateBuildSpec(
            candidate=candidate,
            parameter_name=parameter_name,
            generated_elset_name=generated_elset_name,
            generated_material_name=generated_material_name,
        )
        selected_specs.append(spec)
        selected_by_section[candidate.section_global_index].append(spec)

    material_segments_by_start, material_segments_by_name = _collect_material_segments(blocks)
    material_clones_by_name: Dict[str, List[CandidateBuildSpec]] = defaultdict(list)
    for spec in selected_specs:
        if spec.generated_material_name:
            if spec.candidate.material_name not in material_segments_by_name:
                raise ValueError(f"材料 {spec.candidate.material_name} 在原始 INP 中未找到对应块。")
            material_clones_by_name[spec.candidate.material_name].append(spec)

    section_remainder_names: Dict[int, Optional[str]] = {}
    for section_index, specs in selected_by_section.items():
        record = records[section_index]
        selected_set = {label for spec in specs for label in spec.candidate.element_labels}
        remainder = [label for label in record.element_labels if label not in selected_set]
        if remainder:
            section_remainder_names[section_index] = elset_registries[record.part_name].generate(
                f"AUTO_REM_S{section_index + 1}"
            )
        else:
            section_remainder_names[section_index] = None

    parameter_lines = _render_parameter_blocks(model, selected_specs, args.design_parameter_mode)
    parameter_insert_index = _find_parameter_insert_index(blocks)

    section_block_map = {block_index: ordinal for ordinal, block_index in enumerate(section_block_indices)}

    output_lines: List[str] = []
    inserted_parameter_blocks = False
    idx = 0
    while idx < len(blocks):
        if not inserted_parameter_blocks and idx == parameter_insert_index:
            output_lines.extend(parameter_lines)
            inserted_parameter_blocks = True

        block = blocks[idx]
        if block.keyword in {"PARAMETER", "DESIGN PARAMETER"}:
            idx += 1
            continue

        if idx in material_segments_by_start:
            material_name, start, end = material_segments_by_start[idx]
            source_segment = list(blocks[start:end])
            for segment_block in source_segment:
                output_lines.extend(_render_block(segment_block))
            for spec in material_clones_by_name.get(material_name, []):
                output_lines.extend(_render_cloned_material_segment(source_segment, spec))
            idx = end
            continue

        if idx in section_block_map and section_block_map[idx] in selected_by_section:
            section_ordinal = section_block_map[idx]
            record = records[section_ordinal]
            specs = selected_by_section[section_ordinal]
            source_block = blocks[idx]
            selected_set = {label for spec in specs for label in spec.candidate.element_labels}
            remainder = [label for label in record.element_labels if label not in selected_set]

            if remainder:
                remainder_name = section_remainder_names[section_ordinal]
                if remainder_name is None:
                    raise ValueError("内部错误: remainder elset 名称缺失。")
                output_lines.extend(_format_elset_lines(remainder_name, remainder))
                output_lines.extend(
                    _render_section_variant(
                        source_block=source_block,
                        target_elset_name=remainder_name,
                        parameter_kind=None,
                        parameter_name=None,
                        generated_material_name=None,
                    )
                )

            for spec in specs:
                output_lines.extend(_format_elset_lines(spec.generated_elset_name, spec.candidate.element_labels))
                output_lines.extend(
                    _render_section_variant(
                        source_block=source_block,
                        target_elset_name=spec.generated_elset_name,
                        parameter_kind=spec.candidate.parameter_kind,
                        parameter_name=spec.parameter_name,
                        generated_material_name=spec.generated_material_name,
                    )
                )

            idx += 1
            continue

        output_lines.extend(_render_block(block))
        idx += 1

    if not inserted_parameter_blocks:
        output_lines = [*parameter_lines, *output_lines]

    output_path = Path(args.output_inp).resolve()
    manifest_path = Path(args.output_manifest or f"{output_path}.manifest.json").resolve()
    output_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")

    manifest = {
        "tool": "abaqus_sensitivity_tool",
        "version": 1,
        "generated_at": _utc_now_iso(),
        "source_inp": str(inp_path),
        "plan_file": str(Path(args.plan).resolve()),
        "output_inp": str(output_path),
        "design_parameter_mode": args.design_parameter_mode,
        "selected_parameters": [
            {
                "candidate_id": spec.candidate.candidate_id,
                "parameter_name": spec.parameter_name,
                "mode": spec.candidate.mode,
                "parameter_kind": spec.candidate.parameter_kind,
                "part_name": spec.candidate.part_name,
                "section_global_index": spec.candidate.section_global_index,
                "section_local_index": spec.candidate.section_local_index,
                "section_type": spec.candidate.section_type,
                "source_elset_name": spec.candidate.source_elset_name,
                "material_name": spec.candidate.material_name,
                "generated_elset_name": spec.generated_elset_name,
                "generated_material_name": spec.generated_material_name,
                "default_value": spec.candidate.default_value,
                "element_labels": spec.candidate.element_labels,
            }
            for spec in selected_specs
        ],
    }
    _dump_json(manifest_path, manifest)
    return output_path, manifest_path, manifest


def _load_table_rows(path: Path) -> List[dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rows", [])
        if not isinstance(payload, list):
            raise ValueError("JSON 灵敏度表必须是 list 或包含 rows 的 dict。")
        return [dict(item) for item in payload]

    with path.open("r", encoding="utf-8", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        reader = csv.DictReader(handle, dialect=dialect)
        rows: List[dict] = []
        for row in reader:
            rows.append(
                {
                    (str(key).lstrip("\ufeff") if key is not None else key): value
                    for key, value in dict(row).items()
                }
            )
        return rows


def _write_table_rows(path: Path, rows: Sequence[dict]) -> None:
    if path.suffix.lower() == ".json":
        _dump_json(path, {"rows": list(rows)})
        return

    fieldnames: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_rank_source_map(payload: dict) -> Dict[str, dict]:
    if payload.get("selected_parameters"):
        return {
            str(item["parameter_name"]): dict(item)
            for item in payload.get("selected_parameters", []) or []
        }

    if payload.get("candidates"):
        mapping: Dict[str, dict] = {}
        for item in payload.get("candidates", []) or []:
            parameter_name = str(
                item.get("parameter_name")
                or _preferred_parameter_name(str(item["parameter_kind"]), str(item["candidate_id"]))
            )
            mapping[parameter_name] = {
                "candidate_id": item["candidate_id"],
                "parameter_name": parameter_name,
                "parameter_kind": item["parameter_kind"],
                "mode": item["mode"],
                "part_name": item["part_name"],
                "element_labels": item["element_labels"],
            }
        return mapping

    raise ValueError("rank 输入文件既不是 build manifest，也不是 plan.json。")


def _rank_parameters(payload: dict, table_rows: Sequence[dict], parameter_column: str, value_column: str, topn: int, use_absolute: bool) -> List[dict]:
    manifest_map = _build_rank_source_map(payload)
    scored_rows: List[Tuple[float, float, dict, dict]] = []
    for row in table_rows:
        parameter_name = str(row.get(parameter_column, "")).strip()
        if not parameter_name or parameter_name not in manifest_map:
            continue
        raw_value = row.get(value_column)
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            continue
        score = abs(numeric_value) if use_absolute else numeric_value
        scored_rows.append((score, numeric_value, dict(row), manifest_map[parameter_name]))

    scored_rows.sort(key=lambda item: item[0], reverse=True)

    picked_rows: List[dict] = []
    occupied: Set[Tuple[str, int]] = set()
    for score, numeric_value, row, manifest_item in scored_rows:
        element_keys = {
            (str(manifest_item["part_name"]), int(label))
            for label in manifest_item.get("element_labels", []) or []
        }
        if element_keys & occupied:
            continue
        occupied |= element_keys
        picked_rows.append(
            {
                "rank": len(picked_rows) + 1,
                "candidate_id": manifest_item["candidate_id"],
                "parameter_name": manifest_item["parameter_name"],
                "parameter_kind": manifest_item["parameter_kind"],
                "mode": manifest_item["mode"],
                "part_name": manifest_item["part_name"],
                "element_labels": ",".join(str(label) for label in manifest_item.get("element_labels", [])),
                "sensitivity_value": numeric_value,
                "sort_score": score,
            }
        )
        if len(picked_rows) >= topn:
            break

    return picked_rows


def build_explicit_parameter_file(
    inp: str,
    output_inp: str,
    parameters: Sequence[dict],
    output_manifest: Optional[str] = None,
    conflict_policy: str = "error",
    design_parameter_mode: str = "selected",
    request_metadata: Optional[dict] = None,
) -> dict:
    plan = _build_explicit_plan_payload(
        inp_path=Path(inp).resolve(),
        request_items=parameters,
        metadata=request_metadata,
    )
    args = argparse.Namespace(
        plan="__explicit__",
        output_inp=output_inp,
        output_manifest=output_manifest,
        candidate_ids=None,
        candidate_file=None,
        all_candidates=True,
        mode_filter=None,
        parameter_kind_filter=None,
        conflict_policy=conflict_policy,
        design_parameter_mode=design_parameter_mode,
        allow_filtered_without_all=True,
    )
    output_path, manifest_path, manifest = _build_output_from_plan(plan, args)
    return {
        "ok": True,
        "action": "build-explicit",
        "output_inp": str(output_path),
        "output_manifest": str(manifest_path),
        "parameter_count": len(manifest.get("selected_parameters", [])),
        "selected_parameters": manifest.get("selected_parameters", []),
    }


def run_request(payload: dict) -> dict:
    action = str(payload.get("action", "")).strip().lower()
    if action == "plan":
        plan = _build_plan_payload(
            inp_path=Path(payload["inp"]).resolve(),
            mode=str(payload.get("mode", "both")).lower(),
            selected_elements=(
                [int(item) for item in payload.get("element_labels", [])]
                if payload.get("element_labels") is not None
                else None
            ),
            parameter_kinds=payload.get("parameter_kinds"),
        )
        output = payload.get("output")
        if output:
            _dump_json(Path(output).resolve(), plan)
        return {
            "ok": True,
            "action": "plan",
            "candidate_count": len(plan.get("candidates", [])),
            "output": str(Path(output).resolve()) if output else None,
            "plan": plan if not output else None,
        }

    if action == "build-explicit":
        return build_explicit_parameter_file(
            inp=payload["inp"],
            output_inp=payload["output_inp"],
            parameters=payload.get("parameters", []),
            output_manifest=payload.get("output_manifest"),
            conflict_policy=str(payload.get("conflict_policy", "error")),
            design_parameter_mode=str(payload.get("design_parameter_mode", "selected")),
            request_metadata={k: v for k, v in payload.items() if k not in {
                "action", "inp", "output_inp", "output_manifest",
                "parameters", "conflict_policy", "design_parameter_mode",
            }},
        )

    if action == "rank":
        source_payload = _load_json(Path(payload["manifest"]).resolve())
        table_rows = _load_table_rows(Path(payload["table"]).resolve())
        ranked_rows = _rank_parameters(
            payload=source_payload,
            table_rows=table_rows,
            parameter_column=str(payload.get("parameter_column", "parameter_name")),
            value_column=str(payload.get("value_column", "sensitivity")),
            topn=int(payload.get("topn", 50)),
            use_absolute=not bool(payload.get("raw_order", False)),
        )
        output = Path(payload["output"]).resolve()
        _write_table_rows(output, ranked_rows)
        return {
            "ok": True,
            "action": "rank",
            "output": str(output),
            "selected_count": len(ranked_rows),
        }

    raise ValueError(f"不支持的 action: {action}")


def cmd_plan(args) -> int:
    inp_path = Path(args.inp).resolve()
    selected_elements = _load_label_selection(args.elements, args.elements_file)
    payload = _build_plan_payload(
        inp_path=inp_path,
        mode=args.mode,
        selected_elements=selected_elements,
        parameter_kinds=args.parameter_kind,
    )
    output_path = Path(args.output or f"{inp_path}.plan.json").resolve()
    _dump_json(output_path, payload)
    print(f"plan written: {output_path}")
    print(f"candidate count: {len(payload.get('candidates', []))}")
    return 0


def cmd_build_explicit(args) -> int:
    spec = _load_json(Path(args.spec).resolve())
    result = build_explicit_parameter_file(
        inp=args.inp,
        output_inp=args.output_inp,
        parameters=spec.get("parameters", []),
        output_manifest=args.output_manifest,
        conflict_policy=args.conflict_policy,
        design_parameter_mode=args.design_parameter_mode,
        request_metadata={"spec": str(Path(args.spec).resolve())},
    )
    print(f"inp written: {result['output_inp']}")
    print(f"manifest written: {result['output_manifest']}")
    print(f"parameter count: {result['parameter_count']}")
    return 0


def cmd_build(args) -> int:
    plan = _load_json(Path(args.plan).resolve())
    output_path, manifest_path, manifest = _build_output_from_plan(plan, args)
    print(f"inp written: {output_path}")
    print(f"manifest written: {manifest_path}")
    print(f"parameter count: {len(manifest.get('selected_parameters', []))}")
    return 0


def cmd_rank(args) -> int:
    manifest = _load_json(Path(args.manifest).resolve())
    table_rows = _load_table_rows(Path(args.table).resolve())
    ranked_rows = _rank_parameters(
        payload=manifest,
        table_rows=table_rows,
        parameter_column=args.parameter_column,
        value_column=args.value_column,
        topn=args.topn,
        use_absolute=not args.raw_order,
    )
    output_path = Path(args.output).resolve()
    _write_table_rows(output_path, ranked_rows)
    print(f"ranked rows written: {output_path}")
    print(f"selected count: {len(ranked_rows)}")
    return 0


def cmd_request(args) -> int:
    if args.input == "-":
        payload = json.loads(sys.stdin.read())
    else:
        payload = _load_json(Path(args.input).resolve())
    result = run_request(payload)
    if args.output:
        _dump_json(Path(args.output).resolve(), result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Abaqus INP sensitivity/model-modification helper based on the local inp parser."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="按原始 Section 自动分组并输出候选参数计划。")
    plan_parser.add_argument("--inp", required=True, help="源 INP 文件路径。")
    plan_parser.add_argument("--output", help="输出 plan.json 路径。默认写到 <inp>.plan.json。")
    plan_parser.add_argument(
        "--mode",
        choices=("global", "local", "both"),
        default="both",
        help="候选生成模式。默认 both。",
    )
    plan_parser.add_argument("--elements", help="单元标签列表，支持 1,2,10-20。默认不填表示所有可参数化单元。")
    plan_parser.add_argument("--elements-file", help="单元标签文件，支持 txt/json。")
    plan_parser.add_argument(
        "--parameter-kind",
        action="append",
        choices=PARAMETER_KINDS,
        help="只生成指定类型候选。可重复传入。",
    )
    plan_parser.set_defaults(func=cmd_plan)

    build_parser_cmd = subparsers.add_parser("build", help="根据计划文件生成新的参数化 INP。")
    build_parser_cmd.add_argument("--plan", required=True, help="plan.json 路径。")
    build_parser_cmd.add_argument("--output-inp", required=True, help="输出 INP 路径。")
    build_parser_cmd.add_argument("--output-manifest", help="输出 manifest.json 路径。默认跟随 output-inp。")
    build_parser_cmd.add_argument("--candidate-ids", help="候选 ID 列表，逗号分隔。")
    build_parser_cmd.add_argument("--candidate-file", help="候选 ID 文件，支持 txt/json。")
    build_parser_cmd.add_argument("--all-candidates", action="store_true", help="构建当前过滤条件命中的全部候选项。")
    build_parser_cmd.add_argument("--mode-filter", choices=("global", "local"), help="只取某种模式候选。")
    build_parser_cmd.add_argument(
        "--parameter-kind-filter",
        action="append",
        choices=PARAMETER_KINDS,
        help="只取某种参数类型候选。可重复传入。",
    )
    build_parser_cmd.add_argument(
        "--conflict-policy",
        choices=("error", "keep-first"),
        default="error",
        help="同单元多参数冲突处理策略。默认 error。",
    )
    build_parser_cmd.add_argument(
        "--design-parameter-mode",
        choices=("selected", "merge-existing", "none"),
        default="selected",
        help="输出 *DESIGN PARAMETER 的策略。默认只写本次选中的新参数。",
    )
    build_parser_cmd.set_defaults(func=cmd_build)

    rank_parser = subparsers.add_parser("rank", help="对灵敏度表做 Top-N 排序并去除同单元多参数冲突。")
    rank_parser.add_argument("--manifest", required=True, help="build 生成的 manifest.json，或 plan.json。")
    rank_parser.add_argument("--table", required=True, help="灵敏度表，支持 csv/tsv/json。")
    rank_parser.add_argument("--output", required=True, help="输出结果文件，支持 csv/json。")
    rank_parser.add_argument("--topn", type=int, default=50, help="保留的 Top-N 数量。默认 50。")
    rank_parser.add_argument("--parameter-column", default="parameter_name", help="参数名列名。默认 parameter_name。")
    rank_parser.add_argument("--value-column", default="sensitivity", help="灵敏度值列名。默认 sensitivity。")
    rank_parser.add_argument("--raw-order", action="store_true", help="按原始值降序，而不是按绝对值降序。")
    rank_parser.set_defaults(func=cmd_rank)

    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Abaqus INP sensitivity/model-modification helper based on the local inp parser."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="按原始 Section 自动分组并输出候选参数计划。")
    plan_parser.add_argument("--inp", required=True, help="源 INP 文件路径。")
    plan_parser.add_argument("--output", help="输出 plan.json 路径。默认写到 <inp>.plan.json。")
    plan_parser.add_argument(
        "--mode",
        choices=("global", "local", "both"),
        default="both",
        help="候选生成模式。默认 both。",
    )
    plan_parser.add_argument("--elements", help="单元标签列表，支持 1,2,10-20。默认不填表示所有可参数化单元。")
    plan_parser.add_argument("--elements-file", help="单元标签文件，支持 txt/json。")
    plan_parser.add_argument(
        "--parameter-kind",
        action="append",
        help="只生成指定类型候选。支持 thickness/E/H/density/RHO，可重复传入。",
    )
    plan_parser.set_defaults(func=cmd_plan)

    build_explicit_parser = subparsers.add_parser("build-explicit", help="直接按显式参数规格生成新的参数化 INP。")
    build_explicit_parser.add_argument("--inp", required=True, help="源 INP 文件路径。")
    build_explicit_parser.add_argument("--spec", required=True, help="显式参数 JSON 规格文件。")
    build_explicit_parser.add_argument("--output-inp", required=True, help="输出 INP 路径。")
    build_explicit_parser.add_argument("--output-manifest", help="输出 manifest.json 路径。")
    build_explicit_parser.add_argument(
        "--conflict-policy",
        choices=("error", "keep-first"),
        default="error",
        help="同单元多参数冲突处理策略。默认 error。",
    )
    build_explicit_parser.add_argument(
        "--design-parameter-mode",
        choices=("selected", "merge-existing", "none"),
        default="selected",
        help="输出 *DESIGN PARAMETER 的策略。默认只写本次选中的新参数。",
    )
    build_explicit_parser.set_defaults(func=cmd_build_explicit)

    build_parser_cmd = subparsers.add_parser("build", help="根据计划文件生成新的参数化 INP。")
    build_parser_cmd.add_argument("--plan", required=True, help="plan.json 路径。")
    build_parser_cmd.add_argument("--output-inp", required=True, help="输出 INP 路径。")
    build_parser_cmd.add_argument("--output-manifest", help="输出 manifest.json 路径。默认跟随 output-inp。")
    build_parser_cmd.add_argument("--candidate-ids", help="候选 ID 列表，逗号分隔。")
    build_parser_cmd.add_argument("--candidate-file", help="候选 ID 文件，支持 txt/json。")
    build_parser_cmd.add_argument("--all-candidates", action="store_true", help="构建当前过滤条件命中的全部候选项。")
    build_parser_cmd.add_argument("--mode-filter", choices=("global", "local"), help="只取某种模式候选。")
    build_parser_cmd.add_argument(
        "--parameter-kind-filter",
        action="append",
        help="只取某种参数类型候选。支持 thickness/E/H/density/RHO，可重复传入。",
    )
    build_parser_cmd.add_argument(
        "--conflict-policy",
        choices=("error", "keep-first"),
        default="error",
        help="同单元多参数冲突处理策略。默认 error。",
    )
    build_parser_cmd.add_argument(
        "--design-parameter-mode",
        choices=("selected", "merge-existing", "none"),
        default="selected",
        help="输出 *DESIGN PARAMETER 的策略。默认只写本次选中的新参数。",
    )
    build_parser_cmd.set_defaults(func=cmd_build)

    rank_parser = subparsers.add_parser("rank", help="对灵敏度表做 Top-N 排序并去除同单元多参数冲突。")
    rank_parser.add_argument("--manifest", required=True, help="build 生成的 manifest.json，或 plan.json。")
    rank_parser.add_argument("--table", required=True, help="灵敏度表，支持 csv/tsv/json。")
    rank_parser.add_argument("--output", required=True, help="输出结果文件，支持 csv/json。")
    rank_parser.add_argument("--topn", type=int, default=50, help="保留的 Top-N 数量。默认 50。")
    rank_parser.add_argument("--parameter-column", default="parameter_name", help="参数名列名。默认 parameter_name。")
    rank_parser.add_argument("--value-column", default="sensitivity", help="灵敏度值列名。默认 sensitivity。")
    rank_parser.add_argument("--raw-order", action="store_true", help="按原始值降序，而不是按绝对值降序。")
    rank_parser.set_defaults(func=cmd_rank)

    request_parser = subparsers.add_parser("request", help="统一 JSON 请求入口，适合外层服务端调用。")
    request_parser.add_argument("--input", required=True, help="请求 JSON 文件路径，传 - 表示从 stdin 读取。")
    request_parser.add_argument("--output", help="结果 JSON 文件路径；不填则打印到 stdout。")
    request_parser.set_defaults(func=cmd_request)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
