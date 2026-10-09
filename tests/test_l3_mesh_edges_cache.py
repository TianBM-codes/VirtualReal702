"""element-mesh-edges 缓存回归测试：最终响应包进 cache_store model 桶，
去重改为 int64 合成键一维 unique（与旧的 np.unique(axis=0) 结果逐位一致）。"""
import h5py
import numpy as np
import pytest

from src.l3.infra.l3be import build as l3be_build
from src.l3.services import cache_store
from src.l3.services.mesh_edge_service import (
    _unique_edges,
    element_mesh_edges_payload,
)

INSTANCE = "PART-1-1"


@pytest.fixture(autouse=True)
def _clean():
    cache_store.clear_memory()
    yield
    cache_store.clear_memory()


def _boom(*args, **kwargs):
    raise AssertionError("cache miss: h5py.File was opened")


def _write_surface(workspace, edge_nodes, coords):
    d = workspace / "l2" / "geometry"
    d.mkdir(parents=True, exist_ok=True)
    with h5py.File(d / f"{INSTANCE}_surface.h5", "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes", data=edge_nodes)
        f.create_dataset("nodes/coords_global", data=coords)


def test_unique_edges_matches_numpy_axis0():
    rng = np.random.default_rng(7)
    e = rng.integers(0, 50, size=(400, 2)).astype(np.int32)
    ref = np.unique(np.sort(e, axis=1), axis=0)
    got = _unique_edges(e, 50)
    assert got.dtype == e.dtype and np.array_equal(got, ref)


def test_payload_matches_old_algorithm_and_caches(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    coords = rng.standard_normal((30, 3)).astype(np.float32)
    edges = rng.integers(0, 30, size=(120, 2)).astype(np.int32)
    edges = np.concatenate([edges, edges[:, ::-1]])          # 共享边被写两次
    _write_surface(tmp_path, edges, coords)

    uniq = np.unique(np.sort(edges, axis=1), axis=0)
    expected = l3be_build([("edge_positions", np.ascontiguousarray(
        coords[uniq.ravel()].reshape(len(uniq) * 2, 3)))])

    payload, E, hit = element_mesh_edges_payload(str(tmp_path), INSTANCE)
    assert (payload, E, hit) == (expected, len(uniq), False)
    assert list((tmp_path / "l3_cache" / "model" / "payloads").glob("*.bin"))

    monkeypatch.setattr(h5py, "File", _boom)
    payload2, E2, hit2 = element_mesh_edges_payload(str(tmp_path), INSTANCE)
    assert (payload2, E2, hit2) == (expected, len(uniq), True)


def test_missing_surface_returns_none(tmp_path):
    assert element_mesh_edges_payload(str(tmp_path), INSTANCE) is None


def test_sidecar_arrival_replaces_embedded_cache(tmp_path):
    coords = np.arange(12, dtype=np.float32).reshape(4, 3)
    _write_surface(tmp_path, np.array([[0, 1]], dtype=np.int32), coords)
    old_payload, old_count, _ = element_mesh_edges_payload(str(tmp_path), INSTANCE)
    assert old_count == 1

    sidecar = tmp_path / "l2" / "geometry" / f"{INSTANCE}_edges.h5"
    with h5py.File(sidecar, "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes",
                         data=np.array([[2, 3], [3, 2]], dtype=np.int32))
    payload, count, hit = element_mesh_edges_payload(str(tmp_path), INSTANCE)
    expected = l3be_build([("edge_positions", coords[[2, 3]])])
    assert (payload, count, hit) == (expected, 1, False)
    assert payload != old_payload

    with h5py.File(sidecar, "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes",
                         data=np.array([[0, 2], [1, 3]], dtype=np.int32))
    payload, count, hit = element_mesh_edges_payload(str(tmp_path), INSTANCE)
    assert count == 2 and not hit
    assert payload != expected


def test_core_without_edges_can_gain_sidecar(tmp_path, monkeypatch):
    geometry = tmp_path / "l2" / "geometry"
    geometry.mkdir(parents=True)
    coords = np.arange(9, dtype=np.float32).reshape(3, 3)
    with h5py.File(geometry / f"{INSTANCE}_surface.h5", "w") as f:
        f.create_dataset("nodes/coords_global", data=coords)
    assert element_mesh_edges_payload(str(tmp_path), INSTANCE) is None

    with h5py.File(geometry / f"{INSTANCE}_edges.h5", "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes",
                         data=np.array([[0, 2]], dtype=np.int32))
    result = element_mesh_edges_payload(str(tmp_path), INSTANCE)
    assert result[1:] == (1, False)
    monkeypatch.setattr(h5py, "File", _boom)
    assert element_mesh_edges_payload(str(tmp_path), INSTANCE) == (result[0], 1, True)
