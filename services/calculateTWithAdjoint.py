# -*- coding: utf-8 -*-
"""
将普通 Abaqus 壳模型 inp 转成 Adjoint 壳厚灵敏度 inp

功能：
1. 识别所有 *Shell Section
2. 为每个 Shell Section 生成 thickness distribution
3. 将 *Shell Section 改写为 SHELL THICKNESS=distribution_name
4. 将静力步改为 SENSITIVITY=ADJOINT
5. 在静力步末尾插入一个简单的 Design Response

适用场景：
- 普通壳单元厚度作为设计变量
- Adjoint sizing sensitivity with shells

注意：
- 只处理 *Shell Section
- 不处理 *Shell General Section
- 默认只给静力步加 adjoint sensitivity
"""

import os
import re


def parse_option_value(line, key):
    m = re.search(rf"\b{re.escape(key)}\s*=\s*([^,\n\r]+)", line, re.IGNORECASE)
    return m.group(1).strip() if m else None


def sanitize_name(name):
    s = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not s:
        s = "THK_VAR"
    if s[0].isdigit():
        s = "_" + s
    return s


def is_number_string(text):
    return re.fullmatch(
        r"[+-]?\d+(?:\.\d*)?(?:[Ee][+-]?\d+)?|\.\d+(?:[Ee][+-]?\d+)?",
        text.strip()
    ) is not None


def extract_sets(lines):
    elsets = []
    nsets = []

    for line in lines:
        s = line.strip()

        if re.match(r"^\*ELSET\b", s, re.IGNORECASE):
            name = parse_option_value(line, "ELSET")
            if name and name not in elsets:
                elsets.append(name)

        if re.match(r"^\*NSET\b", s, re.IGNORECASE):
            name = parse_option_value(line, "NSET")
            if name and name not in nsets:
                nsets.append(name)

    return elsets, nsets


def extract_shell_sections(lines):
    """
    提取所有 *Shell Section
    返回列表，每项包含：
    {
        "line_idx": 关键字行索引,
        "elset": elset名,
        "raw_keyword": 原关键字行,
        "data_idx": 数据行索引,
        "thickness": 第一列厚度,
        "rest_data": 数据行剩余部分（逗号后）
    }
    """
    sections = []
    i = 0

    while i < len(lines):
        s = lines[i].strip()

        if re.match(r"^\*SHELL SECTION\b", s, re.IGNORECASE):
            elset_name = parse_option_value(lines[i], "ELSET")
            data_idx = None
            thickness = None
            rest_data = ""

            j = i + 1
            while j < len(lines):
                sj = lines[j].strip()

                if not sj or sj.startswith("**"):
                    j += 1
                    continue

                if sj.startswith("*"):
                    break

                raw = lines[j].rstrip("\r\n")
                parts = raw.split(",", 1)
                first = parts[0].strip()

                if is_number_string(first):
                    data_idx = j
                    thickness = float(first)
                    rest_data = "," + parts[1] if len(parts) == 2 else ""
                break

            if elset_name and data_idx is not None and thickness is not None:
                sections.append({
                    "line_idx": i,
                    "elset": elset_name,
                    "raw_keyword": lines[i].rstrip("\r\n"),
                    "data_idx": data_idx,
                    "thickness": thickness,
                    "rest_data": rest_data,
                })

            i = j
            continue

        i += 1

    return sections


def build_distribution_block(sections):
    """
    生成 distribution 定义块
    """
    block = []
    block.append("** ----------------------------------------------------------------\n")
    block.append("** ADJOINT SHELL THICKNESS DESIGN VARIABLES\n")
    block.append("** ----------------------------------------------------------------\n")
    block.append("*Distribution Table, Name=THK_TABLE\n")
    block.append("LENGTH,\n")

    dist_map = {}

    for sec in sections:
        elset_name = sec["elset"]
        dist_name = "THK_" + sanitize_name(elset_name)
        dist_map[elset_name] = dist_name

        t = sec["thickness"]
        block.append(f"*Distribution, Location=Element, Design Variable, Name={dist_name}, Table=THK_TABLE\n")
        block.append(f", {t:.10g}\n")
        block.append(f"{elset_name}, {t:.10g}\n")

    return block, dist_map


def rewrite_shell_sections(lines, sections, dist_map):
    """
    将:
      *Shell Section, elset=..., ...
      2.31, 5
    改成:
      *Shell Section, elset=..., ..., SHELL THICKNESS=THK_xxx
      , 5
    """
    new_lines = lines[:]

    for sec in sections:
        elset_name = sec["elset"]
        dist_name = dist_map[elset_name]

        # 改 keyword 行
        kw = sec["raw_keyword"]
        if re.search(r"\bSHELL THICKNESS\s*=", kw, re.IGNORECASE):
            kw_new = re.sub(
                r"\bSHELL THICKNESS\s*=\s*([^,\n\r]+)",
                f"SHELL THICKNESS={dist_name}",
                kw,
                flags=re.IGNORECASE,
            )
        else:
            kw_new = kw + f", SHELL THICKNESS={dist_name}"
        new_lines[sec["line_idx"]] = kw_new + "\n"

        # 改数据行：第一列清空，保留剩余部分
        rest = sec["rest_data"]
        new_lines[sec["data_idx"]] = f"{rest}\n" if rest else ",\n"

    return new_lines


def insert_distribution_block(lines, dist_block):
    """
    将 distribution 块插入到第一个 *Shell Section 前
    """
    insert_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^\*SHELL SECTION\b", line.strip(), re.IGNORECASE):
            insert_idx = i
            break

    if insert_idx is None:
        insert_idx = 0

    return lines[:insert_idx] + dist_block + lines[insert_idx:]


def mark_static_steps(lines):
    static_steps = set()
    i = 0

    while i < len(lines):
        if re.match(r"^\*STEP\b", lines[i].strip(), re.IGNORECASE):
            step_idx = i
            j = i + 1
            is_static = False

            while j < len(lines):
                sj = lines[j].strip()

                if re.match(r"^\*END STEP\b", sj, re.IGNORECASE):
                    break
                if re.match(r"^\*STATIC\b", sj, re.IGNORECASE):
                    is_static = True
                    break
                if re.match(
                        r"^\*(FREQUENCY|BUCKLE|MODAL DYNAMIC|STEADY STATE DYNAMICS|HEAT TRANSFER|COUPLED TEMPERATURE-DISPLACEMENT)\b",
                        sj, re.IGNORECASE
                ):
                    is_static = False
                    break

                j += 1

            if is_static:
                static_steps.add(step_idx)

            i = j
        else:
            i += 1

    return static_steps


def add_adjoint_to_static_steps(lines):
    """
    将静力步改成:
    *Step, ..., SENSITIVITY=ADJOINT
    """
    new_lines = lines[:]

    for idx in mark_static_steps(new_lines):
        line = new_lines[idx].rstrip("\n")

        if re.search(r"\bSENSITIVITY\s*=\s*ADJOINT\b", line, re.IGNORECASE):
            continue

        if re.search(r"\bSENSITIVITY\s*=", line, re.IGNORECASE):
            line = re.sub(
                r"\bSENSITIVITY\s*=\s*([^,\n\r]+)",
                "SENSITIVITY=ADJOINT",
                line,
                flags=re.IGNORECASE
            )
        else:
            line += ", SENSITIVITY=ADJOINT"

        new_lines[idx] = line + "\n"

    return new_lines


def add_design_response(lines, response_nset=None):
    """
    在静力 adjoint 步末尾插入一个最简单的 design response
    默认:
      *Design Response, Name=RESP_U
      *Node Response, NSET=...
      U1
    """
    result = []
    in_step = False
    is_static = False
    is_adjoint = False

    for line in lines:
        s = line.strip()

        if re.match(r"^\*STEP\b", s, re.IGNORECASE):
            in_step = True
            is_static = False
            is_adjoint = bool(re.search(r"\bSENSITIVITY\s*=\s*ADJOINT\b", s, re.IGNORECASE))
            result.append(line)
            continue

        if in_step and re.match(r"^\*STATIC\b", s, re.IGNORECASE):
            is_static = True
            result.append(line)
            continue

        if in_step and re.match(
                r"^\*(FREQUENCY|BUCKLE|MODAL DYNAMIC|STEADY STATE DYNAMICS|HEAT TRANSFER|COUPLED TEMPERATURE-DISPLACEMENT)\b",
                s, re.IGNORECASE
        ):
            is_static = False
            result.append(line)
            continue

        if in_step and re.match(r"^\*END STEP\b", s, re.IGNORECASE):
            if is_static and is_adjoint and response_nset:
                result.append("** ----------------------------------------------------------------\n")
                result.append("*Design Response, Name=RESP_U\n")
                result.append(f"*Node Response, NSET={response_nset}\n")
                result.append("U1\n")
            result.append(line)
            in_step = False
            is_static = False
            is_adjoint = False
            continue

        result.append(line)

    return result


def generate_adjoint_shell_thickness_inp(input_inp, output_inp=None, response_nset=None):
    with open(input_inp, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    sections = extract_shell_sections(lines)
    if not sections:
        raise ValueError("未找到可处理的 *Shell Section。")

    elsets, nsets = extract_sets(lines)

    if response_nset is None:
        response_nset = nsets[0] if nsets else None

    # 生成 distribution 块
    dist_block, dist_map = build_distribution_block(sections)

    # 改写 shell section
    new_lines = rewrite_shell_sections(lines, sections, dist_map)

    # 插入 distribution
    new_lines = insert_distribution_block(new_lines, dist_block)

    # 静力步改成 adjoint
    new_lines = add_adjoint_to_static_steps(new_lines)

    # 插入设计响应
    new_lines = add_design_response(new_lines, response_nset=response_nset)

    # 输出文件名
    if output_inp is None:
        base, ext = os.path.splitext(input_inp)
        output_inp = base + "_adjoint_thickness.inp"

    with open(output_inp, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("处理完成")
    print(f"输入文件: {input_inp}")
    print(f"输出文件: {output_inp}")
    print(f"识别到的 Shell Section 数量: {len(sections)}")
    print(f"Design Response NSET: {response_nset}")
    print("生成的厚度设计变量：")
    for sec in sections:
        print(f"  {sec['elset']} -> THK_{sanitize_name(sec['elset'])}")


if __name__ == "__main__":
    input_file = r"D:\WorkSpace\OtherProjects\702\model\door.inp"  # 改成您的 door.inp 路径
    output_file = r"D:\WorkSpace\OtherProjects\702\model\door_adjoint_thickness.inp"  # 改成输出路径

    # 可手动指定一个已有 NSET 作为设计响应位置；不指定则自动取第一个
    response_nset = None

    generate_adjoint_shell_thickness_inp(
        input_inp=input_file,
        output_inp=output_file,
        response_nset=response_nset
    )
