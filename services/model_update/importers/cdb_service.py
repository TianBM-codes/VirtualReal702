# -*- coding: utf-8 -*-
"""
cdb_service.py — Ansys CDB → model_update 数据库导入（对标 bdf_service.import_bdf_data）。

CDB 只有几何与单元属性号；材料常数 / 截面厚度由 src.l1.cdb_text 从文本补出。
归一成共享 model dict 后交给 ansys_mu_common.import_ansys_model 写库。
"""
import numpy as np

from services.model_update.importers.ansys_mu_common import import_ansys_model


def _build_model(cdb_path):
    from ansys.mapdl import reader as pymapdl_reader
    from src.l1 import cdb_text

    arch = pymapdl_reader.Archive(cdb_path)
    grid = arch.grid
    cd = grid.cell_data
    sec = arch.section
    n_elem = grid.n_cells
    sec_arr = (np.asarray(sec, dtype=np.int64) if np.ndim(sec)
               else np.full(n_elem, int(sec), dtype=np.int64))
    return {
        "node_labels": np.asarray(grid.point_data["ansys_node_num"], dtype=np.int64),
        "node_coords": np.asarray(grid.points, dtype=np.float64),
        "elem_label":  np.asarray(cd["ansys_elem_num"], dtype=np.int64),
        "elem_mat":    np.asarray(cd["ansys_material_type"], dtype=np.int64),
        "elem_real":   np.asarray(cd["ansys_real_constant"], dtype=np.int64),
        "elem_sec":    sec_arr,
        "elem_etype":  np.asarray(cd["ansys_elem_type_num"], dtype=np.int64),
        "materials":   cdb_text.parse_materials(cdb_path),
        "sections":    cdb_text.parse_sections(cdb_path),
        "reals":       cdb_text.parse_real_constants(cdb_path),
    }


def import_cdb_data(file_path, project_id, file_id=None, clear_before_insert=True):
    model = _build_model(file_path)
    return import_ansys_model(model, file_path, project_id, "cdb", clear_before_insert)


if __name__ == "__main__":
    import sys
    import_cdb_data(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
