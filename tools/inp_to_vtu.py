"""
INP → VTU 转换脚本

将解析后的 INP 文件写成 VTU（用 meshio），可在 ParaView / Paraview Web 中打开。

用法:
    python tools/inp_to_vtu.py model.inp output.vtu
    python tools/inp_to_vtu.py model.inp output.vtu --no-transform  # 保留 Part 局部坐标

输出说明:
    - 所有 Instance 按变换矩阵组装到全局坐标系
    - 每个单元附带 cell_data:
        - "part_idx"     : 来自哪个 Part（整数序号）
        - "instance_idx" : 来自哪个 Instance（整数序号）
        - "element_label": 原始 Abaqus 单元标签
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

try:
    import meshio
except ImportError:
    print("Error: meshio not installed.  Run: pip install meshio")
    sys.exit(1)

from src.inp import parse_inp, InpModel


# factory_type → meshio cell type string
FACTORY_TO_MESHIO = {
    "TET4":    "tetra",
    "TET10":   "tetra10",
    "WEDGE6":  "wedge",
    "WEDGE15": "wedge15",
    "HEX8":    "hexahedron",
    "HEX20":   "hexahedron20",
    "TRI3":    "triangle",
    "TRI6":    "triangle6",
    "QUAD4":   "quad",
    "QUAD8":   "quad8",
    "LINE2":   "line",
    "LINE3":   "line3",
    "POINT":   "vertex",
}


def build_mesh(model: InpModel, apply_transforms: bool = True):
    """
    Build (points, cells, cell_data) suitable for meshio.write().
    """
    points = []
    point_idx = {}    # (scope_key, node_label) -> 0-based index

    cells_by_type   = {}   # meshio_type -> list of connectivity rows
    cd_part_idx     = {}   # meshio_type -> list of part integers
    cd_inst_idx     = {}   # meshio_type -> list of instance integers
    cd_elem_label   = {}   # meshio_type -> list of element labels

    part_names = list(model.parts.keys())
    inst_names = []

    def add_node(scope_key, label, x, y, z, transform=None):
        if (scope_key, label) in point_idx:
            return point_idx[(scope_key, label)]
        p = np.array([x, y, z, 1.0], dtype=np.float64)
        if transform is not None and apply_transforms:
            p = transform @ p
        points.append(p[:3])
        idx = len(points) - 1
        point_idx[(scope_key, label)] = idx
        return idx

    def add_element(scope_key, part_i, inst_i, elem):
        meshio_type = FACTORY_TO_MESHIO.get(elem.factory_type)
        if meshio_type is None:
            return
        conn = [point_idx.get((scope_key, nl)) for nl in elem.node_labels]
        if any(c is None for c in conn):
            return   # some nodes were missing — skip

        if meshio_type not in cells_by_type:
            cells_by_type[meshio_type]  = []
            cd_part_idx[meshio_type]    = []
            cd_inst_idx[meshio_type]    = []
            cd_elem_label[meshio_type]  = []

        cells_by_type[meshio_type].append(conn)
        cd_part_idx[meshio_type].append(part_i)
        cd_inst_idx[meshio_type].append(inst_i)
        cd_elem_label[meshio_type].append(elem.label)

    if model.assembly and model.assembly.instances:
        # Assembled mode: apply per-instance transform
        for inst_i, (inst_name, inst) in enumerate(model.assembly.instances.items()):
            inst_names.append(inst_name)
            part = model.parts.get(inst.part_name)
            if part is None:
                print(f"  [WARN] Instance '{inst_name}': Part '{inst.part_name}' not found, skipped")
                continue
            part_i = part_names.index(inst.part_name)
            transform = getattr(inst, "transform_matrix", None)

            # Add nodes
            for label, node in part.nodes.items():
                add_node((inst_name, "n"), label, node.x, node.y, node.z, transform)

            # Add elements
            for elem in part.elements.values():
                # remap scope_key to the per-instance node space
                # (already added above with key (inst_name, "n"))
                elem_copy_scope = (inst_name, "n")
                # rebuild a temporary scope lookup
                add_element(elem_copy_scope, part_i, inst_i, elem)
    else:
        # No assembly: dump all parts in local coordinates
        for part_i, (part_name, part) in enumerate(model.parts.items()):
            scope_key = (part_name, "n")
            for label, node in part.nodes.items():
                add_node(scope_key, label, node.x, node.y, node.z)
            for elem in part.elements.values():
                add_element(scope_key, part_i, 0, elem)

    if not points:
        print("No points to write — model may be empty.")
        return None, None, None

    points_arr = np.array(points, dtype=np.float64)

    cells = [(t, np.array(rows, dtype=np.int64))
             for t, rows in cells_by_type.items()]

    cell_data = {
        "part_idx":     [np.array(cd_part_idx[t],   dtype=np.int32)  for t, _ in cells],
        "instance_idx": [np.array(cd_inst_idx[t],   dtype=np.int32)  for t, _ in cells],
        "element_label":[np.array(cd_elem_label[t], dtype=np.int64)  for t, _ in cells],
    }

    return points_arr, cells, cell_data


def main():
    arg_use = False
    if arg_use:
        parser = argparse.ArgumentParser(description="Convert Abaqus INP to VTU via meshio")
        parser.add_argument("inp", help="Input .inp file")
        parser.add_argument("vtu", help="Output .vtu file")
        parser.add_argument("--no-transform", action="store_true",
                            help="Write in Part local coordinates (ignore instance transforms)")
        args = parser.parse_args()

        print(f"Parsing {args.inp} ...")
        model = parse_inp(args.inp)
    else:
        # For quick dev testing without CLI args
        # inp_path = "data/abaqus/assembly_example.inp"
        # inp_path = r"D:\WorkSpace\WebThreeJS\PyModel2JsonDataFolder\model\inp\door.inp"
        inp_path = r"D:\WorkSpace\FEM\Abaqus\2023\win_b64\SMA\samples\job_archive\samples\ReactorHead_reference.inp"
        print(f"Parsing {inp_path} ...")
        model = parse_inp(inp_path)
        args = argparse.Namespace(
            vtu="output.vtu",
            no_transform=False,
        )

    # Print diagnostics
    errors   = [d for d in model.diagnostics if d.severity == "ERROR"]
    warnings = [d for d in model.diagnostics if d.severity == "WARNING"]
    if errors:
        print(f"  {len(errors)} error(s):")
        for d in errors:
            print(f"    {d}")
    if warnings:
        print(f"  {len(warnings)} warning(s):")
        for d in warnings[:10]:
            print(f"    {d}")
        if len(warnings) > 10:
            print(f"    ... and {len(warnings)-10} more")

    apply_transforms = not args.no_transform
    print(f"Building mesh (apply_transforms={apply_transforms}) ...")
    points, cells, cell_data = build_mesh(model, apply_transforms)

    if points is None:
        sys.exit(1)

    total_cells = sum(len(c) for _, c in cells)
    print(f"  Points  : {len(points)}")
    print(f"  Cells   : {total_cells}")
    for t, arr in cells:
        print(f"    {t:20s} × {len(arr)}")

    print(f"Writing {args.vtu} ...")
    meshio.write(
        args.vtu,
        meshio.Mesh(
            points=points,
            cells=cells,
            cell_data=cell_data,
        )
    )
    print("Done.")


if __name__ == "__main__":
    main()
