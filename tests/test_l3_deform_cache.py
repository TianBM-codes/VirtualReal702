"""变形/动画家族缓存回归测试（统一缓存层 cache_store 之后的分层）。

  - render/positions、render/indices 常驻 ModelIndex（load_l2_render_data 加载，
    回退读盘时回填）；
  - 顶点位移向量 [Nv,3] 按 (U 文件签名, 几何签名, instance, 帧) 两级缓存，
    vertex-displacements / modal-shape / modal-animation 共用；
  - deformed-positions / modal-animation 在路由层缓存最终响应包
    （<workspace>/l3_cache/groups/<result_group>/payloads/），scale / n_frames 进 key，
    命中时响应头 X-Cache: hit；frame_deformed_with_aux 本身不再缓存；
  - deform_scale_stats 的小 dict 走小结果 LRU + JSON 磁盘层；
  - U 结果文件被重写 → 签名变 → 自动失效；
  - 法线聚合用 np.bincount（结果与 np.add.at 逐位一致）。

验证手法同 test_l3_result_scalar_cache：第一次调用后把 h5py.File monkeypatch
成抛错，命中缓存的第二次调用不得碰 H5。
"""
import os
import sqlite3

import h5py
import numpy as np
import pytest

from src.l3.services import cache_store
from src.l3.services.result_service import (
    _compute_vertex_normals,
    clear_result_caches,
    deform_scale_stats,
    frame_deformed_positions,
    frame_deformed_with_aux,
    frame_vertex_displacements,
    modal_animation_frames,
    modal_shape_displacement,
)
from src.l3.services.warmup_service import warm_odb_sync

INSTANCE = "PART-1-1"


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_result_caches()
    yield
    clear_result_caches()


def _boom(*args, **kwargs):
    raise AssertionError("cache miss: h5py.File was opened, expected cached result")


def _setup_deform_workspace(workspace, make_registry, *, with_aux=True):
    """
    单三角形 + 3 节点 U 场（2 帧）的最小变形工作区：
    render.h5（positions/indices）、可选 lines aux、instances/steps/result_files
    表（deform_scale_stats 与 warmup 需要）。
    """
    registry = make_registry(workspace, instance=INSTANCE)
    idx = registry.get("odb")

    for rel in ("l2/render", "l2/geometry"):
        (workspace / rel).mkdir(parents=True, exist_ok=True)

    surf_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    with h5py.File(workspace / "l2" / "render" / f"{INSTANCE}_render.h5", "w") as f:
        f.create_dataset("render/positions", data=surf_pos)
        f.create_dataset("render/indices", data=np.array([[0, 1, 2]], dtype=np.int32))
    idx.vtx_node_row[INSTANCE] = np.array([0, 1, 2], dtype=np.int32)
    idx.source_node_rows[INSTANCE] = np.array([[0, 1, 2]], dtype=np.int32)

    if with_aux:
        with h5py.File(workspace / "l2" / "geometry" / f"{INSTANCE}_surface.h5", "w") as f:
            f.create_dataset(
                "lines/positions",
                data=np.array([[[0, 0, 0], [1, 0, 0]]], dtype=np.float32))
            f.create_dataset("lines/node_rows", data=np.array([[0, 1]], dtype=np.int64))

    u = np.arange(9, dtype=np.float32).reshape(3, 3)
    u_path = workspace / "l1" / "results" / "Step-1__U.h5"
    with h5py.File(u_path, "w") as f:
        f.create_group("NODAL").create_group(INSTANCE).create_dataset(
            "data", data=np.stack([u, u * 2.0]))

    conn = sqlite3.connect(workspace / "manifest.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS instances (instance_name TEXT, part_name TEXT,"
        " geom_path TEXT, bbox_min TEXT, bbox_max TEXT)")
    conn.execute(
        "INSERT INTO instances VALUES (?, 'P', '', '[0,0,0]', '[10,1,1]')", (INSTANCE,))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS steps (step_name TEXT, result_group TEXT, nlgeom INT)")
    conn.execute("INSERT INTO steps VALUES ('Step-1', NULL, 0)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS result_files (step_name TEXT, field_name TEXT,"
        " result_group TEXT)")
    conn.execute("INSERT INTO result_files VALUES ('Step-1', 'U', NULL)")
    conn.commit()
    conn.close()
    return registry, surf_pos, u, u_path


def _route_client(monkeypatch, registry):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.l3.api.routes import results

    idx = registry.get("odb")
    monkeypatch.setattr(results.registry, "get", lambda odb_id: idx)
    app = FastAPI()
    app.include_router(results.router)
    return TestClient(app)


PAYLOAD_DIR = ("l3_cache", "groups", "_default", "payloads")


def test_deformed_with_aux_values(workspace, make_registry):
    registry, surf_pos, u, _ = _setup_deform_workspace(workspace, make_registry)
    p1, n1, aux1 = frame_deformed_with_aux(registry, "odb", INSTANCE, "Step-1", 0, 2.0)
    assert np.allclose(p1, surf_pos + 2.0 * u)
    assert [name for name, _ in aux1] == ["line_positions"]
    p2, n2 = frame_deformed_positions(registry, "odb", INSTANCE, "Step-1", 0, 2.0)
    assert np.array_equal(p1, p2) and np.array_equal(n1, n2)


def test_deformed_positions_route_payload_cache(workspace, make_registry, monkeypatch):
    """首次 miss 落盘响应包；再次请求 hit 且不碰 H5；内容逐字节一致。"""
    registry, _, _, _ = _setup_deform_workspace(workspace, make_registry)
    client = _route_client(monkeypatch, registry)
    url = f"/api/odb/odb/results/deformed-positions?instance={INSTANCE}&step=Step-1&scale=2.0"

    r1 = client.get(url)
    assert r1.status_code == 200 and r1.headers["X-Cache"] == "miss"
    assert list(workspace.joinpath(*PAYLOAD_DIR).glob("*.bin"))

    clear_result_caches()          # 内存层清空也不影响：响应包在磁盘
    monkeypatch.setattr(h5py, "File", _boom)
    r2 = client.get(url)
    assert r2.headers["X-Cache"] == "hit"
    assert r2.headers["X-Vertex-Count"] == "3"
    assert r1.content == r2.content


def test_scale_is_part_of_payload_key(workspace, make_registry, monkeypatch):
    registry, _, _, _ = _setup_deform_workspace(workspace, make_registry)
    client = _route_client(monkeypatch, registry)
    base = f"/api/odb/odb/results/deformed-positions?instance={INSTANCE}&step=Step-1"
    assert client.get(base + "&scale=1.0").headers["X-Cache"] == "miss"
    assert client.get(base + "&scale=5.0").headers["X-Cache"] == "miss"
    assert client.get(base + "&scale=1.0").headers["X-Cache"] == "hit"


def test_rewritten_u_file_invalidates_payload(workspace, make_registry, monkeypatch):
    registry, _, _, u_path = _setup_deform_workspace(workspace, make_registry)
    client = _route_client(monkeypatch, registry)
    url = f"/api/odb/odb/results/deformed-positions?instance={INSTANCE}&step=Step-1"
    r1 = client.get(url)

    new_u = np.full((3, 3), 7.0, dtype=np.float32)
    with h5py.File(u_path, "w") as f:
        f.create_group("NODAL").create_group(INSTANCE).create_dataset(
            "data", data=np.stack([new_u, new_u]))
    os.utime(u_path, ns=(os.stat(u_path).st_mtime_ns + 1_000_000,) * 2)

    r2 = client.get(url)
    assert r2.headers["X-Cache"] == "miss"
    assert r1.content != r2.content


def test_modal_animation_route_payload_cache(workspace, make_registry, monkeypatch):
    registry, _, _, _ = _setup_deform_workspace(workspace, make_registry)
    client = _route_client(monkeypatch, registry)
    url = (f"/api/odb/odb/results/modal-animation?instance={INSTANCE}"
           f"&step=Step-1&scale=1.0&n_frames=4")
    r1 = client.get(url)
    assert r1.headers["X-Cache"] == "miss"
    monkeypatch.setattr(h5py, "File", _boom)
    r2 = client.get(url)
    assert r2.headers["X-Cache"] == "hit" and r1.content == r2.content
    # n_frames 进 key
    assert client.get(url.replace("n_frames=4", "n_frames=8")).headers["X-Cache"] == "miss"


def test_disp_vertex_cache_shared_across_modal_family(workspace, make_registry, monkeypatch):
    """modal-shape 预热后，vertex-displacements / modal-animation 不再碰 H5。"""
    registry, _, u, _ = _setup_deform_workspace(workspace, make_registry)
    d0 = modal_shape_displacement(registry, "odb", INSTANCE, "Step-1", 0)
    assert np.array_equal(d0, u)

    monkeypatch.setattr(h5py, "File", _boom)
    d1 = frame_vertex_displacements(registry, "odb", INSTANCE, "Step-1", 0)
    blob = modal_animation_frames(registry, "odb", INSTANCE, "Step-1", 0,
                                  scale=1.0, n_frames=4)
    assert np.array_equal(d0, d1)
    header = np.frombuffer(blob[:8], dtype=np.uint32)
    assert header[0] == 4 and header[1] == 3


def test_disp_vertex_disk_cache_survives_memory_clear(workspace, make_registry, monkeypatch):
    registry, _, u, _ = _setup_deform_workspace(workspace, make_registry)
    d1 = frame_vertex_displacements(registry, "odb", INSTANCE, "Step-1", 0)

    clear_result_caches()
    monkeypatch.setattr(h5py, "File", _boom)
    d2 = frame_vertex_displacements(registry, "odb", INSTANCE, "Step-1", 0)
    assert np.array_equal(d1, d2) and np.array_equal(d2, u)


def test_deform_scale_stats_cached(workspace, make_registry, monkeypatch):
    registry, _, _, _ = _setup_deform_workspace(workspace, make_registry)
    s1 = deform_scale_stats(registry, "odb", "Step-1", 0)
    assert s1["max_disp"] == pytest.approx(8.0)
    assert list((workspace / "l3_cache" / "groups" / "_default" / "json").glob("*.json"))

    monkeypatch.setattr(h5py, "File", _boom)
    assert deform_scale_stats(registry, "odb", "Step-1", 0) == s1
    clear_result_caches()   # 磁盘层
    assert deform_scale_stats(registry, "odb", "Step-1", 0) == s1


def test_vertex_normals_bincount_matches_add_at():
    """bincount 版法线聚合与旧 np.add.at 参考实现逐位一致。"""
    rng = np.random.default_rng(42)
    nv, nt = 200, 400
    positions = rng.standard_normal((nv, 3)).astype(np.float32)
    indices = rng.integers(0, nv, size=(nt, 3)).astype(np.int32)

    got = _compute_vertex_normals(positions, indices)

    v0, v1, v2 = (positions[indices[:, 0]], positions[indices[:, 1]],
                  positions[indices[:, 2]])
    face_normals = np.cross(v1 - v0, v2 - v0)
    ref = np.zeros_like(positions, dtype=np.float64)
    np.add.at(ref, indices[:, 0], face_normals)
    np.add.at(ref, indices[:, 1], face_normals)
    np.add.at(ref, indices[:, 2], face_normals)
    lengths = np.linalg.norm(ref, axis=1, keepdims=True)
    lengths = np.where(lengths < 1e-12, 1.0, lengths)
    ref = (ref / lengths).astype(np.float32)
    assert np.allclose(got, ref, atol=1e-6)


def test_warmup_covers_deform_family(workspace, make_registry, monkeypatch):
    """warm_odb_sync 后 deform_scale_stats / vertex-displacements 均不再读 H5。"""
    registry, _, _, _ = _setup_deform_workspace(workspace, make_registry)
    report = warm_odb_sync("odb", reason="test", reg=registry)
    assert report["status"] == "done"
    assert report["deform_warmed"] >= 2   # stats + 1 instance 位移向量

    monkeypatch.setattr(h5py, "File", _boom)
    deform_scale_stats(registry, "odb", "Step-1", 0)
    frame_vertex_displacements(registry, "odb", INSTANCE, "Step-1", 0)


def test_deform_cache_disabled_by_config(workspace, make_registry, monkeypatch):
    monkeypatch.setattr(cache_store.settings, "scalar_cache_mb", 0)
    registry, _, _, _ = _setup_deform_workspace(workspace, make_registry)
    client = _route_client(monkeypatch, registry)
    url = f"/api/odb/odb/results/deformed-positions?instance={INSTANCE}&step=Step-1"
    assert client.get(url).headers["X-Cache"] == "miss"
    assert client.get(url).headers["X-Cache"] == "miss"   # 关闭时从不命中
    frame_vertex_displacements(registry, "odb", INSTANCE, "Step-1", 0)
    assert not (workspace / "l3_cache").exists()

    monkeypatch.setattr(h5py, "File", _boom)
    with pytest.raises(AssertionError, match="cache miss"):
        frame_vertex_displacements(registry, "odb", INSTANCE, "Step-1", 0)
