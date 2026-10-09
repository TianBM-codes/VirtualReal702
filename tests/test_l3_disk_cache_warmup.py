"""磁盘缓存层 + 预热服务回归测试。

磁盘层（统一缓存层 cache_store）：算过的 scalar_vertex / 图例范围写到
<workspace>/l3_cache/groups/<result_group>/ 下（arrays/*.npz、json/*.json），重启
（清空内存缓存模拟）后直接从磁盘复用，不再打开结果 H5；APP_SCALAR_DISK_CACHE_MB=0
关闭；整个 l3_cache/ 超容量按 mtime 淘汰旧文件；旧布局目录首次访问时删除。

预热：warm_odb_sync 遍历 manifest result_files 的 (result_group, step, field)
× instance，预算第 0 帧默认视图并写入两级缓存；之后 frame_scalars /
compute_scalar_range 不再触碰 H5。
"""
import os
import sqlite3

import h5py
import numpy as np
import pytest

from src.l3.services import cache_store
from src.l3.services.result_service import (
    clear_result_caches,
    compute_scalar_range,
    frame_scalars,
)
from src.l3.services.warmup_service import warm_odb_sync


GROUP_DIR = "l3_cache/groups/_default"


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
    assert list((workspace / GROUP_DIR / "arrays").glob("*.npz"))

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
    assert list((workspace / GROUP_DIR / "json").glob("*.json"))

    clear_result_caches()
    monkeypatch.setattr(h5py, "File", _boom)
    r2 = compute_scalar_range(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=0,
    )
    assert r1 == r2


def test_disk_cache_disabled_by_config(workspace, make_registry, monkeypatch):
    monkeypatch.setattr(cache_store.settings, "scalar_disk_cache_mb", 0)
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
    for f in (workspace / GROUP_DIR / "arrays").glob("*.npz"):
        f.write_bytes(b"garbage")
    clear_result_caches()
    _, legend2, _ = _call_frame_scalars(registry, instance)
    assert np.array_equal(legend1, legend2)


def test_disk_eviction_by_cap(tmp_path):
    """整个 l3_cache/ 超容量时跨子目录按 mtime 从旧到新删。"""
    root = tmp_path / "l3_cache"
    dirs = [root / "model" / "arrays", root / "groups" / "g1" / "payloads",
            root / "groups" / "g2" / "json"]
    for d in dirs:
        d.mkdir(parents=True)
    for i in range(6):
        p = dirs[i % 3] / f"f{i}.bin"
        p.write_bytes(b"x" * 1000)
        os.utime(p, ns=(i * 1_000_000_000,) * 2)
    cache_store.evict(str(tmp_path), cap_bytes=2500)
    left = sorted(f.name for f in root.rglob("*.bin"))
    assert left == ["f4.bin", "f5.bin"]


def test_legacy_cache_dirs_purged(workspace, make_registry):
    """统一缓存层之前的目录（scalars/ranges/deformed_payloads……）首次访问时删除。"""
    for name in ("scalars", "ranges", "raw_displacements", "deformed_normals",
                 "deformed_payloads", "modal_payloads"):
        d = workspace / "l3_cache" / name
        d.mkdir(parents=True)
        (d / "old.bin").write_bytes(b"x")
    cache_store._purged_workspaces.discard(str(workspace / "l3_cache"))
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    _call_frame_scalars(registry, instance)
    assert sorted(p.name for p in (workspace / "l3_cache").iterdir()) == ["groups"]


def test_group_bucket_names():
    assert cache_store.group_bucket(None) == "groups/_default"
    assert cache_store.group_bucket("") == "groups/_default"
    assert cache_store.group_bucket("op2_rg-1.v2") == "groups/op2_rg-1.v2"
    a = cache_store.group_bucket("a/b")
    b = cache_store.group_bucket("a_b")
    assert a.startswith("groups/a_b-") and b == "groups/a_b" and a != b
    assert cache_store.group_bucket("..") != "groups/.."


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


def test_warmup_uses_last_frame_of_each_step(workspace, make_registry, monkeypatch):
    """预热取每个 step 的最后一帧（frames 表最大 frame_idx），第 0 帧不预算。"""
    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
    conn = sqlite3.connect(workspace / "manifest.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS result_files "
        "(step_name TEXT, field_name TEXT, result_group TEXT)"
    )
    conn.execute(
        "INSERT INTO result_files (step_name, field_name, result_group) "
        "VALUES ('Step-1', 'U', NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS frames "
        "(step_name TEXT, frame_idx INT, frame_value REAL, result_group TEXT)"
    )
    conn.executemany(
        "INSERT INTO frames VALUES ('Step-1', ?, ?, NULL)", [(0, 0.0), (1, 1.0)]
    )
    conn.commit()
    conn.close()

    report = warm_odb_sync("odb", reason="test", reg=registry)
    assert report["status"] == "done" and report["warmed"] == 1

    monkeypatch.setattr(h5py, "File", _boom)
    u, legend, _ = frame_scalars(
        registry=registry, odb_id="odb", instance=instance,
        step="Step-1", field="U", frame_idx=1, component_idx=None,
    )
    assert legend[1] == pytest.approx(16.0)       # 第 1 帧数据 = 第 0 帧 × 2
    with pytest.raises(AssertionError, match="cache miss"):
        _call_frame_scalars(registry, instance)   # 第 0 帧未预算


def test_frame_colors_cached_and_warmed(workspace, make_registry, monkeypatch):
    """frame-colors（旧接口）：预热后不碰 H5，结果与首算一致；set 过滤仍按请求执行。"""
    from src.l3.services.result_service import frame_colors

    registry, instance, _ = _setup_nodal_instance(
        workspace, make_registry, values=np.arange(9.0)
    )
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

    def _call():
        return frame_colors(
            registry=registry, odb_id="odb", instance=instance,
            step="Step-1", field="U", frame_idx=0, component="U1",
        )

    c_ref, l_ref = _call()          # 首算（也写入缓存）
    clear_result_caches()
    warm_odb_sync("odb", reason="test", reg=registry)   # 磁盘已有 → 幂等

    clear_result_caches()
    monkeypatch.setattr(h5py, "File", _boom)
    c2, l2 = _call()                # 磁盘命中
    assert np.array_equal(c_ref, c2) and np.array_equal(l_ref, l2)
    assert list((workspace / GROUP_DIR / "arrays").glob("*.npz"))
