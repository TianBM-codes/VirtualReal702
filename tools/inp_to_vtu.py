"""
INP → VTU 转换脚本

将解析后的 INP 文件写成 VTU（用 meshio），可在 ParaView / Paraview Web 中打开。

命令行用法:
    python tools/inp_to_vtu.py model.inp output.vtu
    python tools/inp_to_vtu.py model.inp output.vtu --no-transform
    python tools/inp_to_vtu.py model.inp output.vtu \\
        --node-results node_data.json --cell-results cell_data.json

Python 函数用法（验证计算结果）:
    from tools.inp_to_vtu import write_vtu

    # 节点结果（每个节点一个值或一个向量）
    write_vtu("model.inp", "out.vtu",
        node_results={
            "displacement": {101: [0.001, 0.002, 0.0],
                             102: [0.003, 0.001, 0.0]},
            "temperature":  {101: 25.3, 102: 26.1},
        }
    )

    # 单元结果（每个单元一个值或一个向量）
    write_vtu("model.inp", "out.vtu",
        cell_results={
            "stress_mises": {1001: 125.4, 1002: 98.7},
            "stress_tensor": {1001: [120.0, 110.0, 90.0, 5.0, 3.0, 2.0]},
        }
    )

    # 两种同时传
    write_vtu("model.inp", "out.vtu",
        node_results={"U": {101: [0.001, 0.0, 0.0]}},
        cell_results={"S_MISES": {1001: 125.4}},
    )

结果 JSON 文件格式（命令行用）:
    {
      "displacement": {"101": [0.001, 0.002, 0.0], "102": [0.003, 0.001, 0.0]},
      "temperature":  {"101": 25.3, "102": 26.1}
    }

输出说明:
    - 所有 Instance 按变换矩阵组装到全局坐标系
    - cell_data 始终包含: part_idx / instance_idx / element_label
    - 传入的节点/单元结果写入 point_data / cell_data
    - 找不到对应标签的节点/单元填 NaN（ParaView 中显示为透明）
"""
import argparse
import json
import sys
import os
from typing import Any, Dict, List, Optional, Tuple, Union

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

try:
    import meshio
except ImportError:
    print("Error: meshio not installed.  Run: pip install meshio")
    sys.exit(1)

from config import get_local_service_base_url
from src.inp import parse_inp, InpModel
from tools.odb_client import ODBClient

# 结果类型别名
#   node_results : {"字段名": {节点标签(int): float 或 list[float]}}
#   cell_results : {"字段名": {单元标签(int): float 或 list[float]}}
ResultValue = Union[float, List[float]]
ResultKey = Union[int, str, Tuple[str, int], Tuple[str, str]]
NodeResults = Dict[str, Dict[ResultKey, ResultValue]]
CellResults = Dict[str, Dict[ResultKey, ResultValue]]
SCOPED_LABEL_SEP = "::"
_ROOT_SCOPE_ALIASES = ("__root__", "PART-1-1", "PART1-1")


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


def _parse_result_key(key: ResultKey) -> Tuple[Optional[str], int]:
    if isinstance(key, (tuple, list)):
        if len(key) != 2:
            raise ValueError(f"Invalid scoped result key: {key!r}")
        return str(key[0]), int(key[1])

    if isinstance(key, (int, np.integer)):
        return None, int(key)

    if isinstance(key, str):
        if SCOPED_LABEL_SEP in key:
            instance, label = key.split(SCOPED_LABEL_SEP, 1)
            return instance, int(label)
        return None, int(key)

    raise ValueError(f"Unsupported result key type: {type(key)!r}")


def _coerce_json_result_key(key: str) -> ResultKey:
    if SCOPED_LABEL_SEP in key:
        instance, label = key.split(SCOPED_LABEL_SEP, 1)
        return f"{instance}{SCOPED_LABEL_SEP}{int(label)}"
    return int(key)


def _merge_result_sets(
    base: Optional[Dict[str, Dict[ResultKey, ResultValue]]],
    extra: Optional[Dict[str, Dict[ResultKey, ResultValue]]],
) -> Optional[Dict[str, Dict[ResultKey, ResultValue]]]:
    merged: Dict[str, Dict[ResultKey, ResultValue]] = {}
    if base:
        merged.update(base)
    if extra:
        merged.update(extra)
    return merged or None


def build_mesh(model: InpModel, apply_transforms: bool = True):
    """
    Build (points, cells, cell_data, label_to_pidxs) suitable for meshio.write().

    label_to_pidxs : {node_label: [global_point_index, ...]}
        同一节点标签出现在多个 Instance 时会有多个全局索引，
        write_vtu() 会把相同的结果值写入所有对应位置。
    """
    points = []
    point_idx = {}    # (scope_key, node_label) -> 0-based index
    label_to_pidxs: Dict[int, List[int]] = {}  # node_label -> [global_idx, ...]
    scoped_label_to_pidxs: Dict[Tuple[str, int], List[int]] = {}

    cells_by_type   = {}   # meshio_type -> list of connectivity rows
    cd_part_idx     = {}   # meshio_type -> list of part integers
    cd_inst_idx     = {}   # meshio_type -> list of instance integers
    cd_elem_label   = {}   # meshio_type -> list of element labels
    elem_label_to_cells: Dict[int, List[Tuple[str, int]]] = {}
    scoped_elem_label_to_cells: Dict[Tuple[str, int], List[Tuple[str, int]]] = {}

    part_names = list(model.parts.keys())
    inst_names = []
    has_root_only_part = (
        not (model.assembly and model.assembly.instances)
        and list(model.parts.keys()) == ["__root__"]
    )

    def result_scope_aliases(result_scope):
        if has_root_only_part and result_scope == "__root__":
            return _ROOT_SCOPE_ALIASES
        return (result_scope,)

    def add_node(scope_key, result_scope, label, x, y, z, transform=None):
        if (scope_key, label) in point_idx:
            return point_idx[(scope_key, label)]
        p = np.array([x, y, z, 1.0], dtype=np.float64)
        if transform is not None and apply_transforms:
            p = transform @ p
        points.append(p[:3])
        idx = len(points) - 1
        point_idx[(scope_key, label)] = idx
        label_to_pidxs.setdefault(label, []).append(idx)
        for alias in result_scope_aliases(result_scope):
            scoped_label_to_pidxs.setdefault((alias, label), []).append(idx)
        return idx

    def add_element(scope_key, result_scope, part_i, inst_i, elem):
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

        row_idx = len(cells_by_type[meshio_type])
        cells_by_type[meshio_type].append(conn)
        cd_part_idx[meshio_type].append(part_i)
        cd_inst_idx[meshio_type].append(inst_i)
        cd_elem_label[meshio_type].append(elem.label)
        elem_label_to_cells.setdefault(elem.label, []).append((meshio_type, row_idx))
        for alias in result_scope_aliases(result_scope):
            scoped_elem_label_to_cells.setdefault((alias, elem.label), []).append((meshio_type, row_idx))

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
                add_node((inst_name, "n"), inst_name, label, node.x, node.y, node.z, transform)

            # Add elements
            for elem in part.elements.values():
                # remap scope_key to the per-instance node space
                # (already added above with key (inst_name, "n"))
                elem_copy_scope = (inst_name, "n")
                # rebuild a temporary scope lookup
                add_element(elem_copy_scope, inst_name, part_i, inst_i, elem)
    else:
        # No assembly: dump all parts in local coordinates
        for part_i, (part_name, part) in enumerate(model.parts.items()):
            scope_key = (part_name, "n")
            for label, node in part.nodes.items():
                add_node(scope_key, part_name, label, node.x, node.y, node.z)
            for elem in part.elements.values():
                add_element(scope_key, part_name, part_i, 0, elem)

    if not points:
        print("No points to write — model may be empty.")
        return None, None, None, None, None, None, None

    points_arr = np.array(points, dtype=np.float64)

    cells = [(t, np.array(rows, dtype=np.int64))
             for t, rows in cells_by_type.items()]

    cell_data = {
        "part_idx":     [np.array(cd_part_idx[t],   dtype=np.int32)  for t, _ in cells],
        "instance_idx": [np.array(cd_inst_idx[t],   dtype=np.int32)  for t, _ in cells],
        "element_label":[np.array(cd_elem_label[t], dtype=np.int64)  for t, _ in cells],
    }

    return (
        points_arr,
        cells,
        cell_data,
        label_to_pidxs,
        scoped_label_to_pidxs,
        elem_label_to_cells,
        scoped_elem_label_to_cells,
    )


def _lookup_result_targets(
    key: ResultKey,
    unscoped_index: Dict[int, List[Any]],
    scoped_index: Dict[Tuple[str, int], List[Any]],
) -> List[Any]:
    instance_name, label = _parse_result_key(key)
    if instance_name is not None:
        return scoped_index.get((instance_name, label), [])
    return unscoped_index.get(label, [])


def _normalize_result_value(value: ResultValue) -> ResultValue:
    arr = np.asarray(value)
    if arr.ndim == 0:
        return float(arr.item())
    if arr.ndim == 1 and arr.size == 1:
        return float(arr.reshape(-1)[0].item())
    return arr.tolist()


def write_vtu(
    inp: Union[str, "InpModel"],
    output: str,
    *,
    node_results: Optional[NodeResults] = None,
    cell_results: Optional[CellResults] = None,
    apply_transforms: bool = True,
) -> None:
    """
    INP 文件（或已解析的 InpModel）写成 VTU，可附带节点/单元结果。

    参数
    ----
    inp : str | InpModel
        INP 文件路径，或已经 parse_inp() 得到的 InpModel 对象。

    output : str
        输出 .vtu 文件路径。

    node_results : dict, 可选
        节点结果，格式：
            {"字段名": {节点标签(int): float 或 [v1, v2, ...]}}
        多分量示例（位移）：
            {"U": {101: [0.001, 0.002, 0.0], 102: [0.003, 0.001, 0.0]}}
        标量示例（温度）：
            {"T": {101: 25.3, 102: 26.1}}
        找不到对应节点标签时填 NaN。

    cell_results : dict, 可选
        单元结果，格式：
            {"字段名": {单元标签(int): float 或 [v1, v2, ...]}}
        标量示例（Von Mises 应力）：
            {"S_MISES": {1001: 125.4, 1002: 98.7}}
        多分量示例（应力张量）：
            {"S": {1001: [120.0, 110.0, 90.0, 5.0, 3.0, 2.0]}}
        找不到对应单元标签时填 NaN。

    apply_transforms : bool
        True（默认）：将所有 Instance 变换到全局坐标系。
        False：保留 Part 局部坐标（仅调试用）。

    输出的 VTU 包含：
        point_data  — 来自 node_results 的各字段
        cell_data   — part_idx / instance_idx / element_label（固定）
                      + 来自 cell_results 的各字段
    """
    if isinstance(inp, str):
        model = parse_inp(inp)
    else:
        model = inp

    (
        points,
        cells,
        cell_data,
        label_to_pidxs,
        scoped_label_to_pidxs,
        elem_label_to_cells,
        scoped_elem_label_to_cells,
    ) = build_mesh(model, apply_transforms)
    if points is None:
        raise ValueError("模型为空，无法生成 VTU")

    # ── 节点结果 → point_data ──────────────────────────────────────────────────
    point_data: Dict[str, np.ndarray] = {}
    if node_results:
        n_pts = len(points)
        for field_name, label_values in node_results.items():
            if not label_values:
                continue
            # 判断标量还是向量
            first_val = _normalize_result_value(next(iter(label_values.values())))
            is_scalar = np.isscalar(first_val)
            ncomp = 1 if is_scalar else len(first_val)

            if is_scalar:
                arr = np.full(n_pts, np.nan, dtype=np.float64)
            else:
                arr = np.full((n_pts, ncomp), np.nan, dtype=np.float64)

            missing = 0
            for label, val in label_values.items():
                pidxs = _lookup_result_targets(label, label_to_pidxs, scoped_label_to_pidxs)
                if not pidxs:
                    missing += 1
                    continue
                normalized_val = _normalize_result_value(val)
                for pidx in pidxs:
                    arr[pidx] = normalized_val

            if missing:
                print(f"  [WARN] node_results['{field_name}']: "
                      f"{missing} 个节点标签在模型中未找到")
            point_data[field_name] = arr

    # ── 单元结果 → cell_data（追加到已有的 part_idx 等字段之后）────────────────
    if cell_results:
        # 预先建立 elem_label → (type_idx, row_idx) 索引，方便 O(1) 查找
        # cell_data["element_label"] 是与 cells 列表平行的数组列表
        type_to_cell_idx = {cell_type: idx for idx, (cell_type, _) in enumerate(cells)}

        for field_name, label_values in cell_results.items():
            if not label_values:
                continue
            first_val = _normalize_result_value(next(iter(label_values.values())))
            is_scalar = np.isscalar(first_val)
            ncomp = 1 if is_scalar else len(first_val)

            # 为每种单元类型准备一个数组
            if is_scalar:
                per_type = [
                    np.full(len(lbl_arr), np.nan, dtype=np.float64)
                    for lbl_arr in cell_data["element_label"]
                ]
            else:
                per_type = [
                    np.full((len(lbl_arr), ncomp), np.nan, dtype=np.float64)
                    for lbl_arr in cell_data["element_label"]
                ]

            missing = 0
            for label, val in label_values.items():
                positions = _lookup_result_targets(
                    label,
                    elem_label_to_cells,
                    scoped_elem_label_to_cells,
                )
                if not positions:
                    missing += 1
                    continue
                normalized_val = _normalize_result_value(val)
                for cell_type, ri in positions:
                    per_type[type_to_cell_idx[cell_type]][ri] = normalized_val

            if missing:
                print(f"  [WARN] cell_results['{field_name}']: "
                      f"{missing} 个单元标签在模型中未找到")
            cell_data[field_name] = per_type

    meshio.write(
        output,
        meshio.Mesh(
            points=points,
            cells=cells,
            point_data=point_data,
            cell_data=cell_data,
        ),
    )


def _load_results_json(path: str) -> dict:
    """从 JSON 文件读取结果，把字符串 key 转成 int。"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        field: {_coerce_json_result_key(k): v for k, v in label_vals.items()}
        for field, label_vals in raw.items()
    }


def _default_odb_result_name(spec: Dict[str, Any]) -> str:
    field = spec["field"]
    suffix = spec.get("component")
    if suffix is None and spec.get("component_index") is not None:
        suffix = f"c{spec['component_index']}"
    if suffix is None:
        suffix = spec["position"].lower()
    return f"{field}_{suffix}"


def _build_odb_results(
    spec_path: str,
) -> Tuple[Optional[NodeResults], Optional[CellResults]]:
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)

    client = ODBClient(
        base_url=spec.get("base_url", get_local_service_base_url()),
        timeout=int(spec.get("timeout", 60)),
    )

    odb_id = spec["odb_id"]
    default_instance = spec.get("instance")
    default_step = spec.get("step")
    default_frame = int(spec.get("frame", 0))

    node_results: NodeResults = {}
    cell_results: CellResults = {}

    for item in spec.get("results", []):
        instance = item.get("instance", default_instance)
        step = item.get("step", default_step)
        if not instance or not step:
            raise ValueError("Each ODB result spec requires instance and step")

        position = item["position"]
        frame = int(item.get("frame", default_frame))
        target = item.get("target")
        if target is None:
            target = "point" if position == "NODAL" else "cell"

        result_map = client.get_result_label_map(
            odb_id=odb_id,
            instance=instance,
            step=step,
            field=item["field"],
            position=position,
            frame=frame,
            component=item.get("component"),
            component_index=item.get("component_index", item.get("component_idx")),
            aggregation=item.get("aggregation", "mean"),
            scoped=bool(item.get("scoped", True)),
        )

        field_name = item.get("name") or _default_odb_result_name(item)
        if target == "point":
            if position != "NODAL":
                raise ValueError(
                    f"ODB result '{field_name}' uses position '{position}' and cannot be written as point data"
                )
            node_results[field_name] = result_map
        elif target == "cell":
            if position == "NODAL":
                raise ValueError(
                    f"ODB result '{field_name}' uses NODAL data and cannot be written as cell data"
                )
            cell_results[field_name] = result_map
        else:
            raise ValueError(f"Unknown target '{target}' in ODB result spec")

    return (node_results or None), (cell_results or None)


def main():
    parser = argparse.ArgumentParser(description="Convert Abaqus INP to VTU via meshio")
    parser.add_argument("inp", help="Input .inp file")
    parser.add_argument("vtu", help="Output .vtu file")
    parser.add_argument("--no-transform", action="store_true",
                        help="Write in Part local coordinates (ignore instance transforms)")
    parser.add_argument("--node-results", metavar="JSON",
                        help='节点结果 JSON 文件，格式: {"字段名": {"节点标签": 值或数组}}')
    parser.add_argument("--cell-results", metavar="JSON",
                        help='单元结果 JSON 文件，格式: {"字段名": {"单元标签": 值或数组}}')
    parser.add_argument("--odb-results-spec", metavar="JSON",
                        help="Fetch ODB raw-values and export them to VTU using a JSON spec")
    args = parser.parse_args()

    print(f"Parsing {args.inp} ...")
    model = parse_inp(args.inp)

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

    node_results = _load_results_json(args.node_results) if args.node_results else None
    cell_results = _load_results_json(args.cell_results) if args.cell_results else None
    if args.odb_results_spec:
        odb_node_results, odb_cell_results = _build_odb_results(args.odb_results_spec)
        node_results = _merge_result_sets(node_results, odb_node_results)
        cell_results = _merge_result_sets(cell_results, odb_cell_results)

    apply_transforms = not args.no_transform
    print(f"Building mesh (apply_transforms={apply_transforms}) ...")
    points, cells, cell_data, _, _, _, _ = build_mesh(model, apply_transforms)

    if points is None:
        sys.exit(1)

    total_cells = sum(len(c) for _, c in cells)
    print(f"  Points  : {len(points)}")
    print(f"  Cells   : {total_cells}")
    for t, arr in cells:
        print(f"    {t:20s} × {len(arr)}")
    if node_results:
        print(f"  Node result fields : {list(node_results.keys())}")
    if cell_results:
        print(f"  Cell result fields : {list(cell_results.keys())}")

    print(f"Writing {args.vtu} ...")
    write_vtu(model, args.vtu,
              node_results=node_results,
              cell_results=cell_results,
              apply_transforms=apply_transforms)
    print("Done.")


if __name__ == "__main__":
    main()
