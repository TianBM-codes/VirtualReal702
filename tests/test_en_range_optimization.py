"""
全模型平均范围（_compute_en_global_range）的性能优化不得改变结果：
  1. 区域范围查表从 dict 循环改为 searchsorted 后，与旧实现逐位一致；
  2. 严格准备期间同一帧同一分量只算一次（中间结果进程内复用），缓存关闭时照旧每次现算。
"""
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from src.l3.services import cache_store, result_service as rs


def _reference_range(sec_ids, node_rows, values, threshold):
    """优化前的实现（dict.get 逐节点查区域范围），作为对照。"""
    dom_sort = np.argsort(sec_ids, kind="stable")
    sec_dom, val_dom = sec_ids[dom_sort], values[dom_sort]
    dom_bounds = np.concatenate([[0], np.where(sec_dom[1:] != sec_dom[:-1])[0] + 1, [len(sec_dom)]])
    dom_range_arr = (np.maximum.reduceat(val_dom, dom_bounds[:-1])
                     - np.minimum.reduceat(val_dom, dom_bounds[:-1]))
    dom_range_map = {int(s): float(r) for s, r in zip(sec_dom[dom_bounds[:-1]], dom_range_arr)}
    max_nr = int(node_rows.max()) + 1
    compound = sec_ids.astype(np.int64) * max_nr + node_rows.astype(np.int64)
    order = np.argsort(compound, kind="stable")
    comp_s, sec_s, val_s = compound[order], sec_ids[order], values[order]
    nb = np.concatenate([[0], np.where(comp_s[1:] != comp_s[:-1])[0] + 1, [len(comp_s)]])
    nmin = np.minimum.reduceat(val_s, nb[:-1])
    nmax = np.maximum.reduceat(val_s, nb[:-1])
    nmean = np.add.reduceat(val_s, nb[:-1]) / np.diff(nb).astype(np.float64)
    dom_per_node = np.array([dom_range_map.get(int(s), 0.0) for s in sec_s[nb[:-1]]], dtype=np.float64)
    avg = (dom_per_node < 1e-12) | ((nmax - nmin) <= threshold * dom_per_node)
    g_min, g_max = np.inf, -np.inf
    if avg.any():
        g_min, g_max = min(g_min, float(nmean[avg].min())), max(g_max, float(nmean[avg].max()))
    if (~avg).any():
        g_min, g_max = min(g_min, float(nmin[~avg].min())), max(g_max, float(nmax[~avg].max()))
    return float(g_min), float(g_max)


def _write_model(tmp_path, seed):
    rng = np.random.default_rng(seed)
    n_elem, n_node = 400, 300
    conn = rng.integers(0, n_node, size=(n_elem, 8)).astype(np.int32)
    sec = rng.integers(-2, 5, size=n_elem).astype(np.int32)
    sec[:3] = 9                                         # 一个所有值相同的区域（范围为 0）
    data = rng.normal(size=(2, n_elem, 8, 6)).astype(np.float32)
    data[0, :3] = 1.5
    data[0, rng.random((n_elem, 8)) < 0.05, 2] = np.nan  # 部分角点无数据
    geom = tmp_path / "PART.h5"
    with h5py.File(geom, "w") as f:
        f["elements/C3D8/conn"] = conn
        f["elements/C3D8/section_id"] = sec
    res = tmp_path / "res.h5"
    with h5py.File(res, "w") as f:
        f["ELEMENT_NODAL/PART/C3D8/data"] = data
    return geom, res, conn, sec, data


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("component_idx", [None, 0, 2, 5])
@pytest.mark.parametrize("threshold", [0.0, 0.75, 1.0])
def test_vectorized_domain_lookup_matches_reference(tmp_path, seed, component_idx, threshold):
    geom, res, conn, sec, data = _write_model(tmp_path, seed)
    with h5py.File(res, "r") as f:
        got = rs._compute_en_global_range_uncached(f, str(geom), "PART", 0, component_idx, threshold)
    sc = rs._extract_component(data[0].reshape(-1, 6), component_idx).reshape(400, 8).astype(np.float64)
    vals = sc.ravel()
    ok = np.isfinite(vals)
    expected = _reference_range(np.repeat(sec, 8)[ok], conn.ravel()[ok], vals[ok], threshold)
    assert got == expected                               # 逐位相等，不放宽容差


def test_strict_prepare_computes_range_once(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_store.settings, "scalar_cache_mb", 10)
    monkeypatch.setattr(cache_store.settings, "scalar_disk_cache_mb", 10)
    cache_store.clear_memory()
    res = tmp_path / "res.h5"
    res.write_bytes(b"x")
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return (0.0, 1.0)

    monkeypatch.setattr(rs, "_compute_en_global_range_uncached", fake)
    handle = SimpleNamespace(filename=str(res))
    with cache_store.require_persistent_cache():
        for _ in range(3):                               # range、smooth、flat 三处调用
            assert rs._compute_en_global_range(handle, "g.h5", "PART", 4, 1) == (0.0, 1.0)
    assert len(calls) == 1

    calls.clear()
    monkeypatch.setattr(cache_store.settings, "scalar_cache_mb", 0)   # 缓存关闭（快照比对用）
    cache_store.clear_memory()
    for _ in range(2):
        rs._compute_en_global_range(handle, "g.h5", "PART", 4, 1)
    assert len(calls) == 2
