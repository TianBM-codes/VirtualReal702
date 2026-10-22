"""按分类显示的几何类响应缓存（1440w 探测：大模型 color-code / legend-entries /
region-mesh-edges / region-outline 每次或首次要 0.6～10 s）。

- color-code 整包按（面分类 key + 用户改色/改名）存盘，改色后 key 变化、立即生效；
- legend-entries 只依赖几何的部分（顺序、标题、配色、面数/单元数）存盘，改色/改名每次现查；
- region-mesh-edges / region-outline 整包按（面分类 key + 边/坐标文件签名）存盘；
- warm_color_views 导入时预算以上全部（每个方案、每个单元集合单选、每个区域）。
缓存命中与现算的字节逐一相同。
"""
import asyncio
import os
from collections import Counter
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from src.inp import parse_inp
from src.inp.exporter import export_l1
from src.l3.api.routes import color_code as route
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services import cache_store
from src.l3.services import color_service as C
from src.l3.services import region_edge_service as R

_INP = (
    "*Part, name=PA\n"
    "*Node\n"
    + "".join(f"{i}, {i}.0, 0.0, 0.0\n" for i in range(1, 25))
    + "*Element, type=C3D8R\n"
    "1, 1, 2, 3, 4, 5, 6, 7, 8\n"
    "2, 9, 10, 11, 12, 13, 14, 15, 16\n"
    "3, 17, 18, 19, 20, 21, 22, 23, 24\n"
    "*Elset, elset=SetA\n1, 2\n"
    "*Elset, elset=SetB\n3,\n"
    "*Solid Section, elset=SetA, material=Steel\n1.,\n"
    "*Solid Section, elset=SetB, material=Alu\n1.,\n"
    "*End Part\n"
    "*Assembly, name=Assembly\n"
    "*Instance, name=PA-1, part=PA\n*End Instance\n"
    "*End Assembly\n"
    "*Material, name=Steel\n*Elastic\n210000., 0.3\n"
    "*Material, name=Alu\n*Elastic\n70000., 0.3\n"
)
INST = "PA-1"


class _FakeIdx:
    """6 个表面三角形（每单元 2 个，打乱顺序），三角形汤顶点映射到 L1 节点行。"""

    def __init__(self, ws):
        self.workspace = ws
        self.odb_id = "t"
        self.is_render_ready = True
        self.legend_scan_cache = {}
        self.vtx_tri_idx = {}
        et, row = C._full_element_arrays(self, INST)
        order = np.array([2, 0, 1, 0, 2, 1])
        self.source_elem_etype = {INST: et[order]}
        self.render_source_elem_row = {INST: row[order]}
        self.averaging_data = {INST: {
            "elem_etype": et, "elem_row": row,
            "default_domain_id": np.array([7, 7, 3]),
        }}
        # 单元行号 r 的两个面拼成四边形 (8r, 8r+1, 8r+2, 8r+3)，对角线 8r–8r+2
        vtx, seen = [], set()
        for r in row[order]:
            b = 8 * int(r)
            vtx += [b, b + 1, b + 2] if r not in seen else [b, b + 2, b + 3]
            seen.add(r)
        self.vtx_node_row = {INST: np.array(vtx, dtype=np.int32)}
        self.render_indices = {INST: np.arange(18, dtype=np.int32).reshape(6, 3)}
        self.coords_global = {INST: np.arange(72, dtype=np.float32).reshape(24, 3)}


@pytest.fixture(autouse=True)
def _clean():
    cache_store.clear_memory()
    yield
    cache_store.clear_memory()


def _build(tmp_path):
    inp = os.path.join(str(tmp_path), "m.inp")
    with open(inp, "w") as fh:
        fh.write(_INP)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    export_l1(parse_inp(inp), ws)
    idx = _FakeIdx(ws)
    geometry = os.path.join(ws, "l2", "geometry")
    os.makedirs(geometry, exist_ok=True)
    with h5py.File(os.path.join(geometry, f"{INST}_surface.h5"), "w") as f:
        f.create_dataset("nodes/coords_global", data=idx.coords_global[INST])
    # 每个单元四边形的边 + 对角线（含反向重复），外加跨单元的一条边：去重后 4×3 + 1 = 13
    edges = []
    for e in range(3):
        b = 8 * e
        edges += [[b, b + 1], [b + 1, b + 2], [b + 2, b], [b + 1, b], [b + 2, b + 3]]
    edges.append([2, 9])
    with h5py.File(os.path.join(geometry, f"{INST}_edges.h5"), "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes", data=np.array(edges, dtype=np.int32))
    return idx


def _boom(*args, **kwargs):
    raise AssertionError("cache miss: h5py.File was opened")


def _uncached(monkeypatch, fn, *args):
    """关掉缓存现算一次，作为逐字节比对的参照。"""
    with monkeypatch.context() as m:
        m.setattr(cache_store.settings, "scalar_cache_mb", 0)
        return fn(*args)


def _route(monkeypatch, idx):
    monkeypatch.setattr(route, "registry", SimpleNamespace(get=lambda _: idx))


# ─── color-code ───────────────────────────────────────────────────────────────

def test_color_code_payload_cached_and_identical(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    ref, hit0 = _uncached(monkeypatch, C.color_code_payload, idx, INST, "material", None)
    assert hit0 is False
    p1, hit1 = C.color_code_payload(idx, INST, "material")
    cache_store.clear_memory()
    monkeypatch.setattr(h5py, "File", _boom)
    p2, hit2 = C.color_code_payload(idx, INST, "material")
    assert (hit1, hit2) == (False, True)
    assert p1 == ref and p2 == ref


def test_color_code_route_reports_cache(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    _route(monkeypatch, idx)
    r1 = asyncio.run(route.get_color_code("t", INST, "elset", "PA-1.SETA"))
    r2 = asyncio.run(route.get_color_code("t", INST, "elset", "PA-1.SETA"))
    assert (r1.headers["X-Cache"], r2.headers["X-Cache"]) == ("miss", "hit")
    assert r1.body == r2.body and r2.headers["X-Face-Count"] == "6"


def test_color_override_changes_payload_key(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    p1, _ = C.color_code_payload(idx, INST, "material")
    _, legend = C.get_color_code(idx, INST, "material")
    ManifestRepo(idx.workspace).set_legend_overrides(INST, "material", [
        {"legend_key": legend[0]["legend_key"], "display_name": "改名",
         "color_r": 1.0, "color_g": 0.0, "color_b": 0.0},
    ])
    p2, hit = C.color_code_payload(idx, INST, "material")
    ref, _ = _uncached(monkeypatch, C.color_code_payload, idx, INST, "material", None)
    assert hit is False and p2 != p1 and p2 == ref


def test_color_code_errors_unchanged(tmp_path):
    idx = _build(tmp_path)
    from src.l3.core.errors import ValidationError
    with pytest.raises(ValidationError):
        C.color_code_payload(idx, INST, "nope")
    with pytest.raises(ValidationError):
        C.color_code_payload(idx, INST, "elset", ["PA-1.NOSUCHSET"])


# ─── legend-entries ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("scheme,sets", [
    ("material", None), ("section", None), ("etype", None),
    ("elset", ["PA-1.SETA"]), ("elset", ["PA-1.SETB", "PA-1.SETA"]),
])
def test_legend_entries_base_survives_memory_clear(tmp_path, monkeypatch, scheme, sets):
    idx = _build(tmp_path)
    ref = _uncached(monkeypatch, C.get_legend_entries, idx, INST, scheme, sets)
    first = C.get_legend_entries(idx, INST, scheme, sets)
    cache_store.clear_memory()
    idx.legend_scan_cache.clear()
    monkeypatch.setattr(h5py, "File", _boom)
    second = C.get_legend_entries(idx, INST, scheme, sets)
    assert first == ref and second == ref


def test_legend_entries_overrides_apply_on_cached_base(tmp_path):
    idx = _build(tmp_path)
    before = C.get_legend_entries(idx, INST, "material")
    key = before[0]["legend_key"]
    ManifestRepo(idx.workspace).set_legend_overrides(INST, "material", [
        {"legend_key": key, "display_name": "改名", "color_r": 1.0, "color_g": 0.0,
         "color_b": 0.0},
    ])
    after = C.get_legend_entries(idx, INST, "material")
    e = next(x for x in after if x["legend_key"] == key)
    assert e["display_name"] == "改名" and e["user_color"] and e["color_r"] == 1.0


def test_elset_elem_counts_match_label_counter(tmp_path):
    """bincount 计数与原逐单元标签 Counter 一致（含重复集合名、重叠集合）。"""
    idx = _build(tmp_path)
    sets = ["PA-1.SETA", "PA-1.SETB", "PA-1.SETA"]
    full_et, full_row = C._full_element_arrays(idx, INST)
    ref = dict(Counter(C._labels_from_elsets(idx, INST, full_et, full_row, sets)))
    got = C._total_elem_counts_per_label(idx, INST, "elset", sets, None)
    assert got == ref


# ─── region-mesh-edges / region-outline ──────────────────────────────────────

@pytest.mark.parametrize("fn", [R.region_mesh_edges, R.region_outline])
@pytest.mark.parametrize("scheme,region", [
    ("material", "Steel"), ("etype", "C3D8R"), ("elset", "PA-1.SETB"),
])
def test_region_payload_cached_and_identical(tmp_path, monkeypatch, fn, scheme, region):
    idx = _build(tmp_path)
    ref = _uncached(monkeypatch, fn, idx, INST, scheme, region)
    assert ref is not None and ref[1] > 0
    first = fn(idx, INST, scheme, region)
    cache_store.clear_memory()
    monkeypatch.setattr(h5py, "File", _boom)
    second = fn(idx, INST, scheme, region)
    assert first[:2] == ref[:2] and second[:2] == ref[:2]
    assert (first[2], second[2]) == (False, True)


def test_region_mesh_edges_without_unique_matches_reference(tmp_path, monkeypatch):
    """去掉 np.unique 后的区域节点标记与原实现结果相同。"""
    idx = _build(tmp_path)
    payload, E, _ = _uncached(monkeypatch, R.region_mesh_edges, idx, INST, "material", "Steel")
    mask = C.region_face_mask(idx, INST, "material", "Steel")
    rows = idx.vtx_node_row[INST][np.unique(idx.render_indices[INST][mask].ravel())]
    node_in = np.zeros(24, dtype=bool)
    node_in[rows] = True
    edges = R.instance_unique_edges(idx.workspace, INST, 24)
    kept = edges[node_in[edges[:, 0]] & node_in[edges[:, 1]]]
    assert E == len(kept)
    assert payload == R._edge_payload(idx.coords_global[INST], kept)


def test_region_cache_invalidated_by_rewritten_edges(tmp_path):
    idx = _build(tmp_path)
    _, E1, _ = R.region_mesh_edges(idx, INST, "etype", "C3D8R")
    path = os.path.join(idx.workspace, "l2", "geometry", f"{INST}_edges.h5")
    with h5py.File(path, "w") as f:
        f.create_dataset("element_mesh_edges/edge_nodes",
                         data=np.array([[0, 1], [1, 2], [0, 2], [0, 3]], dtype=np.int32))
    cache_store.clear_memory()
    _, E2, hit = R.region_mesh_edges(idx, INST, "etype", "C3D8R")
    assert hit is False and (E1, E2) == (13, 4)


def test_empty_region_returns_none(tmp_path):
    idx = _build(tmp_path)
    assert R.region_mesh_edges(idx, INST, "material", "NOPE") is None
    assert R.region_outline(idx, INST, "material", "NOPE") is None


# ─── 预热 ─────────────────────────────────────────────────────────────────────

def test_warm_color_views_covers_viewer_requests(tmp_path, monkeypatch):
    idx = _build(tmp_path)
    stats = R.warm_color_views(idx, strict=True)
    schemes = [s for s in C.get_schemes(idx, INST)["schemes"] if s != "elset"]
    elsets = C.get_all_schemes(idx)["elsets"]
    assert stats["color_code"] == stats["legend_entries"] == len(schemes) + len(elsets)
    assert stats["region_mesh_edges"] == stats["region_outline"] > 0
    assert stats["skipped"] == 0

    cache_store.clear_memory()
    idx.legend_scan_cache.clear()
    monkeypatch.setattr(h5py, "File", _boom)
    for scheme, sets in [(s, None) for s in schemes] + [("elset", [e]) for e in elsets]:
        assert C.color_code_payload(idx, INST, scheme, sets)[1] is True
        C.get_legend_entries(idx, INST, scheme, sets)
    for region in ("Steel", "Alu"):
        assert R.region_mesh_edges(idx, INST, "material", region)[2] is True
        assert R.region_outline(idx, INST, "material", region)[2] is True
    assert R.region_mesh_edges(idx, INST, "elset", "PA-1.SETA")[2] is True


def test_warm_color_views_second_run_reads_nothing(tmp_path, monkeypatch):
    """已预算的大条目只检查存在：追加结果时重跑准备不重读几 GB 响应包。"""
    idx = _build(tmp_path)
    first = R.warm_color_views(idx, strict=True)
    monkeypatch.setattr(cache_store, "payload_get", lambda *a: pytest.fail("不应读响应包"))
    second = R.warm_color_views(idx, strict=True)
    first.pop("seconds"), second.pop("seconds")
    assert second == first
