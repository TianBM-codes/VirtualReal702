"""点选不变量字段回归测试。

壳的面内主应变等不变量场，L1 只在 INTEGRATION_POINT 出值，ELEMENT_NODAL 块整块 NaN。
修复前 query/pick 读到 EN 的 NaN 直接返回，NaN 无法序列化成 JSON → 接口 500。
修复后与 frame_scalars 一致：EN 为 NaN 时继续读 IP；都没有有效值则视为无结果。
"""
import h5py
import numpy as np

from src.l3.services.query_service import _read_pick_result


def _write(workspace, *, ip_vals):
    path = workspace / "l1" / "results" / "Step-1__E_MAX_INPLANE_PRINCIPAL.h5"
    with h5py.File(path, "w") as f:
        f.create_group("/ELEMENT_NODAL/PART-1-1/S4R").create_dataset(
            "data", data=np.full((1, 2, 4, 1), np.nan, np.float32))
        f.create_group("/INTEGRATION_POINT/PART-1-1/S4R").create_dataset(
            "data", data=np.asarray(ip_vals, np.float32).reshape(1, 2, 1, 1))


def _pick(workspace):
    return _read_pick_result(
        str(workspace), "PART-1-1", face_node_rows=[0, 1, 2], elem_row=1,
        etype_str="S4R", frame_idx=0, step="Step-1", field="E_MAX_INPLANE_PRINCIPAL",
        component=None, component_idx=None, pick_mode="element")


def test_pick_falls_through_to_ip_when_en_is_nan(workspace):
    _write(workspace, ip_vals=[0.5, -0.25])
    info = _pick(workspace)
    assert info is not None
    assert info.position == "INTEGRATION_POINT"
    assert info.display_value == -0.25


def test_pick_without_finite_value_returns_none(workspace):
    _write(workspace, ip_vals=[np.nan, np.nan])
    assert _pick(workspace) is None
