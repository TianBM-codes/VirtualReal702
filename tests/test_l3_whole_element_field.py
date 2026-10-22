"""WHOLE_ELEMENT 字段（如 STATUS）回归测试。

L1 把 STATUS 这类整单元结果存成 /WHOLE_ELEMENT/<inst>/<etype>/data [帧, 单元, 分量]，
修复前 L3 只认 NODAL / ELEMENT_NODAL / INTEGRATION_POINT，每次请求都判成"无数据"、
返回空 payload，也就从不进缓存（每次约 190 ms）。修复后按单元着色（flat）。
"""
import h5py
import numpy as np

from src.l3.services.result_service import (
    _component_cache_token, compute_scalar_range, frame_scalars,
)


def _write_status_file(workspace, instance, n_elem):
    path = workspace / "l1" / "results" / "Step-1__STATUS.h5"
    status = np.ones((2, n_elem, 1), np.float32)
    status[1, 1, 0] = 0.0                       # 第 1 帧单元 1 被删除
    with h5py.File(path, "w") as f:
        grp = f.create_group(f"/WHOLE_ELEMENT/{instance}/C3D8R")
        grp.create_dataset("data", data=status)
    return path


def _setup(workspace, make_registry):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.source_node_rows[instance] = np.array(
        [[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.int32
    )
    idx.render_source_elem_row[instance] = np.array([0, 1, 2], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"C3D8R"] * 3, dtype="S8")
    path = _write_status_file(workspace, instance, n_elem=4)
    return registry, instance, path


def test_frame_scalars_reads_whole_element(workspace, make_registry):
    registry, instance, _ = _setup(workspace, make_registry)
    u, legend, position = frame_scalars(
        registry=registry, odb_id="odb", instance=instance, step="Step-1",
        field="STATUS", frame_idx=1, component_idx=None,
    )
    assert position == "WHOLE_ELEMENT_FLAT"
    assert np.isfinite(u).all()
    np.testing.assert_array_equal(legend, [0.0, 1.0])


def test_scalar_range_reads_whole_element(workspace, make_registry):
    registry, instance, _ = _setup(workspace, make_registry)
    rng = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance, step="Step-1",
        field="STATUS", frame_idx=1, component_idx=None,
    )
    assert rng == (0.0, 1.0)


def test_cache_token_marks_whole_element_files(workspace, make_registry):
    _, _, path = _setup(workspace, make_registry)
    # 修复前缓存过的"无范围"（None）不能再命中
    assert _component_cache_token(str(path), (1, 2), None) == ("we", None)
    assert _component_cache_token(str(path), (1, 2), 0) == 0      # 显式分量不读文件
