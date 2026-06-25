"""高阶单元 legend 范围回归测试。

_compute_en_global_range 计算云图 legend 的全模型范围。修复前它只取角节点
（conn[:, :n_corner]），高阶单元落在边中节点上的峰值/谷值会被漏掉，legend
封顶在角节点值上、与 Abaqus 对不上。修复后改用全连接（conn_full，含中节点），
中节点极值进入 legend。

参见 docs/l2/HighOrder-Midside-Subdivision-Design.md。
"""
import h5py
import numpy as np

from src.l3.services.result_service import _compute_en_global_range


def _write_c3d20(workspace, instance, *, with_highorder):
    """单个 C3D20 单元：角节点值平庸、两个边中节点持极值 +999 / -999。"""
    geom_path = workspace / "l1" / "geometry" / f"{instance}.h5"
    with h5py.File(geom_path, "w") as f:
        f.create_group("nodes").create_dataset(
            "labels", data=np.arange(1, 21, dtype=np.int32))   # 20 节点, 行 0..19
        et = f.create_group("elements").create_group("C3D20")
        et.create_dataset("conn", data=np.arange(8, dtype=np.int32).reshape(1, 8))
        et.create_dataset("section_id", data=np.array([0], dtype=np.int32))

    if with_highorder:
        ho_path = workspace / "l1" / "geometry" / f"{instance}_highorder.h5"
        with h5py.File(ho_path, "w") as f:
            g = f.create_group("elements").create_group("C3D20")
            # 全连接存的是节点 LABEL（1..20），_load_full_conn_rows 再映射成行
            g.create_dataset("conn_full",
                             data=np.arange(1, 21, dtype=np.int32).reshape(1, 20))

    # ELEMENT_NODAL: [1 帧, 1 单元, 20 节点, 1 分量]
    en = np.full((1, 1, 20, 1), 100.0, dtype=np.float32)
    en[0, 0, 8, 0] = 999.0     # 一个边中节点 = 峰值
    en[0, 0, 9, 0] = -999.0    # 另一个边中节点 = 谷值
    res_path = workspace / "l1" / "results" / "Step-1__S_MISES.h5"
    with h5py.File(res_path, "w") as f:
        f.create_group("ELEMENT_NODAL").create_group(instance).create_group(
            "C3D20").create_dataset("data", data=en)

    return str(geom_path), str(res_path)


def test_legend_range_includes_midnode_extrema(workspace):
    instance = "PART-1-1"
    geom_path, res_path = _write_c3d20(workspace, instance, with_highorder=True)

    with h5py.File(res_path, "r") as result_h5:
        rng = _compute_en_global_range(
            result_h5, geom_path, instance, frame_idx=0, component_idx=None)

    assert rng is not None
    g_min, g_max = rng
    # 中节点的 +999 / -999 必须进入 legend，而不是被角节点的 100 封顶
    assert g_max >= 999.0 - 1e-3, f"峰值 (中节点) 未进入 legend: {g_max}"
    assert g_min <= -999.0 + 1e-3, f"谷值 (中节点) 未进入 legend: {g_min}"


def test_legend_range_corner_only_without_highorder(workspace):
    # 没有 highorder 文件时退回角节点行为（仅角节点值 100）——证明差异确实来自中节点
    instance = "PART-1-1"
    geom_path, res_path = _write_c3d20(workspace, instance, with_highorder=False)

    with h5py.File(res_path, "r") as result_h5:
        rng = _compute_en_global_range(
            result_h5, geom_path, instance, frame_idx=0, component_idx=None)

    assert rng is not None
    g_min, g_max = rng
    # 角节点全是 100，拿不到中节点极值
    assert abs(g_max - 100.0) < 1e-3
    assert abs(g_min - 100.0) < 1e-3
