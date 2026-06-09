#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cdb_text.py — 从 CDB 文本里抠材料常数 / 截面厚度 / 实常数。

ansys-mapdl-reader 的 Archive 只解析网格（节点、单元、单元属性号），
不解析 MPDATA（材料常数）和 SECBLOCK（截面厚度）。这两块对渲染着色和
model_update 修正参数（E / 厚度 / 密度）是必需的，所以从原始文本解析。

格式都很规整、逗号分隔：
  MPTEMP,R5.0, 1, 1,  0.00000000    ,
  MPDATA,R5.0, 1,DENS,       2, 1, 7.850000000E-09,
  MPDATA,R5.0, 1,EX  ,       1, 1, 2.000000000E+011,
  SECTYPE,      1,SHELL,    ,
  SECBLOCK,      1
       0.200000,         2,     0.000000,         3
  RLBLOCK,       1,       1,      12,       7
  (2i8,6g16.9)
  (7g16.9)
         1       6 0.200000000     0.200000000  ...
"""
import re


def _to_float(tok):
    tok = (tok or "").strip()
    if not tok:
        return None
    try:
        return float(tok)
    except ValueError:
        return None


def _to_int(tok):
    tok = (tok or "").strip()
    if not tok:
        return None
    try:
        return int(float(tok))
    except ValueError:
        return None


def parse_materials(cdb_path):
    """
    解析 MPDATA → {mat_id: {prop_name: value}}。
    prop_name 为 ANSYS 材料属性码（EX, EY, NUXY, PRXY, DENS, GXY, ALPX, KXX, C ...）。
    温度相关材料只取第一个温度点的值（温度无关近似）。
    """
    materials = {}
    with open(cdb_path, "r", errors="replace") as fh:
        for line in fh:
            if not line[:6].upper().startswith("MPDATA"):
                continue
            # MPDATA,R5.0, 1,EX  ,       1, 1, 2.000E+011,
            parts = line.split(",")
            if len(parts) < 7:
                continue
            prop = parts[3].strip().upper()
            mat_id = _to_int(parts[4])
            value = _to_float(parts[6])
            if mat_id is None or value is None or not prop:
                continue
            # 同一 (mat, prop) 多温度点：只保留第一次出现的值
            slot = materials.setdefault(mat_id, {})
            slot.setdefault(prop, value)
    return materials


def parse_sections(cdb_path):
    """
    解析 SECTYPE + SECBLOCK → {sec_id: {'type': 'SHELL'|'BEAM'|..., 'thickness': float|None}}。
    SHELL 截面厚度取 SECBLOCK 各层厚度之和（单层即该层厚度）。
    """
    sections = {}
    lines = open(cdb_path, "r", errors="replace").read().splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        head = line[:8].upper()
        if head.startswith("SECTYPE"):
            parts = line.split(",")
            sec_id = _to_int(parts[1]) if len(parts) > 1 else None
            sec_kind = parts[2].strip().upper() if len(parts) > 2 else ""
            if sec_id is not None:
                sections.setdefault(sec_id, {})["type"] = sec_kind or "UNKNOWN"
        elif head.startswith("SECBLOCK"):
            parts = line.split(",")
            sec_id = _to_int(parts[1]) if len(parts) > 1 else None
            # 后续数据行：每层一行 "TK, MAT, THETA, NUMPT"，直到遇到下一条命令
            total_t = 0.0
            got_layer = False
            j = i + 1
            while j < n:
                dl = lines[j]
                # 命令行（行首是字母关键字）则结束本块
                if re.match(r"^\s*[A-Za-z_]", dl):
                    break
                toks = dl.split(",")
                tk = _to_float(toks[0]) if toks else None
                if tk is not None:
                    total_t += tk
                    got_layer = True
                j += 1
            if sec_id is not None and got_layer:
                sections.setdefault(sec_id, {})["thickness"] = total_t
            i = j - 1
        i += 1
    return sections


def parse_real_constants(cdb_path):
    """
    解析 RLBLOCK → {real_id: [r1, r2, ...]}。
    对 SHELL181 等，R1..R4 是角点厚度；旧式壳厚也存这里。
    返回每个实常数集的原始值列表，调用方按单元类型决定如何取厚度。
    """
    reals = {}
    lines = open(cdb_path, "r", errors="replace").read().splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line[:7].upper().startswith("RLBLOCK"):
            # RLBLOCK, NSET, MAXSET, MAXITEMS, NPERLINE
            # 紧跟两行 Fortran 格式声明 (2i8,6g16.9) / (7g16.9)，再是数据行
            j = i + 1
            # 跳过格式声明行（以 '(' 开头）
            while j < n and lines[j].lstrip().startswith("("):
                j += 1
            # 数据行：每个 set 一条（可能折行），形如 "   set_id  nval  v1 v2 ..."
            while j < n:
                dl = lines[j]
                if re.match(r"^\s*[A-Za-z_(]", dl):
                    break
                nums = re.findall(r"[-+]?\d*\.?\d+(?:[eEdD][-+]?\d+)?", dl)
                if len(nums) >= 2:
                    set_id = _to_int(nums[0])
                    vals = [_to_float(x.replace("D", "E").replace("d", "e"))
                            for x in nums[2:]]
                    if set_id is not None:
                        reals[set_id] = [v for v in vals if v is not None]
                j += 1
            i = j - 1
        i += 1
    return reals


def shell_thickness(sec_id, real_id, sections, reals):
    """综合 SECBLOCK 与 RLBLOCK，给壳单元返回一个代表厚度（优先 SECBLOCK）。"""
    if sec_id is not None and sec_id in sections:
        t = sections[sec_id].get("thickness")
        if t:
            return t
    if real_id is not None and real_id in reals and reals[real_id]:
        return reals[real_id][0]
    return None
