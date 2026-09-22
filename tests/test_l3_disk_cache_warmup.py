"""磁盘缓存层 + 预热服务回归测试。

磁盘层：算过的 scalar_vertex / 图例范围写到 <workspace>/l3_cache/ 下
（scalars/*.npz、ranges/*.json），重启（清空内存缓存模拟）后直接从磁盘复用，
不再打开结果 H5；APP_SCALAR_DISK_CACHE_MB=0 关闭；容量超限按 mtime 淘汰旧文件。

预热：warm_odb_sync 遍历 manifest result_files 的 (result_group, step, field)
× instance，预算第 0 帧默认视图并写入两级缓存；之后 frame_scalars /
compute_scalar_range 不再触碰 H5。
"""
import os
import sqlite3

import h5py
import numpy as np
import pytest

import src.l3.services.result_service as rs
from src.l3.services.result_service import (
    clear_result_caches,
    compute_scalar_range,
    frame_scalars,
)
from src.l3.services.warmup_service import warm_odb_sync


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_result_caches()
    yield
    clear_result_caches()


def _boom(*args, **kwargs):
    raise AssertionError("cache miss: h5py.File was opened, expected cached result")


def _setup_nodal_instance(workspace, make_registry, *, values, field="U"):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.source_node_rows[instance] = np.array(
        [[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.int32
    )
    data = np.stack([np.asarray(values, dtype=np.float32),
                     np.asarray(values, dtype=np.float32) * 2.0])
    path = workspace / "l1" / "results" / f"Step-1__{field}.h5"
    with h5py.File(path, "w") as f:
        f.create_group("NODAL").create_group(instance).create_dataset(
            "data", data=data
        )
    return registry, instance, path


def _call_frame_scalars(registry, instance, **kw):
    return frame_scalars(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0, component_idx=None, **kw,
    )


def test_disk_cache_survives_memory_clear(workspace, make_registry, monkeypatch):
    """清空内存缓存（模拟重启）后，第二次调用从磁盘复用，不打开 H5。"""
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    u1, legend1, pos1 = _call_frame_scalars(registry, instance)
    assert (workspace / "l3_cache" / "scalars").is_dir()
    assert list((workspace / "l3_cache" / "scalars").glob("*.npz"))

    clear_result_caches()
    monkeypatch.setattr(h5py, "File", _boom)
    u2, legend2, pos2 = _call_frame_scalars(registry, instance)
    assert np.array_equal(u1, u2)
    assert np.array_equal(legend1, legend2)
    assert pos1 == pos2


def test_range_disk_cache_survives_memory_clear(workspace, make_registry, monkeypatch):
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    r1 = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0,
    )
    assert list((workspace / "l3_cache" / "ranges").glob("*.json"))

    clear_result_caches()
    monkeypatch.setattr(h5py, "File", _boom)
    r2 = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0,
    )
    assert r1 == r2


def test_disk_cache_disabled_by_config(workspace, make_registry, monkeypatch):
    monkeypatch.setattr(rs.settings, "scalar_disk_cache_mb", 0)
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    _call_frame_scalars(registry, instance)
    assert not (workspace / "l3_cache").exists()

    clear_result_caches()
    monkeypatch.setattr(h5py, "File", _boom)
    with pytest.raises(AssertionError, match="cache miss"):
        _call_frame_scalars(registry, instance)


def test_corrupt_disk_entry_recomputes(workspace, make_registry):
    """磁盘条目损坏 → 删除并重算，不报错。"""
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    _, legend1, _ = _call_frame_scalars(registry, instance)
    for f in (workspace / "l3_cache" / "scalars").glob("*.npz"):
        f.write_bytes(b"garbage")
    clear_result_caches()
    _, legend2, _ = _call_frame_scalars(registry, instance)
    assert np.array_equal(legend1, legend2)


def test_disk_eviction_by_cap(tmp_path):
    """scalars 目录超容量时按 mtime 从旧到新删。"""
    d = tmp_path / "scalars"
    d.mkdir()
    for i in range(5):
        p = d / f"f{i}.npz"
        p.write_bytes(b"x" * 1000)
        os.utime(p, ns=(i * 1_000_000_000,) * 2)
    rs._evict_scalar_disk(str(d), cap_bytes=2500)
    left = sorted(f.name for f in d.glob("*.npz"))
    assert left == ["f3.npz", "f4.npz"]


def test_warmup_precomputes_default_view(workspace, make_registry, monkeypatch):
    """warm_odb_sync 后，frame_scalars / compute_scalar_range 均不再读 H5。"""
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    # 建 result_files 表供预热枚举 (result_group, step, field)
    conn = sqlite3.connect(workspace / "manifest.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS result_files "
        "(step_name TEXT, field_name TEXT, result_group TEXT)"
    )
    conn.execute(
        "INSERT INTO result_files (step_name, field_name, result_group) "
        "VALUES ('Step-1', 'U', NULL)"
    )
    conn.commit()
    conn.close()

    report = warm_odb_sync("odb", reason="test", reg=registry)
    assert report["status"] == "done"
    assert report["warmed"] == 1

    monkeypatch.setattr(h5py, "File", _boom)
    u, legend, _ = _call_frame_scalars(registry, instance)
    assert legend[1] == pytest.approx(8.0)
    r = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0,
    )
    assert r == (pytest.approx(0.0), pytest.approx(8.0))
