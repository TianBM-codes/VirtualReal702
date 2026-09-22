"""frame-scalars / frame-scalar-range 结果缓存回归测试。

大模型（700~800 万面片）下 frame_scalars 每次请求都重读 H5 并重做条件平均，
逐帧/切分量/set 操作时同一份计算被反复执行。缓存策略：
  - frame_scalars 归一化前的 scalar_vertex 按 (结果文件 mtime+size, instance,
    帧, 分量, 渲染参数) 缓存（字节数 LRU，上限 APP_SCALAR_CACHE_MB，默认 512）；
  - compute_scalar_range 无 set 路径与 _compute_en_global_range 的图例范围
    走小结果 LRU；
  - 结果文件被重写（外部字段/result_group 重新解析）→ mtime 变化 → 自动失效；
  - APP_SCALAR_CACHE_MB=0 关闭全部结果缓存。

验证手法：第一次调用后把 h5py.File monkeypatch 成直接抛错——命中缓存的第二次
调用不会碰文件，仍应返回与第一次完全相同的结果；恢复 h5py.File 并重写结果
文件后，应返回新数据（证明失效生效）。
"""
import os

import h5py
import numpy as np
import pytest

import src.l3.services.result_service as rs
from src.l3.services.result_service import (
    clear_result_caches,
    compute_scalar_range,
    frame_scalars,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_result_caches()
    yield
    clear_result_caches()


def _boom(*args, **kwargs):
    raise AssertionError("cache miss: h5py.File was opened, expected cached result")


def _setup_nodal_instance(workspace, make_registry, *, values):
    """三角形 3 个、NODAL 结果 [2 帧, 9 节点] 的最小实例。"""
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.source_node_rows[instance] = np.array(
        [[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.int32
    )
    data = np.stack([np.asarray(values, dtype=np.float32),
                     np.asarray(values, dtype=np.float32) * 2.0])
    path = workspace / "l1" / "results" / "Step-1__U.h5"
    with h5py.File(path, "w") as f:
        f.create_group("NODAL").create_group(instance).create_dataset(
            "data", data=data
        )
    return registry, instance, path


def _call_frame_scalars(registry, instance, **kw):
    return frame_scalars(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="U",
        frame_idx=0,
        component_idx=None,
        **kw,
    )


def test_frame_scalars_second_call_hits_cache(workspace, make_registry, monkeypatch):
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    u1, legend1, pos1 = _call_frame_scalars(registry, instance)

    monkeypatch.setattr(h5py, "File", _boom)
    u2, legend2, pos2 = _call_frame_scalars(registry, instance)

    assert np.array_equal(u1, u2)
    assert np.array_equal(legend1, legend2)
    assert pos1 == pos2


def test_cached_array_supports_set_and_override(workspace, make_registry, monkeypatch):
    """命中缓存后 set 过滤 / override 归一化仍按本次请求参数执行。"""
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    _call_frame_scalars(registry, instance)

    monkeypatch.setattr(h5py, "File", _boom)
    u, legend, _ = _call_frame_scalars(
        registry, instance, override_min=0.0, override_max=16.0
    )
    # 原始值 0..8，override 范围 [0,16] → 归一化后最大 0.5
    assert legend[0] == 0.0 and legend[1] == 16.0
    assert np.isclose(np.nanmax(u), 0.5)


def test_rewritten_result_file_invalidates_cache(workspace, make_registry):
    registry, instance, path = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    _, legend1, _ = _call_frame_scalars(registry, instance)

    with h5py.File(path, "w") as f:
        f.create_group("NODAL").create_group(instance).create_dataset(
            "data", data=np.full((2, 9), 100.0, dtype=np.float32)
        )
    os.utime(path, ns=(os.stat(path).st_mtime_ns + 1_000_000,) * 2)

    _, legend2, _ = _call_frame_scalars(registry, instance)
    assert legend1[1] == pytest.approx(8.0)
    assert legend2[0] == pytest.approx(100.0) and legend2[1] == pytest.approx(100.0)


def test_cache_disabled_by_config(workspace, make_registry, monkeypatch):
    """APP_SCALAR_CACHE_MB=0 → 不缓存，第二次调用会真正打开文件。"""
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    monkeypatch.setattr(rs.settings, "scalar_cache_mb", 0)
    _call_frame_scalars(registry, instance)

    monkeypatch.setattr(h5py, "File", _boom)
    with pytest.raises(AssertionError, match="cache miss"):
        _call_frame_scalars(registry, instance)


def test_compute_scalar_range_hits_cache(workspace, make_registry, monkeypatch):
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    r1 = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0, component_idx=None,
    )
    monkeypatch.setattr(h5py, "File", _boom)
    r2 = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0, component_idx=None,
    )
    assert r1 == r2
    assert r1 == (pytest.approx(0.0), pytest.approx(8.0))
