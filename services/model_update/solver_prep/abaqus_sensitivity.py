# -*- coding: utf-8 -*-
"""
ABAQUS 普通 inp -> 材料 E/u 灵敏度分析 inp 生成器

功能：
1. 提取所有材料的：
   - 弹性模量 E
   - 泊松比 u
2. 生成 designParameter.inp
3. 生成 xxx_sensitivity.inp
4. 在静力步中激活 DSA，并定义 Design Response

注意：
- 只处理材料 *Elastic
- 不处理壳厚度
"""

import os
import re


def abaqus_path(path):
    """将路径转换为 Abaqus 更稳妥的格式"""
    return os.path.abspath(path).replace("\\", "/")


def sanitize_name(name):
    """将名称转换为安全参数名"""
    s = name.strip()
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    if not s:
        s = "PARAM"
    if s[0].isdigit():
        s = "_" + s
    return s


def parse_option_value(line, key):
    """从关键字行中提取 key=value"""
    m = re.search(rf"\b{re.escape(key)}\s*=\s*([^,\n\r]+)", line, re.IGNORECASE)
    return m.group(1).strip() if m else None


def is_number_string(text):
    """判断字符串是否为数值"""
    return re.fullmatch(
        r"[+-]?\d+(?:\.\d*)?(?:[Ee][+-]?\d+)?|\.\d+(?:[Ee][+-]?\d+)?",
        text.strip()
    ) is not None


def extract_sets(lines):
    """提取文件中已有的 ELSET 和 NSET 名称"""
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


def extract_materials(lines):
    """
    提取所有材料的 E / u

    返回：
    {
        "MAT1": {"E": 2.1e11, "u": 0.3},
        ...
    }
    """
    materials = {}
    current_material = None

    i = 0
    while i < len(lines):
        s = lines[i].strip()

        if re.match(r"^\*MATERIAL\b", s, re.IGNORECASE):
            mat_name = parse_option_value(lines[i], "NAME")
            if mat_name:
                current_material = mat_name
                if current_material not in materials:
                    materials[current_material] = {"E": None, "u": None}

        elif current_material and re.match(r"^\*ELASTIC\b", s, re.IGNORECASE):
            j = i + 1
            while j < len(lines):
                sj = lines[j].strip()

                if not sj or sj.startswith("**"):
                    j += 1
                    continue

                if sj.startswith("*"):
                    break

                vals = [x.strip() for x in lines[j].split(",") if x.strip()]
                if len(vals) >= 2:
                    try:
                        materials[current_material]["E"] = float(vals[0])
                        materials[current_material]["u"] = float(vals[1])
                    except ValueError:
                        pass
                break

        i += 1

    return materials


def create_parameter_maps(materials):
    """
    生成参数名映射
    材料：
      材料名_E
      材料名_u
    """
    all_params = {}
    material_map = {}

    for mat_name, info in materials.items():
        safe_name = sanitize_name(mat_name)
        pE = f"{safe_name}_E"
        pu = f"{safe_name}_u"

        if info["E"] is not None:
            all_params[pE] = info["E"]
        if info["u"] is not None:
            all_params[pu] = info["u"]

        material_map[mat_name] = {
            "E": pE,
            "u": pu
        }

    return all_params, material_map


def write_design_parameter_file(filepath, params):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("*Parameter\n")
        for name, value in params.items():
            f.write(f"{name} = {value:.10g}\n")


def replace_material_parameters_precise(lines, material_map):
    """
    只替换对应材料 *Elastic 数据行中的前两个数值
    """
    new_lines = lines[:]
    used_params = []
    current_material = None

    i = 0
    while i < len(new_lines):
        line = new_lines[i]
        s = line.strip()

        if re.match(r"^\*MATERIAL\b", s, re.IGNORECASE):
            current_material = parse_option_value(line, "NAME")

        elif current_material and re.match(r"^\*ELASTIC\b", s, re.IGNORECASE):
            if current_material in material_map:
                pE = material_map[current_material]["E"]
                pu = material_map[current_material]["u"]

                j = i + 1
                while j < len(new_lines):
                    sj = new_lines[j].strip()

                    if not sj or sj.startswith("**"):
                        j += 1
                        continue

                    if sj.startswith("*"):
                        break

                    raw = new_lines[j].rstrip("\r\n")
                    parts = raw.split(",")

                    if len(parts) >= 2:
                        f1 = parts[0].strip()
                        f2 = parts[1].strip()

                        if is_number_string(f1) and is_number_string(f2):
                            lead = re.match(r"^\s*", parts[0]).group(0)
                            parts[0] = f"{lead}<{pE}>"
                            parts[1] = f" <{pu}>"
                            new_lines[j] = ",".join(parts) + "\n"
                            used_params.extend([pE, pu])

                    break

        i += 1

    return new_lines, used_params


def insert_include(lines, include_path):
    include_path = abaqus_path(include_path)

    out = []
    inserted = False

    for line in lines:
        out.append(line)
        if (not inserted) and re.match(r"^\*HEADING\b", line.strip(), re.IGNORECASE):
            out.append(f"*Include, input={include_path}\n")
            inserted = True

    if not inserted:
        out.insert(0, f"*Include, input={include_path}\n")

    return out


def insert_design_parameter_block(lines, design_params):
    step_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^\*STEP\b", line.strip(), re.IGNORECASE):
            step_idx = i
            break

    if step_idx is None or not design_params:
        return lines

    block = []
    block.append("** ----------------------------------------------------------------\n")
    block.append("** DSA PARAMETERS\n")
    block.append("** ----------------------------------------------------------------\n")
    block.append("*Design Parameter\n")
    block.append(",".join(design_params) + "\n")

    for p in design_params:
        block.append("*Dsa Controls\n")
        block.append(f"{p}, CD, 1.0e-4\n")

    block.append("*Dsa Controls, formulation=TOTAL\n")
    block.append("** ----------------------------------------------------------------\n")

    return lines[:step_idx] + block + lines[step_idx:]


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


def add_dsa_yes_to_static_steps(lines):
    new_lines = lines[:]
    for idx in mark_static_steps(new_lines):
        line = new_lines[idx].rstrip("\n")

        if re.search(r"\bDSA\s*=\s*YES\b", line, re.IGNORECASE):
            continue

        if re.search(r"\bDSA\s*=\s*NO\b", line, re.IGNORECASE):
            line = re.sub(r"\bDSA\s*=\s*NO\b", "DSA=YES", line, flags=re.IGNORECASE)
        else:
            line += ", DSA=YES"

        new_lines[idx] = line + "\n"

    return new_lines


def build_design_response_block(response_frequency=1,
                                element_elset=None,
                                node_nset=None,
                                node_vars=None,
                                element_vars=None):
    if node_vars is None:
        node_vars = ["U", "RF"]
    if element_vars is None:
        element_vars = ["S"]

    block = [f"*Design Response, Frequency={response_frequency}\n"]

    if element_elset:
        block.append(f"*Element Response, ELSET={element_elset}\n")
        for v in element_vars:
            block.append(f"{v},\n")

    if node_nset:
        block.append(f"*Node Response, NSET={node_nset}\n")
        for v in node_vars:
            block.append(f"{v},\n")

    return block


def add_design_response(lines,
                        element_elset=None,
                        node_nset=None,
                        response_frequency=1,
                        node_vars=None,
                        element_vars=None):
    out = []
    in_step = False
    is_static = False
    step_has_dsa = False

    for line in lines:
        s = line.strip()

        if re.match(r"^\*STEP\b", s, re.IGNORECASE):
            in_step = True
            is_static = False
            step_has_dsa = bool(re.search(r"\bDSA\s*=\s*YES\b", s, re.IGNORECASE))
            out.append(line)
            continue

        if in_step and re.match(r"^\*STATIC\b", s, re.IGNORECASE):
            is_static = True
            out.append(line)
            continue

        if in_step and re.match(
            r"^\*(FREQUENCY|BUCKLE|MODAL DYNAMIC|STEADY STATE DYNAMICS|HEAT TRANSFER|COUPLED TEMPERATURE-DISPLACEMENT)\b",
            s, re.IGNORECASE
        ):
            is_static = False
            out.append(line)
            continue

        if in_step and re.match(r"^\*END STEP\b", s, re.IGNORECASE):
            if is_static and step_has_dsa:
                out.extend(build_design_response_block(
                    response_frequency=response_frequency,
                    element_elset=element_elset,
                    node_nset=node_nset,
                    node_vars=node_vars,
                    element_vars=element_vars
                ))
            out.append(line)
            in_step = False
            is_static = False
            step_has_dsa = False
            continue

        out.append(line)

    return out


def validate_no_name_corruption(lines):
    text = "".join(lines)

    bad_elset = re.findall(r"elset\s*=\s*[^,\n\r]*<[^>]+>[^,\n\r]*", text, flags=re.IGNORECASE)
    if bad_elset:
        raise ValueError("检测到 ELSET 名称被错误替换：\n" + "\n".join(bad_elset[:10]))

    bad_mat = re.findall(r"material\s*=\s*[^,\n\r]*<[^>]+>[^,\n\r]*", text, flags=re.IGNORECASE)
    if bad_mat:
        raise ValueError("检测到 MATERIAL 名称被错误替换：\n" + "\n".join(bad_mat[:10]))


def validate_design_parameters_used(lines, design_params):
    text = "".join(lines)
    for p in design_params:
        if f"<{p}>" not in text:
            raise ValueError(f"设计参数 {p} 没有真正替换进模型。")


def generate_sensitivity_inp(input_inp,
                             output_dir=None,
                             response_elset=None,
                             response_nset=None,
                             response_frequency=1,
                             node_vars=None,
                             element_vars=None):
    input_inp = os.path.abspath(input_inp)

    if output_dir is None:
        output_dir = os.path.dirname(input_inp)

    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(input_inp))[0]
    design_param_path = os.path.join(output_dir, "designParameter.inp")
    sensitivity_inp_path = os.path.join(output_dir, f"{base_name}_sensitivity.inp")

    with open(input_inp, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    materials = extract_materials(lines)
    elsets, nsets = extract_sets(lines)

    if not materials:
        raise ValueError("未找到 *Material / *Elastic")

    all_params, material_map = create_parameter_maps(materials)

    if response_elset is None and elsets:
        response_elset = elsets[0]
    if response_nset is None and nsets:
        response_nset = nsets[0]

    new_lines = lines[:]

    # 1. 精确替换材料参数
    new_lines, used_mat_params = replace_material_parameters_precise(new_lines, material_map)

    # 2. 只保留真正替换进模型的参数
    used_params = []
    for p in all_params.keys():
        if p in used_mat_params:
            used_params.append(p)

    design_params = {k: all_params[k] for k in used_params}

    # 3. 写参数文件
    write_design_parameter_file(design_param_path, design_params)

    # 4. 插入 include 和 DSA 定义
    new_lines = insert_include(new_lines, design_param_path)
    new_lines = insert_design_parameter_block(new_lines, used_params)
    new_lines = add_dsa_yes_to_static_steps(new_lines)
    new_lines = add_design_response(
        new_lines,
        element_elset=response_elset,
        node_nset=response_nset,
        response_frequency=response_frequency,
        node_vars=node_vars,
        element_vars=element_vars
    )

    # 5. 自检
    validate_no_name_corruption(new_lines)
    validate_design_parameters_used(new_lines, used_params)

    # 6. 输出新 inp
    with open(sensitivity_inp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("处理完成")
    print(f"输入文件: {input_inp}")
    print(f"参数文件: {design_param_path}")
    print(f"灵敏度 inp: {sensitivity_inp_path}")
    print(f"材料设计参数数: {len(used_params)}")
    print(f"Design Response ELSET: {response_elset}")
    print(f"Design Response NSET: {response_nset}")

    return design_param_path, sensitivity_inp_path


if __name__ == "__main__":
    input_file = r"D:\workspaces\myprogram\702\inp\workspace\door.inp"      # 改成您的普通 inp
    output_dir = r"D:\workspaces\myprogram\702\inp\workspace"        # 改成您的输出目录

    # 可手动指定设计响应集合；若为 None，会自动取文件里第一个 ELSET/NSET
    response_elset = None
    response_nset = None

    generate_sensitivity_inp(
        input_inp=input_file,
        output_dir=output_dir,
        response_elset=response_elset,
        response_nset=response_nset,
        response_frequency=1,
        node_vars=["U", "RF"],
        element_vars=["S"]
    )
