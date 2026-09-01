#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Read FEMtools UNV file:
  - dataset 15: nodes
  - dataset 82: tracelines (geometry lines)
  - dataset 55: modal displacements (data at nodes, normal modes)
and export to VTK with meshio.

"""
import json
import math
from typing import Dict, List, Tuple

from scipy.spatial import cKDTree
from FemNode import FemNode
from MeshElementFactory import MeshElementFactory

import numpy as np
import meshio
from services.model_update.importers.unv_utils import iter_unv_blocks

UNV_2412_SUPPORTED = {
    11: ("line", 2),
    21: ("line", 2),
    22: ("line", 2),
    23: ("line", 3),
    24: ("line", 3),
    41: ("tria3", 3),
    42: ("tria6", 6),
    44: ("quad4", 4),
    45: ("quad8", 8),
    91: ("tria3", 3),
    92: ("tria6", 6),
    94: ("quad4", 4),
    95: ("quad8", 8),
    111: ("tetra4", 4),
    112: ("tetra10", 10),
    115: ("wedge6", 6),
    116: ("wedge15", 15),
    118: ("hexa8", 8),
    119: ("hexa20", 20),
}


def _to_unv_float(text: str) -> float:
    return float(str(text).replace("D", "E").replace("d", "E"))


def _read_unv_lines(filename: str) -> List[str]:
    with open(filename, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding).splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").splitlines(keepends=True)


def _identity_coordinate_system():
    return {
        "label": 0,
        "type": 0,
        "color": 0,
        "name": "GLOBAL",
        "origin": np.asarray([0.0, 0.0, 0.0], dtype=float),
        "axes": np.asarray(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=float,
        ),
    }


def _transform_point_to_global(point, coordinate_systems: Dict[int, dict], cs_label: int):
    coords = np.asarray(point, dtype=float)
    cs = coordinate_systems.get(int(cs_label or 0))
    if cs is None:
        return coords
    if int(cs.get("type", 0)) != 0:
        return coords
    origin = np.asarray(cs.get("origin", [0.0, 0.0, 0.0]), dtype=float)
    axes = np.asarray(cs.get("axes"), dtype=float)
    return origin + coords[0] * axes[0] + coords[1] * axes[1] + coords[2] * axes[2]


def _parse_dataset_2420(lines: List[str], start_idx: int):
    i = start_idx
    coordinate_systems = {}
    unsupported_types = []

    if i >= len(lines):
        return coordinate_systems, unsupported_types, i

    # Record 1 / 2 are part metadata
    if i < len(lines) and lines[i].strip() != "-1":
        i += 1
    if i < len(lines) and lines[i].strip() != "-1":
        i += 1

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line == "-1":
            i += 1
            break

        header = line.split()
        if len(header) < 3:
            i += 1
            continue

        label = int(header[0])
        cs_type = int(header[1])
        color = int(header[2])
        i += 1

        if i >= len(lines):
            break
        name = lines[i].strip() or f"CS_{label}"
        i += 1

        matrix_rows = []
        for _ in range(4):
            if i >= len(lines):
                break
            row_line = lines[i].strip()
            if row_line == "-1":
                break
            row_vals = [_to_unv_float(x) for x in row_line.split()]
            if len(row_vals) >= 3:
                matrix_rows.append(row_vals[:3])
            i += 1

        if cs_type != 0:
            unsupported_types.append({"label": label, "type": cs_type, "name": name})
            continue
        if len(matrix_rows) < 4:
            continue

        coordinate_systems[label] = {
            "label": label,
            "type": cs_type,
            "color": color,
            "name": name,
            "origin": np.asarray(matrix_rows[0], dtype=float),
            "axes": np.asarray(matrix_rows[1:4], dtype=float),
        }

    return coordinate_systems, unsupported_types, i


def _unv2412_read_int_block(lines: List[str], start_idx: int, count: int):
    vals = []
    idx = start_idx
    hit_dataset_end = False

    while idx < len(lines) and len(vals) < count:
        raw = lines[idx].strip()
        if not raw:
            idx += 1
            continue
        if raw == "-1":
            hit_dataset_end = True
            break
        vals.extend(int(x) for x in raw.split())
        idx += 1

    return vals, idx, hit_dataset_end


def _unv2412_trace_polylines(kind: str, node_ids: List[int]) -> List[List[int]]:
    if kind == "line":
        return [node_ids]

    if kind in {"tria3", "tria6"}:
        corners = node_ids[:3]
        return [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[0]],
        ]

    if kind in {"quad4", "quad8"}:
        corners = node_ids[:4]
        return [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[3]],
            [corners[3], corners[0]],
        ]

    if kind in {"tetra4", "tetra10"}:
        corners = node_ids[:4]
        return [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[0]],
            [corners[0], corners[3]],
            [corners[1], corners[3]],
            [corners[2], corners[3]],
        ]

    if kind in {"wedge6", "wedge15"}:
        corners = node_ids[:6]
        return [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[0]],
            [corners[3], corners[4]],
            [corners[4], corners[5]],
            [corners[5], corners[3]],
            [corners[0], corners[3]],
            [corners[1], corners[4]],
            [corners[2], corners[5]],
        ]

    if kind in {"hexa8", "hexa20"}:
        corners = node_ids[:8]
        return [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[3]],
            [corners[3], corners[0]],
            [corners[4], corners[5]],
            [corners[5], corners[6]],
            [corners[6], corners[7]],
            [corners[7], corners[4]],
            [corners[0], corners[4]],
            [corners[1], corners[5]],
            [corners[2], corners[6]],
            [corners[3], corners[7]],
        ]

    return []


def _create_unv2412_element(element_id: int, kind: str, node_ids: List[int]):
    try:
        if kind == "line":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="CBEAM", fem_software="NASTRAN")[0]
            elem.setFaces(node_ids[:2])
            return elem

        if kind == "tria3":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="CTRIA3", fem_software="NASTRAN")[0]
            elem.setFaces(node_ids[:3])
            return elem

        if kind == "tria6":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="S6", fem_software="ABAQUS")[0]
            elem.setFaces(node_ids[:6])
            return elem

        if kind == "quad4":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="CQUAD4", fem_software="NASTRAN")[0]
            elem.setFaces(node_ids[:4])
            return elem

        if kind == "quad8":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="S8", fem_software="ABAQUS")[0]
            elem.setFaces(node_ids[:8])
            return elem

        if kind == "tetra4":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="CTETRA", fem_software="NASTRAN")[0]
            elem.setFaces(node_ids[:4])
            return elem

        if kind == "tetra10":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="C3D10", fem_software="ABAQUS")[0]
            elem.setFaces(node_ids[:10])
            return elem

        if kind == "wedge6":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="CPENTA", fem_software="NASTRAN")[0]
            elem.setFaces(node_ids[:6])
            return elem

        if kind == "wedge15":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="C3D15", fem_software="ABAQUS")[0]
            elem.setFaces(node_ids[:15])
            return elem

        if kind == "hexa8":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="CHEXA", fem_software="NASTRAN")[0]
            elem.setFaces(node_ids[:8])
            return elem

        if kind == "hexa20":
            elem = MeshElementFactory.CreateElement(e_id=element_id, e_type="C3D20", fem_software="ABAQUS")[0]
            elem.setFaces(node_ids[:20])
            return elem
    except Exception:
        return None

    return None


def _parse_dataset_151(records: List[str]) -> dict:
    header = records[0].strip() if records else ""
    fields = records[1].split() if len(records) >= 2 else []
    return {
        "title": header,
        "fields": fields,
        "record_count": len(records),
        "preview": records[:4],
    }


def _parse_dataset_164(records: List[str]) -> dict:
    header = records[0].strip() if records else ""
    fields = records[1].split() if len(records) >= 2 else []
    return {
        "title": header,
        "fields": fields,
        "record_count": len(records),
        "preview": records[:4],
    }


# ----------------------------------------------------------------------
# 1. 解析 UNV：节点(15) + Tracelines(82) + 模态(55)
# ----------------------------------------------------------------------


def parse_unv(filename: str):
    """
    解析 UNV 文件中的:
      - dataset 15: 节点坐标
      - dataset 82: tracelines 几何连线
      - dataset 55: 模态位移 (normal modes)

    返回:
      nodes: { node_id(int): (x, y, z) }
      tracelines: List[List[int]]    # 每个子列表是一条 polyline 的节点号序列
      modes: List[dict]              # 每个 dict 表示一个模态
    """
    message = {}
    lines = _read_unv_lines(filename)

    lines_count = len(lines)
    i = 0

    nodes = []
    nodes_dict = {}
    trace_lines: List[List[int]] = []
    elements = []
    modes: List[dict] = []
    edge_keys_2412 = set()
    coordinate_systems = {0: _identity_coordinate_system()}
    unsupported_coordinate_systems = []

    while i < lines_count:
        line = lines[i].strip()

        # 找到数据块起始标志 -1
        if line != "-1":
            i += 1
            continue

        # 跳过一个或多个连续的 -1（一些程序会写成 -1, -1, dataset_id）
        i += 1
        while i < lines_count and lines[i].strip() == "-1":
            i += 1
        if i >= lines_count:
            break

        ds_line = lines[i].strip()
        # 试着解析数据集编号
        try:
            dataset_id = int(ds_line)
        except ValueError:
            # 不是标准数据集开头，跳过这一行
            i += 1
            continue

        # 数据集编号这一行已经读了，从下一行开始是具体内容
        i += 1

        # ----------------------
        # Dataset 15: Nodes
        # ----------------------
        if dataset_id == 15:
            while i < lines_count:
                l = lines[i].strip()
                if l == "-1":
                    # 数据集结束，跳过这个 -1
                    i += 1
                    break
                if not l:
                    i += 1
                    continue

                parts = l.split()
                # 格式: node, def_cs, disp_cs, color, x, y, z
                if len(parts) >= 7:
                    node_id = int(parts[0])
                    ics = int(parts[1])
                    ocs = int(parts[2])
                    x = _to_unv_float(parts[4])
                    y = _to_unv_float(parts[5])
                    z = _to_unv_float(parts[6])
                    global_coord = _transform_point_to_global([x, y, z], coordinate_systems, ics)
                    gx, gy, gz = (float(global_coord[0]), float(global_coord[1]), float(global_coord[2]))
                    nodes.append(FemNode(node_id, gx, gy, gz, ics, ocs))
                    nodes_dict[node_id] = [gx, gy, gz]

                i += 1

        # ----------------------
        # Dataset 2411: Nodes
        # ----------------------
        elif dataset_id == 2411:
            while i < lines_count:
                l = lines[i].strip()
                if l == "-1":
                    i += 1
                    break
                if not l:
                    i += 1
                    continue

                header_parts = l.split()
                if len(header_parts) < 4:
                    i += 1
                    continue

                node_id = int(header_parts[0])
                ics = int(header_parts[1])
                ocs = int(header_parts[2])
                i += 1

                if i >= lines_count:
                    break

                coord_line = lines[i].strip()
                if coord_line == "-1":
                    break

                coord_parts = coord_line.split()
                if len(coord_parts) < 3:
                    i += 1
                    continue

                x = _to_unv_float(coord_parts[0])
                y = _to_unv_float(coord_parts[1])
                z = _to_unv_float(coord_parts[2])
                global_coord = _transform_point_to_global([x, y, z], coordinate_systems, ics)
                gx, gy, gz = (float(global_coord[0]), float(global_coord[1]), float(global_coord[2]))
                nodes.append(FemNode(node_id, gx, gy, gz, ics, ocs))
                nodes_dict[node_id] = [gx, gy, gz]
                i += 1

        # ----------------------
        # Dataset 2420: Coordinate Systems
        # ----------------------
        elif dataset_id == 2420:
            parsed_coordinate_systems, unsupported_types, i = _parse_dataset_2420(lines, i)
            coordinate_systems.update(parsed_coordinate_systems)
            unsupported_coordinate_systems.extend(unsupported_types)

        # ----------------------
        # Dataset 82: Tracelines
        # ----------------------
        elif dataset_id == 82:
            # Record 1: trace_id, n_nodes, color
            if i >= lines_count:
                break
            header_parts = lines[i].split()
            if len(header_parts) >= 3:
                # trace_id = int(header_parts[0])
                # n_nodes = int(header_parts[1])
                # color = int(header_parts[2])
                pass
            i += 1

            # Record 2: identification line
            if i >= lines_count:
                break
            title_line = lines[i].rstrip("\n")
            i += 1

            # Record 3: 一大串整数，直到遇到 -1
            all_ints: List[int] = []
            while i < lines_count:
                l = lines[i].strip()
                if l == "-1":
                    i += 1
                    break
                if l:
                    all_ints.extend(int(x) for x in l.split())
                i += 1

            # 按 0 分段，构造 polyline
            current_poly: List[int] = []
            for v in all_ints:
                if v == 0:
                    if len(current_poly) > 1:
                        for ii in range(len(current_poly) - 1):
                            iter_ele, _ = MeshElementFactory.CreateElement(e_id=-1, e_type='CBEAM', fem_software='NASTRAN')
                            elements.append(iter_ele)
                            iter_ele.setFaces([current_poly[ii], current_poly[ii + 1]])
                        trace_lines.append(current_poly)
                    current_poly = []
                else:
                    current_poly.append(v)
            if len(current_poly) > 1:
                trace_lines.append(current_poly)
                for ii in range(len(current_poly) - 1):
                    iter_ele, _ = MeshElementFactory.CreateElement(e_id=-1, e_type='CBEAM', fem_software='NASTRAN')
                    elements.append(iter_ele)
                    iter_ele.setFaces([current_poly[ii], current_poly[ii + 1]])

        # ----------------------
        # Dataset 2412: Elements
        # ----------------------
        elif dataset_id == 2412:
            while i < lines_count:
                l = lines[i].strip()
                if not l:
                    i += 1
                    continue
                if l == "-1":
                    i += 1
                    break

                header_vals = [int(x) for x in l.split()]
                if len(header_vals) < 6:
                    i += 1
                    continue

                element_id = int(header_vals[0])
                fe_descriptor_id = int(header_vals[1])
                node_count = int(header_vals[5])
                i += 1

                if node_count <= 0:
                    continue

                node_block, i, hit_dataset_end = _unv2412_read_int_block(lines, i, node_count)
                if len(node_block) < node_count:
                    if hit_dataset_end:
                        break
                    continue

                node_ids = [int(v) for v in node_block[-node_count:]]
                kind_info = UNV_2412_SUPPORTED.get(fe_descriptor_id)
                if kind_info is None:
                    if hit_dataset_end:
                        i += 1
                        break
                    continue

                kind, expected_nodes = kind_info
                if len(node_ids) < expected_nodes:
                    if hit_dataset_end:
                        break
                    continue

                node_ids = node_ids[:expected_nodes]
                elem = _create_unv2412_element(element_id, kind, node_ids)
                if elem is not None:
                    elements.append(elem)

                for poly in _unv2412_trace_polylines(kind, node_ids):
                    if len(poly) < 2:
                        continue
                    key = tuple(sorted((poly[0], poly[-1]))) if len(poly) == 2 else tuple(poly)
                    if key in edge_keys_2412:
                        continue
                    edge_keys_2412.add(key)
                    trace_lines.append(poly)

                if hit_dataset_end:
                    i += 1
                    break

        # ----------------------
        # Dataset 55: Data at Nodes
        # ----------------------
        elif dataset_id == 55:
            """
            https://www.ceas3.uc.edu/sdrluff/view.php
            根据 UFF 规范，前 5 行是 ID line（通常是 "NONE"）

            支持:
              - analysis_type = 2 : Normal Mode
              - analysis_type = 3 : Complex Eigenvalue

            存储策略:
              - analysis_type = 2, data_type = 2:
                    直接保存实数位移
              - analysis_type = 3, data_type = 5:
                    按官方说明读取交错复数:
                        real1, imag1, real2, imag2, ...
                    并转换成幅值保存
                    这样 displacements 的格式仍与 analysis_type == 2 一致
            """
            id_lines: List[str] = []
            for _ in range(5):
                if i >= lines_count:
                    break
                id_lines.append(lines[i].rstrip("\n"))
                i += 1

            if i >= lines_count:
                break

            # Record 6: 6I10
            # model_type, analysis_type, data_ch, spec_data_type, data_type, ndv
            parts = lines[i].split()
            if len(parts) < 6:
                # 格式异常，直接跳到下一个数据集
                i += 1
                while i < lines_count and lines[i].strip() != "-1":
                    i += 1
                if i < lines_count:
                    i += 1
                continue

            model_type = int(parts[0])
            analysis_type = int(parts[1])
            data_ch = int(parts[2])
            spec_data_type = int(parts[3])
            data_type = int(parts[4])
            ndv = int(parts[5])
            i += 1

            # 只处理 Static / Normal Mode / Complex Eigenvalue
            if analysis_type not in (1, 2, 3):
                while i < lines_count and lines[i].strip() != "-1":
                    i += 1
                if i < lines_count:
                    i += 1
                continue

            # Record 7: 8I10
            # analysis_type = 1 / 2 / 3 时:
            #   field3 = load_case
            #   field4 = result number / modal number
            if i >= lines_count:
                break
            parts = lines[i].split()
            load_case = int(parts[2]) if len(parts) >= 3 else 0
            modal_number = int(parts[3]) if len(parts) >= 4 else (len(modes) + 1)
            i += 1

            # Record 8: 6E13.5
            # analysis_type = 2:
            #   field1 = frequency
            # analysis_type = 3:
            #   field1 = real part eigenvalue
            #   field2 = imaginary part eigenvalue
            if i >= lines_count:
                break

            parts = lines[i].split()
            freq = 0.0
            eig_real = 0.0
            eig_imag = 0.0

            load_factor = 0.0

            if analysis_type == 1:
                """
                Static displacement:
                Field 1: Load factor / scale factor (if present)
                Other fields are not used by the current import chain.
                """
                load_factor = float(parts[0]) if len(parts) >= 1 else 0.0
                damping = 0.0
                message["is_static"] = True

            elif analysis_type == 2:
                """
                Field 1: Frequency (Hertz)
                Field 2: Modal Mass
                Field 3: Modal Viscous Damping Ratio
                Field 4: Modal Hysteretic Damping Ratio
                """
                freq = float(parts[0]) if len(parts) >= 1 else 0.0
                damping = float(parts[2])
                message["is_static"] = False

            elif analysis_type == 3:
                """
                Field 1: Real Part Eigenvalue
                Field 2: Imaginary Part Eigenvalue
                Field 3: Real Part Of Modal A
                Field 4: Imaginary Part Of Modal A
                Field 5: Real Part Of Modal B
                Field 6: Imaginary Part Of Modal B
                """
                eig_real = float(parts[0]) if len(parts) >= 1 else 0.0
                eig_imag = float(parts[1]) if len(parts) >= 2 else 0.0
                message["is_static"] = False
                damping = 0

            else:
                raise KeyError(f"Un support analysis type: {analysis_type}")

            i += 1

            # Record 9 & 10: 节点数据
            # Record 9: node number (I10)
            # Record 10: data values
            #
            # data_type:
            #   2 -> Real
            #   5 -> Complex
            #
            # 官方说明:
            #   For Complex Data There Will Be 2*Ndv Data Items At Each Node.
            #   The Order Is Real Part For Value 1, Imaginary Part For Value 1, Etc.
            #
            displacements = {}

            def read_float_values(start_idx: int, count: int):
                """
                从 lines[start_idx] 开始连续读取，直到凑够 count 个浮点数
                返回:
                    vals, next_idx, hit_dataset_end
                """
                vals = []
                idx = start_idx
                hit_dataset_end = False

                while idx < lines_count and len(vals) < count:
                    s = lines[idx].strip()
                    if not s:
                        idx += 1
                        continue
                    if s == "-1":
                        hit_dataset_end = True
                        break
                    vals.extend(float(x) for x in s.split())
                    idx += 1

                return vals, idx, hit_dataset_end

            while i < lines_count:
                l = lines[i].strip()
                if not l:
                    i += 1
                    continue

                if l == "-1":
                    # dataset 55 结束
                    i += 1
                    break

                # Record 9: node number
                try:
                    node_id = int(l.split()[0])
                except ValueError:
                    i += 1
                    continue

                i += 1
                if i >= lines_count:
                    break

                if data_type == 2:
                    """
                    实数: 每节点 NDV 个数, 只取前三个分量作为位移
                    """
                    message["is_real"] = True
                    needed = ndv
                    vals, i, hit_end = read_float_values(i, needed)

                    while len(vals) < ndv:
                        vals.append(0.0)

                    ux = vals[0] if ndv >= 1 else 0.0
                    uy = vals[1] if ndv >= 2 else 0.0
                    uz = vals[2] if ndv >= 3 else 0.0

                    if len(vals) == 6 and analysis_type == 1:
                        displacements[node_id] = {"real": (ux, uy, uz), "imag": (0, 0, 0), "rotate": (vals[3], vals[4], vals[5])}
                    else:
                        displacements[node_id] = {"real": (ux, uy, uz), "imag": (0, 0, 0)}

                    if hit_end:
                        i += 1
                        break

                elif data_type == 5:
                    # 复数: 每节点 2*NDV 个数，按官方顺序交错排列:
                    # real1, imag1, real2, imag2, ...
                    message["is_real"] = False
                    needed = 2 * ndv
                    vals, i, hit_end = read_float_values(i, needed)

                    while len(vals) < needed:
                        vals.append(0.0)

                    real_vals = []
                    imag_vals = []
                    for k in range(ndv):
                        real_part = vals[2 * k]
                        imag_part = vals[2 * k + 1]
                        real_vals.append(real_part)
                        imag_vals.append(imag_part)

                    r_ux = real_vals[0] if ndv >= 1 else 0.0
                    r_uy = real_vals[1] if ndv >= 2 else 0.0
                    r_uz = real_vals[2] if ndv >= 3 else 0.0

                    i_ux = imag_vals[0] if ndv >= 1 else 0.0
                    i_uy = imag_vals[1] if ndv >= 2 else 0.0
                    i_uz = imag_vals[2] if ndv >= 3 else 0.0

                    displacements[node_id] = {"real": (r_ux, r_uy, r_uz), "imag": (i_ux, i_uy, i_uz)}

                    if hit_end:
                        i += 1
                        break

                else:
                    # 未支持的数据类型，跳过整个 dataset
                    while i < lines_count and lines[i].strip() != "-1":
                        i += 1
                    if i < lines_count:
                        i += 1
                    break

            mode_info = {
                "id_lines": id_lines,
                "model_type": model_type,
                "analysis_type": analysis_type,
                "data_ch": data_ch,
                "spec_data_type": spec_data_type,
                "data_type": data_type,
                "ndv": ndv,
                "load_case": load_case,
                "modal_number": modal_number,
                "displacements": displacements,
                "damping": damping
            }

            if analysis_type == 1:
                mode_info["frequency"] = 0.0
                mode_info["load_factor"] = load_factor
            elif analysis_type == 2:
                mode_info["frequency"] = freq
            elif analysis_type == 3:
                mode_info["frequency"] = abs(eig_imag) / (2 * math.pi)
                mode_info["eigenvalue_Re"] = eig_real
                mode_info["eigenvalue_Im"] = eig_imag

            if displacements:
                modes.append(mode_info)

        else:
            while i < lines_count and lines[i].strip() != "-1":
                i += 1
            if i < lines_count:
                i += 1

    """
    对所有模态的阶次进行排序, unv中存储的不一定是按照频率大小排序的
    """
    modes.sort(key=lambda x: x["frequency"])
    for i, item in enumerate(modes, 1):
        item["modal_number"] = i

    message["coordinate_system_count"] = max(len(coordinate_systems) - 1, 0)
    message["unsupported_coordinate_systems"] = unsupported_coordinate_systems
    message["message"] = "Reading..."

    return nodes, nodes_dict, trace_lines, modes, elements, message


def _parse_unv_streaming(filename: str):
    message = {}
    nodes = []
    nodes_dict = {}
    trace_lines: List[List[int]] = []
    elements = []
    modes: List[dict] = []
    edge_keys_2412 = set()
    coordinate_systems = {0: _identity_coordinate_system()}
    unsupported_coordinate_systems = []
    dataset_151 = []
    dataset_164 = []

    for dataset_id_text, records in iter_unv_blocks(filename):
        try:
            dataset_id = int(dataset_id_text)
        except ValueError:
            continue

        if dataset_id == 15:
            for raw_line in records:
                parts = raw_line.split()
                if len(parts) < 7:
                    continue
                node_id = int(parts[0])
                ics = int(parts[1])
                ocs = int(parts[2])
                x = _to_unv_float(parts[4])
                y = _to_unv_float(parts[5])
                z = _to_unv_float(parts[6])
                global_coord = _transform_point_to_global([x, y, z], coordinate_systems, ics)
                gx, gy, gz = (float(global_coord[0]), float(global_coord[1]), float(global_coord[2]))
                nodes.append(FemNode(node_id, gx, gy, gz, ics, ocs))
                nodes_dict[node_id] = [gx, gy, gz]
            continue

        if dataset_id == 2411:
            idx = 0
            while idx < len(records):
                header_parts = records[idx].split()
                idx += 1
                if len(header_parts) < 4 or idx >= len(records):
                    continue

                coord_parts = records[idx].split()
                idx += 1
                if len(coord_parts) < 3:
                    continue

                node_id = int(header_parts[0])
                ics = int(header_parts[1])
                ocs = int(header_parts[2])
                x = _to_unv_float(coord_parts[0])
                y = _to_unv_float(coord_parts[1])
                z = _to_unv_float(coord_parts[2])
                global_coord = _transform_point_to_global([x, y, z], coordinate_systems, ics)
                gx, gy, gz = (float(global_coord[0]), float(global_coord[1]), float(global_coord[2]))
                nodes.append(FemNode(node_id, gx, gy, gz, ics, ocs))
                nodes_dict[node_id] = [gx, gy, gz]
            continue

        if dataset_id == 2420:
            parsed_coordinate_systems, unsupported_types, _ = _parse_dataset_2420(records + ["-1"], 0)
            coordinate_systems.update(parsed_coordinate_systems)
            unsupported_coordinate_systems.extend(unsupported_types)
            continue

        if dataset_id == 82:
            all_ints: List[int] = []
            for raw_line in records[2:]:
                all_ints.extend(int(x) for x in raw_line.split())

            current_poly: List[int] = []
            for value in all_ints:
                if value == 0:
                    if len(current_poly) > 1:
                        for ii in range(len(current_poly) - 1):
                            iter_ele, _ = MeshElementFactory.CreateElement(e_id=-1, e_type="CBEAM", fem_software="NASTRAN")
                            elements.append(iter_ele)
                            iter_ele.setFaces([current_poly[ii], current_poly[ii + 1]])
                        trace_lines.append(current_poly)
                    current_poly = []
                else:
                    current_poly.append(value)
            if len(current_poly) > 1:
                trace_lines.append(current_poly)
                for ii in range(len(current_poly) - 1):
                    iter_ele, _ = MeshElementFactory.CreateElement(e_id=-1, e_type="CBEAM", fem_software="NASTRAN")
                    elements.append(iter_ele)
                    iter_ele.setFaces([current_poly[ii], current_poly[ii + 1]])
            continue

        if dataset_id == 2412:
            idx = 0
            while idx < len(records):
                line = records[idx].strip()
                idx += 1
                if not line:
                    continue

                header_vals = [int(x) for x in line.split()]
                if len(header_vals) < 6:
                    continue

                element_id = int(header_vals[0])
                fe_descriptor_id = int(header_vals[1])
                node_count = int(header_vals[5])
                if node_count <= 0:
                    continue

                node_block, idx, _ = _unv2412_read_int_block(records, idx, node_count)
                if len(node_block) < node_count:
                    continue

                node_ids = [int(v) for v in node_block[-node_count:]]
                kind_info = UNV_2412_SUPPORTED.get(fe_descriptor_id)
                if kind_info is None:
                    continue

                kind, expected_nodes = kind_info
                if len(node_ids) < expected_nodes:
                    continue

                node_ids = node_ids[:expected_nodes]
                elem = _create_unv2412_element(element_id, kind, node_ids)
                if elem is not None:
                    elements.append(elem)

                for poly in _unv2412_trace_polylines(kind, node_ids):
                    if len(poly) < 2:
                        continue
                    key = tuple(sorted((poly[0], poly[-1]))) if len(poly) == 2 else tuple(poly)
                    if key in edge_keys_2412:
                        continue
                    edge_keys_2412.add(key)
                    trace_lines.append(poly)
            continue

        if dataset_id == 55:
            lines = records
            lines_count = len(lines)
            i = 0
            id_lines: List[str] = []
            for _ in range(5):
                if i >= lines_count:
                    break
                id_lines.append(lines[i].rstrip("\n"))
                i += 1

            if i >= lines_count:
                continue

            parts = lines[i].split()
            if len(parts) < 6:
                continue

            model_type = int(parts[0])
            analysis_type = int(parts[1])
            data_ch = int(parts[2])
            spec_data_type = int(parts[3])
            data_type = int(parts[4])
            ndv = int(parts[5])
            i += 1

            if analysis_type not in (1, 2, 3) or i >= lines_count:
                continue

            parts = lines[i].split()
            load_case = int(parts[2]) if len(parts) >= 3 else 0
            modal_number = int(parts[3]) if len(parts) >= 4 else (len(modes) + 1)
            i += 1

            if i >= lines_count:
                continue

            parts = lines[i].split()
            freq = 0.0
            eig_real = 0.0
            eig_imag = 0.0
            load_factor = 0.0

            if analysis_type == 1:
                load_factor = _to_unv_float(parts[0]) if len(parts) >= 1 else 0.0
                damping = 0.0
                message["is_static"] = True
            elif analysis_type == 2:
                freq = _to_unv_float(parts[0]) if len(parts) >= 1 else 0.0
                damping = _to_unv_float(parts[2]) if len(parts) >= 3 else 0.0
                message["is_static"] = False
            else:
                eig_real = _to_unv_float(parts[0]) if len(parts) >= 1 else 0.0
                eig_imag = _to_unv_float(parts[1]) if len(parts) >= 2 else 0.0
                damping = 0.0
                message["is_static"] = False
            i += 1

            displacements = {}

            def read_float_values(start_idx: int, count: int):
                vals = []
                idx = start_idx
                while idx < lines_count and len(vals) < count:
                    s = lines[idx].strip()
                    if not s:
                        idx += 1
                        continue
                    vals.extend(_to_unv_float(x) for x in s.split())
                    idx += 1
                return vals, idx

            while i < lines_count:
                l = lines[i].strip()
                if not l:
                    i += 1
                    continue

                try:
                    node_id = int(l.split()[0])
                except ValueError:
                    i += 1
                    continue

                i += 1
                if i >= lines_count:
                    break

                if data_type == 2:
                    message["is_real"] = True
                    vals, i = read_float_values(i, ndv)
                    while len(vals) < ndv:
                        vals.append(0.0)

                    ux = vals[0] if ndv >= 1 else 0.0
                    uy = vals[1] if ndv >= 2 else 0.0
                    uz = vals[2] if ndv >= 3 else 0.0

                    if len(vals) >= 6 and analysis_type == 1:
                        displacements[node_id] = {
                            "real": (ux, uy, uz),
                            "imag": (0, 0, 0),
                            "rotate": (vals[3], vals[4], vals[5]),
                        }
                    else:
                        displacements[node_id] = {"real": (ux, uy, uz), "imag": (0, 0, 0)}
                    continue

                if data_type == 5:
                    message["is_real"] = False
                    vals, i = read_float_values(i, 2 * ndv)
                    while len(vals) < 2 * ndv:
                        vals.append(0.0)

                    real_vals = []
                    imag_vals = []
                    for k in range(ndv):
                        real_vals.append(vals[2 * k])
                        imag_vals.append(vals[2 * k + 1])

                    displacements[node_id] = {
                        "real": (
                            real_vals[0] if ndv >= 1 else 0.0,
                            real_vals[1] if ndv >= 2 else 0.0,
                            real_vals[2] if ndv >= 3 else 0.0,
                        ),
                        "imag": (
                            imag_vals[0] if ndv >= 1 else 0.0,
                            imag_vals[1] if ndv >= 2 else 0.0,
                            imag_vals[2] if ndv >= 3 else 0.0,
                        ),
                    }
                    continue

                break

            mode_info = {
                "id_lines": id_lines,
                "model_type": model_type,
                "analysis_type": analysis_type,
                "data_ch": data_ch,
                "spec_data_type": spec_data_type,
                "data_type": data_type,
                "ndv": ndv,
                "load_case": load_case,
                "modal_number": modal_number,
                "displacements": displacements,
                "damping": damping,
            }

            if analysis_type == 1:
                mode_info["frequency"] = 0.0
                mode_info["load_factor"] = load_factor
            elif analysis_type == 2:
                mode_info["frequency"] = freq
            else:
                mode_info["frequency"] = abs(eig_imag) / (2 * math.pi)
                mode_info["eigenvalue_Re"] = eig_real
                mode_info["eigenvalue_Im"] = eig_imag

            modes.append(mode_info)
            continue

        if dataset_id == 151:
            dataset_151.append(_parse_dataset_151(records))
            continue

        if dataset_id == 164:
            dataset_164.append(_parse_dataset_164(records))
            continue

    modes.sort(key=lambda x: x["frequency"])
    for idx, item in enumerate(modes, 1):
        item["modal_number"] = idx

    message["coordinate_system_count"] = max(len(coordinate_systems) - 1, 0)
    message["unsupported_coordinate_systems"] = unsupported_coordinate_systems
    message["dataset_151"] = dataset_151
    message["dataset_164"] = dataset_164
    message["dataset_151_count"] = len(dataset_151)
    message["dataset_164_count"] = len(dataset_164)
    message["message"] = "Reading..."

    return nodes, nodes_dict, trace_lines, modes, elements, message


parse_unv = _parse_unv_streaming


# ----------------------------------------------------------------------
# 2. 用节点 + tracelines 构造 meshio.Mesh
# ----------------------------------------------------------------------


def build_mesh_from_tracelines(
        nodes: Dict[int, Tuple[float, float, float]],
        # nodes: List[FemNode],
        tracelines: List[List[int]],
):
    """
    根据节点和 traceline polyline 构造 meshio.Mesh:

    - points: 所有节点坐标
    - cells: line 元素，每一条 polyline 拆成若干 line (p[i], p[i+1])

    返回:
      mesh: meshio.Mesh
      node_ids_sorted: List[int]     # 排好序的节点号
      id2idx: { node_id: point_index }
    """
    if not nodes:
        raise ValueError("No nodes found in UNV file (dataset 15 is empty).")

    node_ids_sorted = sorted(nodes.keys())
    id2idx = {nid: idx for idx, nid in enumerate(node_ids_sorted)}
    points = np.array([nodes[nid] for nid in node_ids_sorted], dtype=float)

    line_cells = []
    for poly in tracelines:
        # 只保留存在于节点表中的节点
        poly_filtered = [nid for nid in poly if nid in id2idx]
        if len(poly_filtered) < 2:
            continue
        for a, b in zip(poly_filtered[:-1], poly_filtered[1:]):
            line_cells.append([id2idx[a], id2idx[b]])

    if line_cells:
        cells = [("line", np.array(line_cells, dtype=int))]
    else:
        cells = []

    mesh = meshio.Mesh(points=points, cells=cells)
    return mesh, node_ids_sorted, id2idx


# ----------------------------------------------------------------------
# 3. 把各个模态位移写入 mesh.point_data
# ----------------------------------------------------------------------


def add_modes_to_mesh(
        mesh: meshio.Mesh,
        node_ids_sorted: List[int],
        id2idx: Dict[int, int],
        modes: List[dict],
):
    """
    对每个模态构造一个 (Npoints, 3) 的位移向量，并挂到 mesh.point_data 里。

    数组名形如: mode_001_f_44.433_Hz
    """
    npoints = len(node_ids_sorted)

    for i_mode, mode in enumerate(modes, start=1):
        displacements = mode["displacements"]["imag"]
        freq = mode.get("frequency", 0.0)
        modal_number = mode.get("modal_number") or i_mode

        # 所有点先置零
        vec = np.zeros((npoints, 3), dtype=float)

        # 把有数据的节点填进去
        for nid, (ux, uy, uz) in displacements.items():
            idx = id2idx.get(nid, None)
            if idx is None:
                continue
            vec[idx, :] = (ux, uy, uz)

        name = f"mode_{modal_number:03d}_f_{freq:.3f}_Hz"
        mesh.point_data[name] = vec

    return mesh


def write_mesh_to_json(json_dir_p, mesh_p, deform_p):
    json_data = {"data": {},
                 "metadata": {"type": "BufferGeometry", "version": 4},
                 "name": "XiGuiChe_Line",
                 "type": "BufferGeometry",
                 "userData": {},
                 "uuid": "946A1C8F-19EF-1CA6-8B63-B4745CB83B4B"}

    json_data["data"]["attributes"] = {}
    json_data["data"]["index"] = {}

    json_data["data"]["interleavedBuffers"] = {"Result": {"buffer": "1CE1796E-3F05-2251-2DCC-2C2D14F13B3F",
                                                          "stride": 1,
                                                          "type": "Float32Array",
                                                          "uuid": "FF275754-4711-AC0A-21EA-69246C54D02C"}}
    json_data["data"]["attributes"]["position"] = {"array": [], "itemSize": 3, "type": "Float32Array"}
    json_data["data"]["index"] = {"array": [], "itemSize": 1, "type": "Uint32Array"}
    json_data["data"]["content"] = ["modal"]
    json_data["data"]["index"]["array"] = mesh_p.cells[0].data.flatten().tolist()
    for k, v in mesh_p.point_data.items():
        if deform_p:
            origin_xyz = mesh_p.points.flatten()
            deform_xyz = np.zeros((len(origin_xyz)), dtype=float)
            for ii in range(int(len(origin_xyz) / 3)):
                deform_xyz[ii * 3] = v[ii, 0]
                deform_xyz[ii * 3 + 1] = v[ii, 1]
                deform_xyz[ii * 3 + 2] = v[ii, 2]
            json_data["data"]["attributes"]["position"]["array"] = (origin_xyz + deform_xyz).tolist()
        else:
            json_data["data"]["attributes"]["position"]["array"] = mesh_p.points.flatten().tolist()

        mag = np.sqrt(v[:, 0] ** 2 + v[:, 1] ** 2 + v[:, 2] ** 2)
        json_data["data"]["arrayBuffers"] = {"1CE1796E-3F05-2251-2DCC-2C2D14F13B3F": mag.tolist()}
        with open(json_dir_p + f"/{k}.json", 'w') as f:
            json.dump(json_data, f, indent=4)


def find_nearest_node(mesh_p, json_file, output_file):
    """
    找出离试验节点最近的有限元模型的节点坐标
    :param mesh_p: UNV文件中的节点，试验节点
    :param json_file:
    :param output_file:
    :return:
    """
    with open(json_file, 'r') as f:
        json_data = json.load(f)
    fem_node_position = np.reshape(np.array(json_data['data']["attributes"]["position"]['array']), (-1, 3))
    node_tree = cKDTree(fem_node_position)
    sensor_node = mesh_p.points
    sensor_write_pos = []
    fem_write_pos = []
    for node in sensor_node:
        nearest_node_index = node_tree.query(node)[1]
        near_node = fem_node_position[nearest_node_index].tolist()
        sensor_write_pos.append(node.tolist())
        fem_write_pos.append(near_node)

    with open(output_file, 'w') as f:
        json.dump({"fem": fem_write_pos, "lab": sensor_write_pos}, f, indent=4)


if __name__ == "__main__":
    # input_unv = "../PyModel2JsonDataFolder/model/femtools/power_train/power_train.unv"
    input_unv = "../PyModel2JsonDataFolder/model/femtools/engine/engine.unv"
    # input_unv = "../PyModel2JsonDataFolder/model/femtools/plate_modal/plate.unv"
    # input_unv = "../PyModel2JsonDataFolder/model/femtools/doe/assembly.unv"
    output_vtk = "../PyModel2JsonDataFolder/output/engine.vtk"

    print(f"Reading UNV file: {input_unv}")
    r_nodes, r_nodes_dict, r_trace_lines, r_modes, r_elements, _ = parse_unv(input_unv)
    print(f"  Nodes read: {len(r_nodes)}")
    print(f"  Tracelines polylines: {len(r_trace_lines)}")
    print(f"  Modes found (dataset 55): {len(r_modes)}")

    mesh, node_ids_sorted, id2idx = build_mesh_from_tracelines(r_nodes_dict, r_trace_lines)
    mesh = add_modes_to_mesh(mesh, node_ids_sorted, id2idx, r_modes)

    print(f"Writing VTK file: {output_vtk}")
    mesh.write(output_vtk)

    # output_json_dir = "./output/"
    # print(f"Writing Json file: {output_json_dir}")
    # write_mesh_to_json(output_json_dir, mesh, True)

    # find_nearest_node(mesh, r"D:\WorkSpace\WebThreeJS\PyModelToJson\output\fem_rotated_undeform.json",
    #                   "./output/nearst_.json")
    print("Done.")
