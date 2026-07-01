#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import json
import re
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple, Set
import argparse


# =========================================================
# 1. Input / Output
# =========================================================
MAIN_INP = r"testmodel.inp"
JSON_CONFIG = r"AL_config.json"

OVERWRITE_MAIN_INP = False
UPDATED_MAIN_INP = r"testmodel_dsa.inp"

DEFAULT_INCLUDE_FILE = r"include.inp"
COMMENT_EMPTY_SECTION = True
ROOT_SCOPE_TYPE = "ROOT"
PART_SCOPE_TYPE = "PART"
ASSEMBLY_SCOPE_TYPE = "ASSEMBLY"
ROOT_SCOPE_NAME = "__ROOT__"
AUTO_COMMENT_PREFIX = "DSA_AUTO"

SECTION_KEYWORDS = (
    "*SHELL SECTION",
    "*SHELL GENERAL SECTION",
)

# 输出 set 时，每行最多几个离散编号
IDS_PER_LINE = 10

# 仅当连续段长度 >= 3 时，才用 GENERATE
MIN_GENERATE_COUNT = 3

# formulation 固定 TOTAL，不再从 JSON 读取
FIXED_DSA_FORMULATION = "TOTAL"


# =========================================================
# Basic helpers
# =========================================================
def expand_id_specs(values: List[Any], field_name: str = "elements") -> List[int]:
    """
    支持以下写法：
    1) 单个整数: 101
    2) 字符串数字: "101"
    3) 范围对象:
       {"start": 200, "stop": 260}
       {"start": 300, "stop": 350, "step": 5}
    """
    result: List[int] = []

    for idx, v in enumerate(values, 1):
        if isinstance(v, int):
            result.append(v)
            continue

        if isinstance(v, str):
            s = v.strip()
            if re.fullmatch(r"-?\d+", s):
                result.append(int(s))
                continue
            raise ValueError(
                f"{field_name}[{idx}] invalid string value: {v!r}. "
                f"Only plain integer strings are allowed."
            )

        if isinstance(v, dict):
            if "start" not in v or "stop" not in v:
                raise ValueError(
                    f"{field_name}[{idx}] range object must contain 'start' and 'stop'."
                )

            try:
                start = int(v["start"])
                stop = int(v["stop"])
                step = int(v.get("step", 1))
            except Exception:
                raise ValueError(
                    f"{field_name}[{idx}] range object has non-integer values: {v}"
                )

            if step == 0:
                raise ValueError(f"{field_name}[{idx}] range step cannot be 0.")

            if step > 0:
                if start > stop:
                    raise ValueError(
                        f"{field_name}[{idx}] invalid ascending range: {v}"
                    )
                cur = start
                while cur <= stop:
                    result.append(cur)
                    cur += step
            else:
                if start < stop:
                    raise ValueError(
                        f"{field_name}[{idx}] invalid descending range: {v}"
                    )
                cur = start
                while cur >= stop:
                    result.append(cur)
                    cur += step

            continue

        raise ValueError(
            f"{field_name}[{idx}] unsupported item type: {type(v).__name__}, value={v!r}"
        )

    return sorted(set(result))


def parse_param_value(header_line: str, key: str) -> Optional[str]:
    m = re.search(rf"{re.escape(key)}\s*=\s*([^,\s]+)", header_line, re.I)
    return m.group(1) if m else None


def format_id_lines(ids: List[int], per_line: int = IDS_PER_LINE) -> List[str]:
    ids = sorted(set(int(x) for x in ids))
    out = []
    for i in range(0, len(ids), per_line):
        out.append(", ".join(str(x) for x in ids[i:i + per_line]))
    return out


def infer_section_keyword_from_header(header: str) -> str:
    hu = header.strip().upper()
    for kw in SECTION_KEYWORDS:
        if hu.startswith(kw):
            return kw
    return "*SHELL SECTION"


def extract_shell_section_thickness(data_lines: List[str]) -> Optional[str]:
    """
    从原始 *SHELL SECTION / *SHELL GENERAL SECTION 数据行里提取壳厚度。
    默认取第一条非空、非注释数据行的第一个字段。

    例如:
        1.2
        1.2, 5
        <t_old>

    返回:
        "1.2" 或 "<t_old>" 等字符串；如果找不到则返回 None。
    """
    for line in data_lines:
        s = line.strip()
        if not s or s.startswith("**"):
            continue

        parts = [p.strip() for p in s.split(",") if p.strip()]
        if parts:
            return parts[0]

    return None


def remove_trailing_comma(s: str) -> str:
    return s[:-1] if s.endswith(",") else s


def remove_blank_lines(lines: List[str]) -> List[str]:
    return [line for line in lines if line.strip() != ""]


def make_auto_response_set_name(index: int) -> str:
    return f"RSP_ESET_{index}"


def make_auto_response_node_set_name(index: int) -> str:
    return f"RSP_NSET_{index}"


def make_section_sub_set_name(base_name: str, mother_set: str, index: int) -> str:
    safe_mother = re.sub(r"[^A-Za-z0-9_]", "_", mother_set)
    return f"{base_name}__SEC_{index}_{safe_mother}"


def make_scope_key(scope_type: str, scope_name: Optional[str]) -> Tuple[str, str]:
    normalized_type = str(scope_type or ROOT_SCOPE_TYPE).strip().upper() or ROOT_SCOPE_TYPE
    normalized_name = str(scope_name or "").strip()
    if normalized_type == ROOT_SCOPE_TYPE:
        return ROOT_SCOPE_TYPE, ROOT_SCOPE_NAME
    return normalized_type, normalized_name


def scope_comment(scope_type: str, scope_name: Optional[str]) -> str:
    _, resolved_name = make_scope_key(scope_type, scope_name)
    if scope_type == ROOT_SCOPE_TYPE:
        return "ROOT"
    return f"{scope_type}:{resolved_name}"


def safe_include_fragment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    return text or "scope"


# =========================================================
# NEW: compress ids for GENERATE writing
# =========================================================
def compress_ids_to_runs(
    ids: List[int],
    min_generate_count: int = MIN_GENERATE_COUNT
) -> Tuple[List[Tuple[int, int, int]], List[int]]:
    """
    将一组整数压缩成若干等步长连续段。
    返回:
      generate_runs: [(start, stop, step), ...]
      singles:       [离散编号]
    规则:
      - 只有长度 >= min_generate_count 的段才使用 GENERATE
      - 其余保留为 singles
    """
    arr = sorted(set(int(x) for x in ids))
    if not arr:
        return [], []

    generate_runs: List[Tuple[int, int, int]] = []
    singles: List[int] = []

    n = len(arr)
    i = 0

    while i < n:
        if i == n - 1:
            singles.append(arr[i])
            i += 1
            continue

        step = arr[i + 1] - arr[i]
        if step == 0:
            singles.append(arr[i])
            i += 1
            continue

        j = i + 1
        while j < n and arr[j] - arr[j - 1] == step:
            j += 1

        run = arr[i:j]
        if len(run) >= min_generate_count:
            generate_runs.append((run[0], run[-1], step))
        else:
            singles.extend(run)

        i = j

    singles = sorted(set(singles))
    return generate_runs, singles


def build_set_block(
    keyword: str,
    set_name: str,
    ids: List[int],
    per_line: int = IDS_PER_LINE,
    extra_options: Optional[List[str]] = None,
) -> List[str]:
    """
    自动生成 *ELSET / *NSET 块。
    若存在连续编号段，则优先写成 GENERATE。
    其余离散编号再补普通定义。
    允许同名 set 多次定义，Abaqus 会并入。
    """
    ids = sorted(set(int(x) for x in ids))
    if not ids:
        return [f"** {keyword} {set_name} is empty"]

    generate_runs, singles = compress_ids_to_runs(ids)
    option_suffix = ""
    if extra_options:
        filtered_options = [str(item).strip() for item in extra_options if str(item).strip()]
        if filtered_options:
            option_suffix = ", " + ", ".join(filtered_options)

    lines: List[str] = []

    for start, stop, step in generate_runs:
        lines.append(f"{keyword}, {keyword[1:]}={set_name}{option_suffix}, GENERATE")
        lines.append(f"{start}, {stop}, {step}")

    if singles:
        lines.append(f"{keyword}, {keyword[1:]}={set_name}{option_suffix}")
        lines.extend(format_id_lines(singles, per_line=per_line))

    return lines


# =========================================================
# JSON loading
# =========================================================
def load_config(json_path: Path) -> Dict[str, Any]:
    if not json_path.exists():
        raise FileNotFoundError(f"JSON config not found: {json_path}")

    raw_text = json_path.read_text(encoding="utf-8-sig").strip()
    if not raw_text:
        raise ValueError(f"JSON config is empty: {json_path}")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON file: {json_path}, line={e.lineno}, col={e.colno}, msg={e.msg}"
        ) from e

    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object.")

    data.setdefault("include_file", DEFAULT_INCLUDE_FILE)
    data.setdefault("main_output", UPDATED_MAIN_INP)
    data.setdefault("element_sets", [])
    data.setdefault("node_sets", [])
    data.setdefault("responses", [])

    if not isinstance(data["element_sets"], list):
        raise ValueError("element_sets must be a list.")
    if not isinstance(data["node_sets"], list):
        raise ValueError("node_sets must be a list.")
    if not isinstance(data["responses"], list):
        raise ValueError("responses must be a list.")

    for idx, item in enumerate(data["element_sets"], 1):
        # material 不再要求；value 允许省略。
        # 如果 value 省略，后续会从原 inp 中该单元所在的原始 shell section 厚度自动继承。
        for key in ("set_name", "elements", "parameter"):
            if key not in item:
                raise ValueError(f"element_sets[{idx}] missing key: {key}")

        item["set_name"] = str(item["set_name"]).strip()
        item["parameter"] = str(item["parameter"]).strip()
        item["elements"] = expand_id_specs(
            item["elements"],
            field_name=f"element_sets[{idx}].elements"
        )

        if not item["set_name"]:
            raise ValueError(f"element_sets[{idx}].set_name is empty")
        if not item["parameter"]:
            raise ValueError(f"element_sets[{idx}].parameter is empty")
        if not item["elements"]:
            raise ValueError(f"element_sets[{idx}].elements is empty")

    for idx, item in enumerate(data["node_sets"], 1):
        for key in ("set_name", "nodes"):
            if key not in item:
                raise ValueError(f"node_sets[{idx}] missing key: {key}")

        item["set_name"] = str(item["set_name"]).strip()
        item["nodes"] = expand_id_specs(
            item["nodes"],
            field_name=f"node_sets[{idx}].nodes"
        )

        if not item["set_name"]:
            raise ValueError(f"node_sets[{idx}].set_name is empty")
        if not item["nodes"]:
            raise ValueError(f"node_sets[{idx}].nodes is empty")

    used_response_set_names: Set[str] = set()

    for idx, item in enumerate(data["responses"], 1):
        for key in ("type", "variables"):
            if key not in item:
                raise ValueError(f"responses[{idx}] missing key: {key}")

        item["type"] = str(item["type"]).strip().lower()

        if item["type"] not in ("node", "element"):
            raise ValueError(
                f"responses[{idx}].type only supports 'node' or 'element', got {item['type']}"
            )

        if not isinstance(item["variables"], list) or not item["variables"]:
            raise ValueError(f"responses[{idx}].variables must be a non-empty list")

        item["variables"] = [str(v).strip() for v in item["variables"] if str(v).strip()]
        if not item["variables"]:
            raise ValueError(f"responses[{idx}].variables cannot be empty")

        if item["type"] == "node":
            has_set = "set" in item and str(item.get("set", "")).strip() != ""
            has_nodes = "nodes" in item

            if has_set and has_nodes:
                raise ValueError(
                    f"responses[{idx}] node response cannot contain both 'set' and 'nodes'"
                )

            if not has_set and not has_nodes:
                raise ValueError(
                    f"responses[{idx}] node response must contain either 'set' or 'nodes'"
                )

            if has_set:
                item["set"] = str(item["set"]).strip()
                if not item["set"]:
                    raise ValueError(f"responses[{idx}].set is empty")

            else:
                item["nodes"] = expand_id_specs(
                    item["nodes"],
                    field_name=f"responses[{idx}].nodes"
                )
                if not item["nodes"]:
                    raise ValueError(f"responses[{idx}].nodes is empty")

                set_name = str(item.get("set_name", "")).strip()
                if not set_name:
                    set_name = make_auto_response_node_set_name(idx)

                item["set_name"] = set_name
                item["set"] = set_name

        elif item["type"] == "element":
            has_set = "set" in item and str(item.get("set", "")).strip() != ""
            has_elements = "elements" in item

            if has_set and has_elements:
                raise ValueError(
                    f"responses[{idx}] element response cannot contain both 'set' and 'elements'"
                )

            if not has_set and not has_elements:
                raise ValueError(
                    f"responses[{idx}] element response must contain either 'set' or 'elements'"
                )

            if has_set:
                item["set"] = str(item["set"]).strip()
                if not item["set"]:
                    raise ValueError(f"responses[{idx}].set is empty")

            else:
                item["elements"] = expand_id_specs(
                    item["elements"],
                    field_name=f"responses[{idx}].elements"
                )
                if not item["elements"]:
                    raise ValueError(f"responses[{idx}].elements is empty")

                set_name = str(item.get("set_name", "")).strip()
                if not set_name:
                    set_name = make_auto_response_set_name(idx)

                item["set_name"] = set_name
                item["set"] = set_name

        if item["set"] in used_response_set_names:
            raise ValueError(
                f'responses[{idx}] duplicated response set name: "{item["set"]}"'
            )
        used_response_set_names.add(item["set"])

    return data


# =========================================================
# Parse *ELSET data line
# =========================================================
def _parse_elset_data_line(parts: List[str], is_generate: bool, elset_name: str) -> List[Tuple[str, Any]]:
    items: List[Tuple[str, Any]] = []

    if is_generate:
        if len(parts) < 2:
            raise ValueError(f"*ELSET, GENERATE data is incomplete: {elset_name}")

        try:
            start = int(parts[0])
            stop = int(parts[1])
            step = int(parts[2]) if len(parts) >= 3 else 1
        except ValueError:
            raise ValueError(f"*ELSET, GENERATE cannot parse integers: {elset_name} -> {parts}")

        if step == 0:
            raise ValueError(f"*ELSET, GENERATE step cannot be 0: {elset_name}")

        if step > 0:
            if start > stop:
                raise ValueError(f"*ELSET, GENERATE invalid range: {elset_name} -> {parts}")
            cur = start
            while cur <= stop:
                items.append(("id", cur))
                cur += step
        else:
            if start < stop:
                raise ValueError(f"*ELSET, GENERATE invalid range: {elset_name} -> {parts}")
            cur = start
            while cur >= stop:
                items.append(("id", cur))
                cur += step
    else:
        for p in parts:
            try:
                items.append(("id", int(p)))
            except ValueError:
                items.append(("set", p))

    return items


# =========================================================
# Parse main inp
# =========================================================
def parse_main_inp(lines: List[str]) -> Dict[str, Any]:
    element_to_sets: Dict[Tuple[str, str, int], Set[str]] = defaultdict(set)
    seed_elset_members: Dict[Tuple[str, str, str], Set[int]] = defaultdict(set)
    raw_elset_items: Dict[Tuple[str, str, str], List[Tuple[str, Any]]] = defaultdict(list)
    section_blocks: List[Dict[str, Any]] = []
    step_blocks: List[Dict[str, Any]] = []
    part_blocks: List[Dict[str, Any]] = []
    instance_to_part: Dict[str, str] = {}
    part_to_instances: Dict[str, List[str]] = defaultdict(list)
    assembly_blocks: List[Dict[str, Any]] = []

    i = 0
    n = len(lines)
    current_part_name: Optional[str] = None
    current_part_start: Optional[int] = None
    current_assembly_name: Optional[str] = None
    current_assembly_start: Optional[int] = None

    while i < n:
        line = lines[i]
        s = line.strip()
        su = s.upper()

        if su.startswith("*PART"):
            current_part_name = parse_param_value(s, "NAME") or ""
            current_part_start = i
            i += 1
            continue

        if su.startswith("*END PART"):
            part_blocks.append(
                {
                    "name": str(current_part_name or "").strip(),
                    "start": int(current_part_start if current_part_start is not None else i),
                    "end": i + 1,
                    "end_part_idx": i,
                }
            )
            current_part_name = None
            current_part_start = None
            i += 1
            continue

        if su.startswith("*ASSEMBLY"):
            current_assembly_name = parse_param_value(s, "NAME") or ""
            current_assembly_start = i
            i += 1
            continue

        if su.startswith("*END ASSEMBLY"):
            assembly_blocks.append(
                {
                    "name": str(current_assembly_name or "").strip(),
                    "start": int(current_assembly_start if current_assembly_start is not None else i),
                    "end": i + 1,
                    "end_assembly_idx": i,
                }
            )
            current_assembly_name = None
            current_assembly_start = None
            i += 1
            continue

        if su.startswith("*INSTANCE"):
            instance_name = str(parse_param_value(s, "NAME") or "").strip()
            part_name = str(parse_param_value(s, "PART") or "").strip()
            if instance_name and part_name:
                instance_to_part[instance_name] = part_name
                part_to_instances[part_name].append(instance_name)
            i += 1
            continue

        scope_type = PART_SCOPE_TYPE if current_part_name else ROOT_SCOPE_TYPE
        scope_name = current_part_name if current_part_name else ROOT_SCOPE_NAME
        scope_key = make_scope_key(scope_type, scope_name)

        if su.startswith("*ELEMENT"):
            elset = parse_param_value(s, "ELSET")
            j = i + 1

            while j < n:
                s2 = lines[j].strip()

                if not s2 or s2.startswith("**"):
                    j += 1
                    continue

                if s2.startswith("*"):
                    break

                parts = [p.strip() for p in lines[j].split(",") if p.strip()]
                if parts:
                    try:
                        eid = int(parts[0])
                        if elset:
                            seed_elset_members[(scope_key[0], scope_key[1], elset)].add(eid)
                            element_to_sets[(scope_key[0], scope_key[1], eid)].add(elset)
                    except ValueError:
                        pass

                j += 1

            i = j
            continue

        if su.startswith("*ELSET"):
            elset_name = parse_param_value(s, "ELSET")
            if not elset_name:
                i += 1
                continue

            if current_assembly_name and not current_part_name:
                i += 1
                while i < n:
                    s2 = lines[i].strip()
                    if not s2 or s2.startswith("**"):
                        i += 1
                        continue
                    if s2.startswith("*"):
                        break
                    i += 1
                continue

            is_generate = "GENERATE" in su
            j = i + 1

            while j < n:
                s2 = lines[j].strip()

                if not s2 or s2.startswith("**"):
                    j += 1
                    continue

                if s2.startswith("*"):
                    break

                parts = [p.strip() for p in lines[j].split(",") if p.strip()]
                raw_elset_items[(scope_key[0], scope_key[1], elset_name)].extend(
                    _parse_elset_data_line(parts, is_generate, elset_name)
                )
                j += 1

            i = j
            continue

        if su.startswith(SECTION_KEYWORDS):
            elset = parse_param_value(s, "ELSET")
            material = parse_param_value(s, "MATERIAL")
            j = i + 1
            data_lines = []

            while j < n:
                s2 = lines[j].strip()
                if s2.startswith("*") and not s2.startswith("**"):
                    break
                data_lines.append(lines[j])
                j += 1

            section_blocks.append({
                "start": i,
                "end": j,
                "header": line,
                "elset": elset,
                "material": material,
                "data_lines": data_lines,
                "scope_type": scope_key[0],
                "scope_name": scope_key[1],
                "scope_key": scope_key,
            })
            i = j
            continue

        if su.startswith("*STEP"):
            j = i + 1
            while j < n:
                if lines[j].strip().upper().startswith("*END STEP"):
                    j += 1
                    break
                j += 1

            step_blocks.append({
                "start": i,
                "end": j,
                "header": line,
            })
            i = j
            continue

        i += 1

    resolved_elset_members: Dict[Tuple[str, str, str], Set[int]] = {}

    def resolve_elset(scope_elset_key: Tuple[str, str, str], stack: Optional[Set[Tuple[str, str, str]]] = None) -> Set[int]:
        if scope_elset_key in resolved_elset_members:
            return resolved_elset_members[scope_elset_key]

        if stack is None:
            stack = set()

        if scope_elset_key in stack:
            raise ValueError(f"Cyclic ELSET reference detected: {scope_elset_key[2]}")

        stack.add(scope_elset_key)

        members = set(seed_elset_members.get(scope_elset_key, set()))
        for kind, value in raw_elset_items.get(scope_elset_key, []):
            if kind == "id":
                members.add(value)
            elif kind == "set":
                nested_key = (scope_elset_key[0], scope_elset_key[1], str(value))
                members.update(resolve_elset(nested_key, stack))

        stack.remove(scope_elset_key)
        resolved_elset_members[scope_elset_key] = members
        return members

    all_elset_names = set(seed_elset_members.keys()) | set(raw_elset_items.keys())
    for scoped_set_key in all_elset_names:
        resolve_elset(scoped_set_key)

    for scoped_set_key, members in resolved_elset_members.items():
        for eid in members:
            element_to_sets[(scoped_set_key[0], scoped_set_key[1], eid)].add(scoped_set_key[2])

    section_by_elset = {
        (blk["scope_type"], blk["scope_name"], blk["elset"]): blk
        for blk in section_blocks
        if blk.get("elset")
    }
    section_elsets = set(section_by_elset.keys())

    return {
        "element_to_sets": element_to_sets,
        "elset_members": resolved_elset_members,
        "section_blocks": section_blocks,
        "section_by_elset": section_by_elset,
        "section_elsets": section_elsets,
        "step_blocks": step_blocks,
        "part_blocks": part_blocks,
        "part_blocks_by_name": {
            str(block.get("name") or "").strip(): block
            for block in part_blocks
            if str(block.get("name") or "").strip()
        },
        "assembly_blocks": assembly_blocks,
        "assembly_block": assembly_blocks[0] if assembly_blocks else None,
        "instance_to_part": instance_to_part,
        "part_to_instances": {key: list(value) for key, value in part_to_instances.items()},
    }


# =========================================================
# Analyze element-set tasks
# =========================================================
def analyze_element_set_tasks(
    tasks: List[Dict[str, Any]],
    parsed: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]], List[str]]:
    element_to_sets = parsed["element_to_sets"]
    elset_members = parsed["elset_members"]
    section_by_elset = parsed["section_by_elset"]
    section_elsets = parsed["section_elsets"]

    warnings: List[str] = []
    mother_set_to_remove_ids: Dict[str, Set[int]] = defaultdict(set)
    enriched_tasks: List[Dict[str, Any]] = []

    for task in tasks:
        set_name = task["set_name"]
        elements = task["elements"]
        parameter = task["parameter"]
        value = task.get("value", None)

        # 每个 mother set 单独归组，后面自动生成对应子集并赋对应 material
        source_mother_sets: Dict[str, List[int]] = defaultdict(list)

        for eid in elements:
            all_sets = element_to_sets.get(eid, set())
            mother_candidates = sorted(s for s in all_sets if s in section_elsets)

            if len(mother_candidates) == 0:
                raise ValueError(
                    f"Element {eid} does not belong to any section-driving mother elset."
                )

            if len(mother_candidates) > 1:
                raise ValueError(
                    f"Element {eid} belongs to multiple section-driving mother elsets: "
                    f"{mother_candidates}"
                )

            mother_set = mother_candidates[0]
            source_mother_sets[mother_set].append(eid)
            mother_set_to_remove_ids[mother_set].add(eid)

        source_groups: List[Dict[str, Any]] = []
        for idx_group, mother_set in enumerate(sorted(source_mother_sets.keys()), 1):
            blk = section_by_elset[mother_set]
            section_keyword = infer_section_keyword_from_header(blk["header"])
            material = parse_param_value(blk["header"], "MATERIAL")

            if not material:
                raise ValueError(
                    f"Cannot infer MATERIAL from section header of mother elset '{mother_set}'. "
                    f"Header: {blk['header']}"
                )

            original_thickness = extract_shell_section_thickness(blk["data_lines"])
            if original_thickness is None:
                raise ValueError(
                    f"Cannot infer original shell thickness from section of mother elset '{mother_set}'. "
                    f"Header: {blk['header']}"
                )

            group_set_name = (
                set_name if len(source_mother_sets) == 1
                else make_section_sub_set_name(set_name, mother_set, idx_group)
            )

            source_groups.append({
                "mother_set": mother_set,
                "elements": sorted(set(source_mother_sets[mother_set])),
                "section_keyword": section_keyword,
                "material": material,
                "section_header": blk["header"],
                "original_thickness": original_thickness,
                "group_set_name": group_set_name,
            })

        if len(source_groups) > 1:
            warnings.append(
                f"Set {set_name}: elements belong to multiple original section/material groups; "
                f"auto split into {len(source_groups)} section subsets."
            )

        enriched_tasks.append({
            "set_name": set_name,
            "elements": sorted(set(elements)),
            "parameter": parameter,
            "value": value,
            "source_mother_sets": dict(source_mother_sets),
            "source_groups": source_groups,
        })

    mother_set_to_remainder_name: Dict[str, Optional[str]] = {}
    for mother_set, removed_ids in mother_set_to_remove_ids.items():
        all_ids = set(elset_members.get(mother_set, set()))
        remain = sorted(all_ids - removed_ids)

        if remain:
            mother_set_to_remainder_name[mother_set] = f"{mother_set}_REM"
        else:
            mother_set_to_remainder_name[mother_set] = None
            warnings.append(f"Mother set {mother_set} becomes empty after subtraction.")

    return enriched_tasks, mother_set_to_remainder_name, warnings


# =========================================================
# Build include.inp
# =========================================================
def build_include_text(
    config: Dict[str, Any],
    enriched_tasks: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    mother_set_to_remainder_name: Dict[str, Optional[str]],
) -> str:
    """
    include.inp contains:
    1. *PARAMETER
    2. *DESIGN PARAMETER
    3. new ELSET for section-thickness tasks
    4. new SHELL SECTION
    5. remainder ELSET
    6. new NSET
    7. response ELSET created from responses[].elements
    """
    elset_members = parsed["elset_members"]
    parameter_alias_map = _build_parameter_alias_map(enriched_tasks)

    parameter_map: Dict[str, Any] = {}

    for task in enriched_tasks:
        parameter_name = str(task["parameter"])
        p = parameter_alias_map.get(parameter_name, _safe_parameter_identifier(parameter_name))

        # 如果 JSON 明确写了 value，就使用 JSON 的 value。
        # 如果没有写 value，就从该任务对应原始 section 的壳厚度自动继承。
        if task.get("value", None) is not None:
            v = task["value"]
        else:
            original_values = sorted(
                set(str(group["original_thickness"]) for group in task["source_groups"])
            )

            if len(original_values) != 1:
                raise ValueError(
                    f'Parameter "{p}" has no explicit value, but selected elements come from '
                    f"multiple original shell thicknesses: {original_values}. "
                    f"Please set 'value' explicitly or split this task into different parameters."
                )

            v = original_values[0]

        if p in parameter_map and str(parameter_map[p]) != str(v):
            raise ValueError(
                f'Parameter "{parameter_name}" has inconsistent initial values: '
                f"{parameter_map[p]} vs {v}"
            )

        parameter_map[p] = v

    lines: List[str] = []
    lines.append("** ================================================================")
    lines.append("** Auto-generated include file")
    lines.append("** ================================================================")

    # thickness design parameters
    lines.append("*PARAMETER")
    for p in sorted(parameter_map.keys()):
        lines.append(f"{p}={parameter_map[p]}")

    lines.append("*DESIGN PARAMETER")
    unique_params = sorted(parameter_map.keys())
    for i in range(0, len(unique_params), 10):
        row = ",".join(unique_params[i:i + 10]) + ","
        lines.append(row)

    # new section-driving element sets
    lines.append("** New element sets and shell sections")
    for idx, task in enumerate(enriched_tasks, 1):
        lines.append("**")
        lines.append(f"** Element task {idx}: {task['set_name']}")

        # 可选：先写一个总集合，便于查看
        lines.extend(build_set_block("*ELSET", task["set_name"], task["elements"]))

        # 真正赋 section 的是按 mother/material 自动分出来的子集
        for group in task["source_groups"]:
            if group["group_set_name"] != task["set_name"]:
                lines.append("**")
                lines.append(
                    f"** Auto section subset from mother set {group['mother_set']}: {group['group_set_name']}"
                )
                lines.extend(build_set_block("*ELSET", group["group_set_name"], group["elements"]))

            lines.append(
                f"{group['section_keyword']}, ELSET={group['group_set_name']}, MATERIAL={group['material']}"
            )
            lines.append(
                f"<{parameter_alias_map.get(str(task['parameter']), _safe_parameter_identifier(str(task['parameter'])))}>"
            )

    # remainder sets
    lines.append("** Remainder element sets")
    for mother_set, remainder_name in sorted(mother_set_to_remainder_name.items()):
        if remainder_name is None:
            continue

        removed_ids = set()
        for task in enriched_tasks:
            removed_ids.update(task["source_mother_sets"].get(mother_set, []))

        all_ids = set(elset_members.get(mother_set, set()))
        remain = sorted(all_ids - removed_ids)
        if not remain:
            continue

        lines.extend(build_set_block("*ELSET", remainder_name, remain))

    # node sets
    if config["node_sets"]:
        lines.append("** New node sets")
        for idx, nset in enumerate(config["node_sets"], 1):
            lines.append("**")
            lines.append(f"** Node set {idx}: {nset['set_name']}")
            lines.extend(build_set_block("*NSET", nset["set_name"], nset["nodes"]))

    # response element sets created on demand
    response_elset_written: Set[str] = set()
    has_response_elsets = any(
        rsp.get("type") == "element" and "elements" in rsp
        for rsp in config["responses"]
    )

    if has_response_elsets:
        lines.append("** Response element sets")
        for idx, rsp in enumerate(config["responses"], 1):
            if rsp.get("type") != "element":
                continue
            if "elements" not in rsp:
                continue

            set_name = rsp["set"]
            if set_name in response_elset_written:
                raise ValueError(
                    f'Duplicate response element set name detected: "{set_name}"'
                )

            lines.append("**")
            lines.append(f"** Response element set {idx}: {set_name}")
            lines.extend(build_set_block("*ELSET", set_name, rsp["elements"]))
            response_elset_written.add(set_name)

    # response node sets created on demand
    response_nset_written: Set[str] = set()
    has_response_nsets = any(
        rsp.get("type") == "node" and "nodes" in rsp
        for rsp in config["responses"]
    )

    if has_response_nsets:
        lines.append("** Response node sets")
        for idx, rsp in enumerate(config["responses"], 1):
            if rsp.get("type") != "node":
                continue
            if "nodes" not in rsp:
                continue

            set_name = rsp["set"]
            if set_name in response_nset_written:
                raise ValueError(
                    f'Duplicate response node set name detected: "{set_name}"'
                )

            lines.append("**")
            lines.append(f"** Response node set {idx}: {set_name}")
            lines.extend(build_set_block("*NSET", set_name, rsp["nodes"]))
            response_nset_written.add(set_name)

    lines.append("** ================================================================")
    return "\n".join(line for line in lines if line.strip() != "") + "\n"


def _safe_parameter_identifier(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "").strip())
    text = text.strip("_")
    if not text:
        text = "P_AUTO"
    elif text[0].isdigit():
        text = f"P_{text}"
    return text.upper()


def _build_parameter_alias_map(enriched_tasks: List[Dict[str, Any]]) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    used_aliases: Set[str] = set()
    for task in enriched_tasks:
        original_name = str(task.get("parameter") or "").strip()
        if not original_name:
            continue
        if original_name in alias_map:
            continue
        base_alias = _safe_parameter_identifier(original_name)
        alias = base_alias
        index = 1
        while alias in used_aliases:
            alias = f"{base_alias}_{index}"
            index += 1
        alias_map[original_name] = alias
        used_aliases.add(alias)
    return alias_map


def analyze_element_set_tasks_scoped(
    tasks: List[Dict[str, Any]],
    parsed: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str, str], Optional[str]], List[str]]:
    element_to_sets = parsed["element_to_sets"]
    elset_members = parsed["elset_members"]
    section_by_elset = parsed["section_by_elset"]
    section_elsets = parsed["section_elsets"]
    instance_to_part = parsed.get("instance_to_part", {})
    part_to_instances = parsed.get("part_to_instances", {})

    warnings: List[str] = []
    mother_set_to_remove_ids: Dict[Tuple[str, str, str], Set[int]] = defaultdict(set)
    enriched_tasks: List[Dict[str, Any]] = []

    for task in tasks:
        set_name = task["set_name"]
        elements = task["elements"]
        parameter = task["parameter"]
        value = task.get("value", None)
        requested_scope = str(task.get("set_scope") or ROOT_SCOPE_TYPE).strip().upper() or ROOT_SCOPE_TYPE
        part_name = str(task.get("part_name") or "").strip()
        instance_name = str(task.get("instance_name") or "").strip()

        if requested_scope == ASSEMBLY_SCOPE_TYPE and instance_name:
            inferred_part_name = str(instance_to_part.get(instance_name) or "").strip()
            if not inferred_part_name:
                raise ValueError(
                    f'Instance "{instance_name}" cannot be resolved to a part for parameter "{parameter}".'
                )
            if len(part_to_instances.get(inferred_part_name, [])) > 1:
                warnings.append(
                    f'Parameter "{parameter}" targets instance "{instance_name}", but part "{inferred_part_name}" '
                    f'is reused by multiple instances. Generated shell section changes will affect all of them.'
                )
            part_name = inferred_part_name
            requested_scope = PART_SCOPE_TYPE

        target_scope_key = make_scope_key(PART_SCOPE_TYPE if part_name else ROOT_SCOPE_TYPE, part_name or ROOT_SCOPE_NAME)
        source_mother_sets: Dict[Tuple[str, str, str], List[int]] = defaultdict(list)

        for eid in elements:
            all_sets = element_to_sets.get((target_scope_key[0], target_scope_key[1], eid), set())
            mother_candidates = sorted(
                (target_scope_key[0], target_scope_key[1], candidate_name)
                for candidate_name in all_sets
                if (target_scope_key[0], target_scope_key[1], candidate_name) in section_elsets
            )
            if not mother_candidates:
                raise ValueError(
                    f"Element {eid} does not belong to any section-driving mother elset in scope "
                    f"{scope_comment(target_scope_key[0], target_scope_key[1])}."
                )
            if len(mother_candidates) > 1:
                raise ValueError(
                    f"Element {eid} belongs to multiple section-driving mother elsets: "
                    f"{[item[2] for item in mother_candidates]}"
                )
            mother_set = mother_candidates[0]
            source_mother_sets[mother_set].append(eid)
            mother_set_to_remove_ids[mother_set].add(eid)

        source_groups: List[Dict[str, Any]] = []
        for idx_group, mother_set in enumerate(sorted(source_mother_sets.keys()), 1):
            blk = section_by_elset[mother_set]
            material = parse_param_value(blk["header"], "MATERIAL")
            if not material:
                raise ValueError(
                    f"Cannot infer MATERIAL from section header of mother elset '{mother_set[2]}'. "
                    f"Header: {blk['header']}"
                )
            original_thickness = extract_shell_section_thickness(blk["data_lines"])
            if original_thickness is None:
                raise ValueError(
                    f"Cannot infer original shell thickness from section of mother elset '{mother_set[2]}'. "
                    f"Header: {blk['header']}"
                )
            group_set_name = (
                set_name if len(source_mother_sets) == 1
                else make_section_sub_set_name(set_name, mother_set[2], idx_group)
            )
            source_groups.append(
                {
                    "mother_set": mother_set,
                    "elements": sorted(set(source_mother_sets[mother_set])),
                    "section_keyword": infer_section_keyword_from_header(blk["header"]),
                    "material": material,
                    "section_header": blk["header"],
                    "original_thickness": original_thickness,
                    "group_set_name": group_set_name,
                    "scope_type": blk["scope_type"],
                    "scope_name": blk["scope_name"],
                }
            )

        if len(source_groups) > 1:
            warnings.append(
                f"Set {set_name}: elements belong to multiple original section/material groups; "
                f"auto split into {len(source_groups)} section subsets."
            )

        enriched_tasks.append(
            {
                "set_name": set_name,
                "elements": sorted(set(elements)),
                "parameter": parameter,
                "value": value,
                "source_mother_sets": dict(source_mother_sets),
                "source_groups": source_groups,
                "set_scope": requested_scope,
                "part_name": part_name or None,
                "instance_name": instance_name or None,
                "target_scope_key": target_scope_key,
            }
        )

    mother_set_to_remainder_name: Dict[Tuple[str, str, str], Optional[str]] = {}
    for mother_set, removed_ids in mother_set_to_remove_ids.items():
        all_ids = set(elset_members.get(mother_set, set()))
        remain = sorted(all_ids - removed_ids)
        if remain:
            mother_set_to_remainder_name[mother_set] = f"{mother_set[2]}_REM"
        else:
            mother_set_to_remainder_name[mother_set] = None
            warnings.append(
                f"Mother set {mother_set[2]} in scope {scope_comment(mother_set[0], mother_set[1])} "
                f"becomes empty after subtraction."
            )

    return enriched_tasks, mother_set_to_remainder_name, warnings


def _build_parameter_lines(enriched_tasks: List[Dict[str, Any]]) -> List[str]:
    parameter_alias_map = _build_parameter_alias_map(enriched_tasks)
    parameter_map: Dict[str, Any] = {}
    for task in enriched_tasks:
        parameter_name = str(task["parameter"])
        parameter_alias = parameter_alias_map.get(parameter_name, _safe_parameter_identifier(parameter_name))
        if task.get("value", None) is not None:
            value = task["value"]
        else:
            original_values = sorted(set(str(group["original_thickness"]) for group in task["source_groups"]))
            if len(original_values) != 1:
                raise ValueError(
                    f'Parameter "{parameter_name}" has no explicit value, but selected elements come from '
                    f"multiple original shell thicknesses: {original_values}. "
                    f"Please set 'value' explicitly or split this task into different parameters."
                )
            value = original_values[0]
        if parameter_alias in parameter_map and str(parameter_map[parameter_alias]) != str(value):
            raise ValueError(
                f'Parameter "{parameter_name}" has inconsistent initial values: '
                f"{parameter_map[parameter_alias]} vs {value}"
            )
        parameter_map[parameter_alias] = value

    lines: List[str] = [
        "** ================================================================",
        "** Auto-generated DSA global include",
        "** ================================================================",
        f"** {AUTO_COMMENT_PREFIX}_GLOBAL_BEGIN",
        "*PARAMETER",
    ]
    for parameter_name in sorted(parameter_map.keys()):
        lines.append(f"{parameter_name}={parameter_map[parameter_name]}")
    lines.append("*DESIGN PARAMETER")
    unique_params = sorted(parameter_map.keys())
    for i in range(0, len(unique_params), 10):
        lines.append(",".join(unique_params[i:i + 10]) + ",")
    lines.append(f"** {AUTO_COMMENT_PREFIX}_GLOBAL_END")
    return lines


def _build_scope_structure_lines(
    *,
    scope_key: Tuple[str, str],
    config: Dict[str, Any],
    enriched_tasks: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    mother_set_to_remainder_name: Dict[Tuple[str, str, str], Optional[str]],
) -> List[str]:
    parameter_alias_map = _build_parameter_alias_map(enriched_tasks)
    lines: List[str] = [
        "** ================================================================",
        f"** Auto-generated DSA scoped include for {scope_comment(scope_key[0], scope_key[1])}",
        "** ================================================================",
        f"** {AUTO_COMMENT_PREFIX}_SCOPE_BEGIN {scope_comment(scope_key[0], scope_key[1])}",
    ]
    elset_members = parsed["elset_members"]

    local_task_found = False
    for idx, task in enumerate(enriched_tasks, 1):
        groups = [group for group in task["source_groups"] if make_scope_key(group["scope_type"], group["scope_name"]) == scope_key]
        if not groups:
            continue
        local_task_found = True
        parameter_name = str(task["parameter"])
        parameter_alias = parameter_alias_map.get(parameter_name, _safe_parameter_identifier(parameter_name))
        lines.append("**")
        lines.append(
            f"** {AUTO_COMMENT_PREFIX}_TASK_BEGIN index={idx} parameter={parameter_name} alias={parameter_alias} set={task['set_name']}"
        )
        if make_scope_key(task["target_scope_key"][0], task["target_scope_key"][1]) == scope_key:
            lines.extend(build_set_block("*ELSET", task["set_name"], task["elements"]))
        for group in groups:
            if group["group_set_name"] != task["set_name"]:
                lines.append("**")
                lines.append(
                    f"** Auto section subset from mother set {group['mother_set'][2]}: {group['group_set_name']}"
                )
                lines.extend(build_set_block("*ELSET", group["group_set_name"], group["elements"]))
            lines.append(
                f"{group['section_keyword']}, ELSET={group['group_set_name']}, MATERIAL={group['material']}"
            )
            lines.append(f"<{parameter_alias}>")
        lines.append(
            f"** {AUTO_COMMENT_PREFIX}_TASK_END index={idx} parameter={parameter_name} alias={parameter_alias} set={task['set_name']}"
        )

    local_remainder_found = False
    for mother_set, remainder_name in sorted(mother_set_to_remainder_name.items()):
        if make_scope_key(mother_set[0], mother_set[1]) != scope_key or remainder_name is None:
            continue
        local_remainder_found = True
        removed_ids = set()
        for task in enriched_tasks:
            removed_ids.update(task["source_mother_sets"].get(mother_set, []))
        all_ids = set(elset_members.get(mother_set, set()))
        remain = sorted(all_ids - removed_ids)
        if not remain:
            continue
        lines.append("**")
        lines.append(f"** {AUTO_COMMENT_PREFIX}_REMAINDER mother_set={mother_set[2]} renamed={remainder_name}")
        lines.extend(build_set_block("*ELSET", remainder_name, remain))

    if scope_key[0] == ROOT_SCOPE_TYPE:
        if config["node_sets"]:
            lines.append("** New node sets")
            for idx, nset in enumerate(config["node_sets"], 1):
                lines.append("**")
                lines.append(f"** Node set {idx}: {nset['set_name']}")
                lines.extend(build_set_block("*NSET", nset["set_name"], nset["nodes"]))
        for idx, rsp in enumerate(config["responses"], 1):
            if rsp.get("type") == "element" and "elements" in rsp:
                lines.append("**")
                lines.append(f"** Response element set {idx}: {rsp['set']}")
                lines.extend(build_set_block("*ELSET", rsp["set"], rsp["elements"]))
            if rsp.get("type") == "node" and "nodes" in rsp:
                lines.append("**")
                lines.append(f"** Response node set {idx}: {rsp['set']}")
                lines.extend(build_set_block("*NSET", rsp["set"], rsp["nodes"]))

    if not local_task_found and not local_remainder_found and len(lines) <= 4:
        return []
    lines.append(f"** {AUTO_COMMENT_PREFIX}_SCOPE_END {scope_comment(scope_key[0], scope_key[1])}")
    return [line for line in lines if line.strip() != ""]


def _build_assembly_lines(config: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    if config["node_sets"]:
        lines.append("** New node sets")
        for idx, nset in enumerate(config["node_sets"], 1):
            lines.append("**")
            lines.append(f"** Node set {idx}: {nset['set_name']}")
            extra_options: List[str] = []
            instance_name = str(nset.get("instance_name") or "").strip()
            if instance_name:
                extra_options.append(f"INSTANCE={instance_name}")
            lines.extend(build_set_block("*NSET", nset["set_name"], nset["nodes"], extra_options=extra_options))
    for idx, rsp in enumerate(config["responses"], 1):
        if rsp.get("type") == "element" and "elements" in rsp:
            lines.append("**")
            lines.append(f"** Response element set {idx}: {rsp['set']}")
            extra_options = []
            instance_name = str(rsp.get("instance_name") or "").strip()
            if instance_name:
                extra_options.append(f"INSTANCE={instance_name}")
            lines.extend(build_set_block("*ELSET", rsp["set"], rsp["elements"], extra_options=extra_options))
        if rsp.get("type") == "node" and "nodes" in rsp:
            lines.append("**")
            lines.append(f"** Response node set {idx}: {rsp['set']}")
            extra_options = []
            instance_name = str(rsp.get("instance_name") or "").strip()
            if instance_name:
                extra_options.append(f"INSTANCE={instance_name}")
            lines.extend(build_set_block("*NSET", rsp["set"], rsp["nodes"], extra_options=extra_options))
    if not lines:
        return []
    return [
        "** ================================================================",
        "** Auto-generated DSA assembly include",
        "** ================================================================",
        f"** {AUTO_COMMENT_PREFIX}_ASSEMBLY_BEGIN",
        *lines,
        f"** {AUTO_COMMENT_PREFIX}_ASSEMBLY_END",
    ]


def build_dsa_include_layout(
    config: Dict[str, Any],
    enriched_tasks: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    mother_set_to_remainder_name: Dict[Tuple[str, str, str], Optional[str]],
    *,
    include_file: str,
) -> Dict[str, Any]:
    include_name = str(include_file or DEFAULT_INCLUDE_FILE).strip() or DEFAULT_INCLUDE_FILE
    include_path = Path(include_name)
    include_stem = include_path.stem or "include"
    include_suffix = include_path.suffix or ".inp"

    files: Dict[str, str] = {}
    files[include_name] = "\n".join(_build_parameter_lines(enriched_tasks)) + "\n"

    has_part_scope = any(group.get("scope_type") == PART_SCOPE_TYPE for task in enriched_tasks for group in task["source_groups"])
    scope_include_refs: Dict[Tuple[str, str], str] = {}

    if has_part_scope:
        part_names = sorted(
            {
                make_scope_key(group["scope_type"], group["scope_name"])[1]
                for task in enriched_tasks
                for group in task["source_groups"]
                if group.get("scope_type") == PART_SCOPE_TYPE
            }
        )
        for part_name in part_names:
            scope_key = make_scope_key(PART_SCOPE_TYPE, part_name)
            part_include_name = f"{include_stem}_part_{safe_include_fragment(part_name)}{include_suffix}"
            part_lines = _build_scope_structure_lines(
                scope_key=scope_key,
                config=config,
                enriched_tasks=enriched_tasks,
                parsed=parsed,
                mother_set_to_remainder_name=mother_set_to_remainder_name,
            )
            if part_lines:
                files[part_include_name] = "\n".join(part_lines) + "\n"
                scope_include_refs[scope_key] = part_include_name
        assembly_lines = _build_assembly_lines(config)
        assembly_include_name = None
        if assembly_lines:
            assembly_include_name = f"{include_stem}_assembly{include_suffix}"
            files[assembly_include_name] = "\n".join(assembly_lines) + "\n"
    else:
        root_scope = make_scope_key(ROOT_SCOPE_TYPE, ROOT_SCOPE_NAME)
        root_lines = _build_scope_structure_lines(
            scope_key=root_scope,
            config=config,
            enriched_tasks=enriched_tasks,
            parsed=parsed,
            mother_set_to_remainder_name=mother_set_to_remainder_name,
        )
        if root_lines:
            files[include_name] = files[include_name] + "\n".join(root_lines) + "\n"
        assembly_lines = _build_assembly_lines(config)
        assembly_include_name = None
        if assembly_lines:
            assembly_include_name = f"{include_stem}_assembly{include_suffix}"
            files[assembly_include_name] = "\n".join(assembly_lines) + "\n"

    return {
        "global_include_name": include_name,
        "scope_include_refs": scope_include_refs,
        "assembly_include_name": assembly_include_name,
        "files": files,
    }


def patch_main_sections_scoped(
    main_lines: List[str],
    parsed: Dict[str, Any],
    mother_set_to_remainder_name: Dict[Tuple[str, str, str], Optional[str]],
    comment_empty_section: bool = True,
) -> List[str]:
    section_blocks = parsed["section_blocks"]
    start_to_block = {blk["start"]: blk for blk in section_blocks}
    out_lines: List[str] = []
    i = 0
    n = len(main_lines)

    while i < n:
        blk = start_to_block.get(i)
        if blk is None:
            out_lines.append(main_lines[i])
            i += 1
            continue

        block_scope_key = (blk["scope_type"], blk["scope_name"], blk["elset"])
        start = blk["start"]
        end = blk["end"]
        if block_scope_key not in mother_set_to_remainder_name:
            out_lines.extend(main_lines[start:end])
            i = end
            continue

        remainder_name = mother_set_to_remainder_name[block_scope_key]
        if remainder_name is not None:
            for line_index in range(start, end):
                line = main_lines[line_index]
                if line_index == start:
                    line = re.sub(
                        rf"(ELSET\s*=\s*){re.escape(blk['elset'])}\b",
                        rf"\1{remainder_name}",
                        line,
                        flags=re.I,
                    )
                out_lines.append(line)
        else:
            for line_index in range(start, end):
                line = main_lines[line_index]
                if line.startswith("**"):
                    out_lines.append(line)
                elif comment_empty_section:
                    out_lines.append("** " + line)
        i = end

    return out_lines


def patch_scoped_include_lines(
    lines: List[str],
    parsed: Dict[str, Any],
    include_layout: Dict[str, Any],
) -> List[str]:
    out = patch_include_line(lines, include_layout["global_include_name"])
    scope_include_refs = include_layout.get("scope_include_refs", {})
    assembly_block = parsed.get("assembly_block")
    assembly_include_name = include_layout.get("assembly_include_name")

    def _find_named_block_end(keyword: str, block_name: str, end_keyword: str) -> Optional[int]:
        in_target = False
        for idx, line in enumerate(out):
            stripped = line.strip()
            upper = stripped.upper()
            if not in_target and upper.startswith(keyword):
                current_name = str(parse_param_value(stripped, "NAME") or "").strip()
                if current_name == block_name:
                    in_target = True
                    continue
            if in_target and upper.startswith(end_keyword):
                return idx
        return None

    def _find_first_keyword(keyword: str) -> Optional[int]:
        for idx, line in enumerate(out):
            if line.strip().upper().startswith(keyword):
                return idx
        return None

    insertions: Dict[int, List[str]] = defaultdict(list)
    for scope_key, include_name in scope_include_refs.items():
        if scope_key[0] != PART_SCOPE_TYPE:
            continue
        end_idx = _find_named_block_end("*PART", str(scope_key[1]), "*END PART")
        if end_idx is None:
            raise ValueError(f'Cannot locate *Part block for generated include "{include_name}".')
        insertions[int(end_idx)].append(
            f"** {AUTO_COMMENT_PREFIX}_PART_INCLUDE scope={scope_comment(scope_key[0], scope_key[1])}"
        )
        insertions[int(end_idx)].append(f"*Include, input={include_name}")

    if assembly_include_name and assembly_block:
        assembly_name = str(assembly_block.get("name") or "").strip()
        if assembly_name:
            end_idx = _find_named_block_end("*ASSEMBLY", assembly_name, "*END ASSEMBLY")
        else:
            end_idx = _find_first_keyword("*END ASSEMBLY")
        if end_idx is None:
            raise ValueError(f'Cannot locate *Assembly block for generated include "{assembly_include_name}".')
        insertions[int(end_idx)].append(f"** {AUTO_COMMENT_PREFIX}_ASSEMBLY_INCLUDE")
        insertions[int(end_idx)].append(f"*Include, input={assembly_include_name}")

    if not insertions:
        return out

    patched_lines: List[str] = []
    for idx, line in enumerate(out):
        if idx in insertions:
            patched_lines.extend(insertions[idx])
        patched_lines.append(line)
    return patched_lines


# =========================================================
# Patch main inp shell sections
# =========================================================
def patch_main_sections(
    main_lines: List[str],
    parsed: Dict[str, Any],
    mother_set_to_remainder_name: Dict[str, Optional[str]],
    comment_empty_section: bool = True,
) -> List[str]:
    section_blocks = parsed["section_blocks"]
    start_to_block = {blk["start"]: blk for blk in section_blocks}

    out_lines: List[str] = []
    i = 0
    n = len(main_lines)

    while i < n:
        blk = start_to_block.get(i)
        if blk is None:
            out_lines.append(main_lines[i])
            i += 1
            continue

        mother_set = blk["elset"]
        start = blk["start"]
        end = blk["end"]

        if mother_set not in mother_set_to_remainder_name:
            out_lines.extend(main_lines[start:end])
            i = end
            continue

        remainder_name = mother_set_to_remainder_name[mother_set]

        if remainder_name is not None:
            for k in range(start, end):
                line = main_lines[k]
                if k == start:
                    line = re.sub(
                        rf"(ELSET\s*=\s*){re.escape(mother_set)}\b",
                        rf"\1{remainder_name}",
                        line,
                        flags=re.I,
                    )
                out_lines.append(line)
        else:
            if comment_empty_section:
                for k in range(start, end):
                    line = main_lines[k]
                    if line.startswith("**"):
                        out_lines.append(line)
                    else:
                        out_lines.append("** " + line)

        i = end

    return out_lines


# =========================================================
# Insert / replace *Include
# =========================================================
def patch_include_line(lines: List[str], include_file: str) -> List[str]:
    out = list(lines)
    include_line = f"*Include, input={include_file}"

    for i, line in enumerate(out):
        if line.strip().upper().startswith("*INCLUDE"):
            out[i] = include_line
            return out

    insert_idx = None
    if out and out[0].strip().upper().startswith("*HEADING"):
        for i in range(1, len(out)):
            s = out[i].strip()
            if s.startswith("*") and s.upper() != "*HEADING" and not s.startswith("**"):
                insert_idx = i
                break

    if insert_idx is None:
        insert_idx = 0

    out.insert(insert_idx, include_line)
    return out


# =========================================================
# DSA / response helpers
# =========================================================
def build_design_response_block(config: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append("*DESIGN RESPONSE")

    for rsp in config["responses"]:
        rsp_type = rsp["type"]
        set_name = rsp["set"]
        variables = rsp["variables"]

        if rsp_type == "node":
            lines.append(f"*NODE RESPONSE, NSET={set_name}")
        elif rsp_type == "element":
            lines.append(f"*ELEMENT RESPONSE, ELSET={set_name}")
        else:
            raise ValueError(f"Unsupported response type: {rsp_type}")

        row = ",".join(variables)
        if not row.endswith(","):
            row += ","
        lines.append(row)

    return lines


def find_analysis_step_blocks(lines: List[str]) -> List[Dict[str, int]]:
    """
    找所有 *STEP 块。
    这里把所有 step 都视为分析步候选；
    若后续你想更严格区分，可在这里再加过滤。
    """
    step_blocks: List[Dict[str, int]] = []
    i = 0
    n = len(lines)

    while i < n:
        s = lines[i].strip().upper()
        if s.startswith("*STEP"):
            start = i
            j = i + 1
            while j < n:
                if lines[j].strip().upper().startswith("*END STEP"):
                    step_blocks.append({
                        "start": start,
                        "end": j + 1,
                        "end_step_idx": j,
                    })
                    j += 1
                    break
                j += 1
            i = j
        else:
            i += 1

    return step_blocks


def _step_block_is_static(lines: List[str], step_block: Dict[str, int]) -> bool:
    for idx in range(int(step_block["start"]) + 1, int(step_block["end"])):
        keyword = lines[idx].strip().upper()
        if not keyword or keyword.startswith("**"):
            continue
        if keyword.startswith("*STATIC"):
            return True
        if keyword.startswith("*END STEP"):
            return False
    return False


def find_static_step_blocks(lines: List[str]) -> List[Dict[str, int]]:
    return [
        step_block
        for step_block in find_analysis_step_blocks(lines)
        if _step_block_is_static(lines, step_block)
    ]


def remove_existing_dsa_controls(lines: List[str]) -> List[str]:
    out = []
    for line in lines:
        if line.strip().upper().startswith("*DSA CONTROLS"):
            continue
        out.append(line)
    return out


def remove_all_design_response_blocks(lines: List[str]) -> List[str]:
    """
    Remove every existing *DESIGN RESPONSE block so the generator can
    re-insert exactly one normalized block into the selected DSA step.
    """
    out: List[str] = []
    i = 0
    n = len(lines)

    while i < n:
        s = lines[i].strip().upper()
        if not s.startswith("*DESIGN RESPONSE"):
            out.append(lines[i])
            i += 1
            continue

        i += 1
        while i < n:
            keyword = lines[i].strip().upper()
            if keyword.startswith("*NODE RESPONSE") or keyword.startswith("*ELEMENT RESPONSE"):
                i += 1
                continue
            if not keyword.startswith("*"):
                i += 1
                continue
            break

    return out


def remove_design_response_blocks_in_step(out: List[str], step_start: int, step_end_exclusive: int) -> List[str]:
    """
    删除指定 step 内已有的 *DESIGN RESPONSE 块，避免重复。
    """
    result = out[:step_start + 1]
    i = step_start + 1

    while i < step_end_exclusive:
        s = out[i].strip().upper()

        if s.startswith("*DESIGN RESPONSE"):
            j = i + 1
            while j < step_end_exclusive:
                sj = out[j].strip().upper()

                if sj.startswith("*NODE RESPONSE") or sj.startswith("*ELEMENT RESPONSE"):
                    j += 1
                    continue

                if not sj.startswith("*"):
                    j += 1
                    continue

                break

            i = j
            continue

        result.append(out[i])
        i += 1

    result.extend(out[step_end_exclusive:])
    return result


def normalize_step_line_to_dsa(step_line: str) -> str:
    parts = [part.strip() for part in str(step_line or "").strip().split(",")]
    if not parts or parts[0].upper() != "*STEP":
        return "*STEP,DSA"

    kept_params = []
    for param in parts[1:]:
        if not param:
            continue
        upper = param.upper()
        if upper == "DSA":
            continue
        if upper == "PERTURBATION":
            continue
        if upper.startswith("SENSITIVITY"):
            continue
        kept_params.append(param)

    return ",".join(["*STEP"] + kept_params + ["DSA"])


# =========================================================
# Patch step to DSA:
# 1) FORMULATION 固定 TOTAL
# 2) *DSA CONTROLS 放到第一个分析步之前
# 3) *DESIGN RESPONSE 插到最后一个分析步中
# =========================================================
def patch_static_step_to_dsa(lines: List[str], config: Dict[str, Any]) -> List[str]:
    formulation = FIXED_DSA_FORMULATION

    out = list(lines)

    # 1) 删除所有已有 *DSA CONTROLS
    out = remove_existing_dsa_controls(out)
    out = remove_all_design_response_blocks(out)

    # 2) 找所有分析步
    step_blocks = find_analysis_step_blocks(out)
    if not step_blocks:
        raise ValueError("No *STEP block was found in the main inp.")
    static_step_blocks = find_static_step_blocks(out)
    if not static_step_blocks:
        raise ValueError("No static *STEP block was found in the main inp.")

    first_step_idx = static_step_blocks[0]["start"]
    last_step = static_step_blocks[-1]
    last_step_idx = last_step["start"]
    last_end_step_idx = last_step["end_step_idx"]

    # 3) 插入固定 TOTAL 的 DSA CONTROLS 到第一个分析步之前
    dsa_controls_line = f"*DSA CONTROLS, FORMULATION={formulation}"
    out.insert(first_step_idx, dsa_controls_line)

    # 4) 重新找 step blocks（因为索引变了）
    static_step_blocks = find_static_step_blocks(out)
    if not static_step_blocks:
        raise ValueError("No static *STEP block was found in the main inp after DSA insertion.")

    last_step = static_step_blocks[-1]
    last_step_idx = last_step["start"]
    last_end_step_idx = last_step["end_step_idx"]

    # 5) 将最后一个分析步改成 *STEP,DSA
    step_line = out[last_step_idx].strip()
    if step_line.upper().startswith("*STEP"):
        out[last_step_idx] = normalize_step_line_to_dsa(step_line)

    # 6) 删除最后一个分析步里已有的 *DESIGN RESPONSE 块
    static_step_blocks = find_static_step_blocks(out)

    # 7) 再次定位最后一个分析步
    last_step = static_step_blocks[-1]
    last_end_step_idx = last_step["end_step_idx"]

    # 8) 在最后一个分析步 *END STEP 之前插入响应块
    response_block = build_design_response_block(config)
    response_block.insert(0, "**")
    response_block.insert(1, "** Auto-generated design responses")
    out[last_end_step_idx:last_end_step_idx] = response_block

    return out

def parse_args():
    parser = argparse.ArgumentParser(
        description="Abaqus DSA generator"
    )

    parser.add_argument("main_inp", help="Main Abaqus inp file")
    parser.add_argument("config", help="JSON config file")

    parser.add_argument(
        "-o", "--output",
        help="Output main inp file (default: *_dsa.inp)",
        default=None
    )

    parser.add_argument(
        "-i", "--include",
        help="Include file name (default: include.inp)",
        default=None
    )

    return parser.parse_args()

# =========================================================
# Main pipeline
# =========================================================
def main():


    args = parse_args()
    main_path = Path(args.main_inp)
    config_path = Path(args.config)

    if not main_path.exists():
        raise FileNotFoundError(f"Main inp not found: {main_path}")

    config = load_config(config_path)

    # 命令行参数优先级高于 JSON。
    # 这样 --include 不仅影响写出的 include 文件名，也会同步影响主 inp 中的 *Include 行。
    if args.include:
        config["include_file"] = args.include
    if args.output:
        config["main_output"] = args.output

    main_lines = main_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    parsed = parse_main_inp(main_lines)

    enriched_tasks, mother_set_to_remainder_name, warnings = analyze_element_set_tasks(
        config["element_sets"],
        parsed,
    )

    include_text = build_include_text(
        config,
        enriched_tasks,
        parsed,
        mother_set_to_remainder_name,
    )

    patched_main_lines = patch_main_sections(
        main_lines,
        parsed,
        mother_set_to_remainder_name,
        comment_empty_section=COMMENT_EMPTY_SECTION,
    )
    patched_main_lines = patch_include_line(
        patched_main_lines,
        config.get("include_file", DEFAULT_INCLUDE_FILE),
    )
    patched_main_lines = patch_static_step_to_dsa(
        patched_main_lines,
        config,
    )

    patched_main_lines = remove_blank_lines(patched_main_lines)
    updated_main = "\n".join(patched_main_lines) + "\n"

    include_out = Path(config.get("include_file", DEFAULT_INCLUDE_FILE))
    main_out = main_path if OVERWRITE_MAIN_INP else Path(config.get("main_output", UPDATED_MAIN_INP))

    include_out.write_text(include_text, encoding="utf-8")
    main_out.write_text(updated_main, encoding="utf-8")

    print("=" * 72)
    print("Warnings")
    print("=" * 72)
    if warnings:
        for w in warnings:
            print("-", w)
    else:
        print("None")

    print("\n" + "=" * 72)
    print("Mother set -> remainder set")
    print("=" * 72)
    if mother_set_to_remainder_name:
        for mother_set, rem in sorted(mother_set_to_remainder_name.items()):
            if rem is None:
                print(f"{mother_set} -> EMPTY")
            else:
                print(f"{mother_set} -> {rem}")
    else:
        print("None")

    print("\n" + "=" * 72)
    print("Generated files")
    print("=" * 72)
    print(f"Include file: {include_out}")
    print(f"Main file   : {main_out}")


if __name__ == "__main__":
    main()
