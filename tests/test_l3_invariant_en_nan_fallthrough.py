"""不变量字段回退回归测试。

L1 用 getScalarField 提取不变量(如 S_INV3 / S_PRESS / S_MAX_PRINCIPAL)时，
ELEMENT_NODAL 块未被填充 → 整块写成 NaN，只有 INTEGRATION_POINT 有真实值。

修复前：L3 看到 EN 分组"存在"就锁定到它，范围算不出(全 NaN) → range 返回 None、
云图全灰。修复后：EN 全 NaN 被视为"无数据"，自动回退到 INTEGRATION_POINT。

本测试构造 EN 全 NaN + IP 有值的结果 H5，断言 compute_scalar_range / frame_scalars
都能拿到 IP 的真实数据。
"""
import h5py
import numpy as np

from src.l3.services.result_service import compute_scalar_range, frame_scalars


def _write_inv_result_file(workspace, instance, *, step, field, n_elem):
    """EN 全 NaN、IP 有真实值的单 etype(C3D8R)结果文件。"""
    fname = f"{step.replace(' ', '_')}__{field}.h5"
    path = workspace / "l1" / "results" / fname
    ip_vals = np.arange(2 * n_elem, dtype=np.float32).reshape(2, n_elem, 1, 1)
    with h5py.File(path, "w") as f:
        en = f.create_group(f"/ELEMENT_NODAL/{instance}/C3D8R")
        en.create_dataset("data", data=np.full((2, n_elem, 8, 1), np.nan, np.float32))
        ip = f.create_group(f"/INTEGRATION_POINT/{instance}/C3D8R")
        ip.create_dataset("data", data=ip_vals)
    return path, ip_vals


def test_compute_scalar_range_falls_through_to_ip_when_en_all_nan(
    workspace, make_registry
):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    n_elem = 5
    idx.source_node_rows[instance] = np.array(
        [[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.int32
    )
    idx.render_source_elem_row[instance] = np.array([0, 1, 2], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"C3D8R"] * 3, dtype="S8")

    _, ip_vals = _write_inv_result_file(
        workspace, instance, step="Step-1", field="S_INV3", n_elem=n_elem
    )

    rng = compute_scalar_range(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="S_INV3",
        frame_idx=1,
        component_idx=None,
    )

    assert rng is not None, "EN 全 NaN 时应回退到 IP，而不是返回 None"
    # 渲染面只覆盖前 3 个单元；frame 1 的 IP 值是 n_elem..2*n_elem-1
    faces = ip_vals[1, :3, 0, 0]
    assert rng[0] == float(faces.min())
    assert rng[1] == float(faces.max())


def test_frame_scalars_uses_ip_when_en_all_nan(workspace, make_registry):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    n_elem = 5
    idx.source_node_rows[instance] = np.array(
        [[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.int32
    )
    idx.render_source_elem_row[instance] = np.array([0, 1, 2], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"C3D8R"] * 3, dtype="S8")

    _write_inv_result_file(
        workspace, instance, step="Step-1", field="S_INV3", n_elem=n_elem
    )

    u, legend, position = frame_scalars(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="S_INV3",
        frame_idx=1,
        component_idx=None,
    )

    assert position == "INTEGRATION_POINT_FLAT"
    # 修复前 u 会因为锁定到全 NaN 的 EN 而全是 NaN；修复后应有有限值
    assert np.isfinite(u).any()
    assert np.isfinite(legend).all()
