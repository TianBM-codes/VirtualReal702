"""越界分量置灰回归测试(混合 solid+shell 模型)。

混合模型里, 字段 S/E 的分量下拉是各实例分量的并集 → 含 S13/S23(实体有, 壳/膜没有)。
当某个实例是纯壳/膜(只有 S11,S22,S33,S12 共 4 分量)时, 选 S23(component_idx=5)
对该实例所有单元类型都越界。

修复前: NODAL 缺失 → EN 全 NaN 返回 None → IP 全 NaN 返回 None → 一路 None →
抛 NotFoundError "no result data for instance ... in field 'S'", 整个云图渲染失败。
修复后: 识别出"选中分量对该实例所有 etype 越界" = 该实例无此分量, 返回全 NaN 让前端
置灰(grey), 不再 fall through 报错。

本测试构造纯壳实例(S4R, 4 分量), 选越界分量 5, 断言 frame_scalars 不抛错、返回全灰;
并断言选合法分量(0=S11)仍能正常出值, 证明修复没有把正常分量也吞成灰。
"""
import h5py
import numpy as np
import pytest

from src.l3.services.result_service import compute_scalar_range, frame_scalars


def _write_shell_result_file(workspace, instance, *, step, field, n_elem, ncomp=4):
    """纯壳(S4R)结果文件: EN/IP 数据放在 sp1 子组下, 只有 ncomp 个分量。"""
    fname = f"{step.replace(' ', '_')}__{field}.h5"
    path = workspace / "l1" / "results" / fname
    # 每个分量给不同量级, 方便区分; 形状 [num_frames, n_elem, n_local_node, ncomp]
    base = np.arange(n_elem, dtype=np.float32)[None, :, None, None]
    comp = np.arange(ncomp, dtype=np.float32)[None, None, None, :]
    en_vals = (base * 10.0 + comp) * np.ones((2, n_elem, 4, ncomp), np.float32)
    ip_vals = (base * 10.0 + comp) * np.ones((2, n_elem, 1, ncomp), np.float32)
    with h5py.File(path, "w") as f:
        en = f.create_group(f"/ELEMENT_NODAL/{instance}/S4R/sp1")
        en.create_dataset("data", data=en_vals)
        ip = f.create_group(f"/INTEGRATION_POINT/{instance}/S4R/sp1")
        ip.create_dataset("data", data=ip_vals)
    return path


def _setup_shell_instance(workspace, make_registry, instance, n_elem):
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.source_node_rows[instance] = np.array(
        [[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.int32
    )
    idx.render_source_elem_row[instance] = np.array([0, 1, 2], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"] * 3, dtype="S8")
    _write_shell_result_file(
        workspace, instance, step="Step-1", field="S", n_elem=n_elem
    )
    return registry


def test_frame_scalars_greys_out_of_range_component(workspace, make_registry):
    """壳实例选 S23(idx 5)越界 → 全灰, 不抛 NotFoundError。"""
    instance = "PART-1-1"
    registry = _setup_shell_instance(workspace, make_registry, instance, n_elem=5)

    u, legend, position = frame_scalars(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="S",
        frame_idx=1,
        component_idx=5,   # S23: 壳只有 4 分量 → 越界
    )

    # 修复前这里会抛 NotFoundError; 修复后返回全 NaN(前端置灰)
    assert not np.isfinite(u).any(), "越界分量应全为 NaN(置灰)"
    assert np.isfinite(legend).all(), "legend 仍应是有限值(0,0)"


def test_compute_scalar_range_skips_out_of_range_component(workspace, make_registry):
    """壳实例选越界分量时, 范围应返回 None(不参与并集图例), 而不是报错。"""
    instance = "PART-1-1"
    registry = _setup_shell_instance(workspace, make_registry, instance, n_elem=5)

    rng = compute_scalar_range(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="S",
        frame_idx=1,
        component_idx=5,
    )
    assert rng is None, "越界分量对该实例无数据 → 不贡献图例范围"


def test_frame_scalars_valid_component_still_works(workspace, make_registry):
    """合法分量(S11, idx 0)仍正常出值 — 证明置灰逻辑没误伤正常分量。"""
    instance = "PART-1-1"
    registry = _setup_shell_instance(workspace, make_registry, instance, n_elem=5)

    u, legend, position = frame_scalars(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="S",
        frame_idx=1,
        component_idx=0,   # S11: 壳有此分量
    )
    assert np.isfinite(u).any(), "合法分量应有有限值"
    assert np.isfinite(legend).all()
