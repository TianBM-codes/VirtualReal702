# -*- coding: utf-8 -*-
"""
rst_service.py — Ansys RST → model_update 数据库导入（对标 bdf_service.import_bdf_data）。

RST 自带几何、单元属性号与材料常数（rst.materials 给 EX/NUXY/DENS）。
壳厚来自实常数（mesh.rlblock）；SECTYPE 厚度 RST 不直接暴露，缺则后续再补。
归一成共享 model dict 后交给 ansys_mu_common.import_ansys_model 写库。
"""
import numpy as np

from services.model_update.importers.ansys_mu_common import import_ansys_model


def _build_model(rst_path):
    from ansys.mapdl import reader as pymapdl_reader

    rst = pymapdl_reader.read_binary(rst_path)
    mesh = rst.mesh
    grid = rst.grid
    cd = grid.cell_data
    n_elem = grid.n_cells

    sec = getattr(mesh, "section", 0)
    sec_arr = (np.asarray(sec, dtype=np.int64) if np.ndim(sec)
               else np.full(n_elem, int(sec), dtype=np.int64))

    # 材料常数直接来自 rst.materials（键为 ANSYS 物性码 EX/NUXY/DENS/...）
    materials = {}
    for mid, props in (getattr(rst, "materials", {}) or {}).items():
        materials[int(mid)] = {str(k).upper(): v for k, v in props.items()}

    # 实常数（壳厚来源）：mesh.rlblock 按 rlblock_num 对应
    reals = {}
    rl = getattr(mesh, "rlblock", None)
    rln = getattr(mesh, "rlblock_num", None)
    if rl is not None and rln is not None:
        rl = np.asarray(rl, dtype=object)
        for i, rid in enumerate(np.asarray(rln)):
            try:
                reals[int(rid)] = [float(x) for x in np.asarray(rl[i]).ravel()]
            except Exception:
                pass

    return {
        "node_labels": np.asarray(grid.point_data["ansys_node_num"], dtype=np.int64),
        "node_coords": np.asarray(grid.points, dtype=np.float64),
        "elem_label":  np.asarray(cd["ansys_elem_num"], dtype=np.int64),
        "elem_mat":    np.asarray(cd["ansys_material_type"], dtype=np.int64),
        "elem_real":   np.asarray(cd["ansys_real_constant"], dtype=np.int64),
        "elem_sec":    sec_arr,
        "elem_etype":  np.asarray(cd["ansys_elem_type_num"], dtype=np.int64),
        "materials":   materials,
        "sections":    {},   # RST 不直接暴露 SECTYPE 厚度，缺则后续补
        "reals":       reals,
    }


def import_rst_data(file_path, project_id, file_id=None, clear_before_insert=True):
    model = _build_model(file_path)
    return import_ansys_model(model, file_path, project_id, "rst", clear_before_insert)


if __name__ == "__main__":
    import sys
    import_rst_data(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
