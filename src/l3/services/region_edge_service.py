"""
区域边线（color-code/{instance}/region-mesh-edges、region-outline）与按分类显示的
几何类缓存预热。

区域 = 某着色方案（etype / material / section …，或单个单元集合）下的一个图例取值。
边线 / 外轮廓只依赖模型几何，但大区域每次现算要数秒（241 万节点模型整模型区域
约 10 s / 2.6 s）→ 最终 L3BE 响应包走统一缓存（model 桶，key = 面分类 key + 边/坐标
文件签名）。warm_color_views 在导入时把着色包、图例条目、区域边线一起预算。
"""
import logging
import time
from typing import Optional, Tuple

import numpy as np

from ..core.errors import NotFoundError, ValidationError
from ..infra.l3be import build as l3be_build
from src.l1.manifest_schema import canon_instance
from . import cache_store
from . import color_service
from .mesh_edge_service import _surface_h5, edge_source, instance_unique_edges

logger = logging.getLogger(__name__)


def _region_sets(scheme: str, region: str):
    # elset 方案的区域就是集合名本身，分类按 [region] 单独计算（与 region_face_mask 一致）
    return [region] if scheme == "elset" else None


def mesh_edges_key(idx, instance: str, scheme: str, region: str) -> Optional[tuple]:
    vkey = color_service.view_key(idx, instance, scheme, _region_sets(scheme, region))
    if vkey is None:
        return None
    ws = idx.workspace
    src = edge_source(ws, instance)
    return ("region_mesh_edges_v1", vkey, region, src, cache_store.file_sig(src),
            cache_store.file_sig(_surface_h5(ws, instance)))


def outline_key(idx, instance: str, scheme: str, region: str) -> Optional[tuple]:
    vkey = color_service.view_key(idx, instance, scheme, _region_sets(scheme, region))
    if vkey is None:
        return None
    return ("region_outline_v1", vkey, region,
            cache_store.file_sig(_surface_h5(idx.workspace, instance)))


def _cached(workspace: str, key: Optional[tuple]):
    if key is None:
        return None
    payload = cache_store.payload_get(workspace, cache_store.MODEL_BUCKET, key)
    count = cache_store.json_get(workspace, cache_store.MODEL_BUCKET, key + ("count",))
    if payload is None or count is cache_store.MISS:
        return None
    return payload, int(count), True


def _store(workspace: str, key: Optional[tuple], payload: bytes, count: int):
    if key is not None:
        cache_store.json_put(workspace, cache_store.MODEL_BUCKET, key + ("count",), count)
        cache_store.payload_put(workspace, cache_store.MODEL_BUCKET, key, payload)
    return payload, count, False


def _edge_payload(coords_global: np.ndarray, edges: np.ndarray) -> bytes:
    E = len(edges)
    edge_positions = np.ascontiguousarray(
        coords_global[edges.ravel()].reshape(E * 2, 3).astype(np.float32)
    )
    return l3be_build([("edge_positions", edge_positions)])


def _region_arrays(idx, instance: str, scheme: str, region: str):
    """→ (face_mask, render_indices, vtx_node_row, coords_global)；区域为空或缺数据时 None。"""
    face_mask = color_service.region_face_mask(idx, instance, scheme, region)
    if face_mask is None or not np.any(face_mask):
        return None
    render_indices = idx.render_indices.get(instance)
    vtx_node_row   = idx.vtx_node_row.get(instance)
    coords_global  = idx.coords_global.get(instance)
    if render_indices is None or vtx_node_row is None or coords_global is None:
        return None
    return face_mask, render_indices, vtx_node_row, coords_global


def region_mesh_edges(idx, instance: str, scheme: str,
                      region: str) -> Optional[Tuple[bytes, int, bool]]:
    """区域内的单元边 → (L3BE 包 edge_positions [E*2, 3] float32, E, 是否命中缓存)；空区域 None。

    两个端点都属于区域渲染顶点的边保留（四边形单元显示为四边形，不显示三角剖分对角线）。
    """
    key = mesh_edges_key(idx, instance, scheme, region)
    hit = _cached(idx.workspace, key)
    if hit is not None:
        return hit
    arrays = _region_arrays(idx, instance, scheme, region)
    if arrays is None:
        return None
    face_mask, render_indices, vtx_node_row, coords_global = arrays

    node_in_region = np.zeros(len(coords_global), dtype=bool)
    node_in_region[vtx_node_row[render_indices[face_mask]]] = True

    edge_nodes = instance_unique_edges(idx.workspace, instance, len(coords_global))
    if edge_nodes is None:
        return None

    # 边表已去重且有序，筛选后与原 np.unique(np.sort(...), axis=0) 结果一致
    edge_mask = node_in_region[edge_nodes[:, 0]] & node_in_region[edge_nodes[:, 1]]
    filtered = edge_nodes[edge_mask]
    return _store(idx.workspace, key, _edge_payload(coords_global, filtered), len(filtered))


def region_outline(idx, instance: str, scheme: str,
                   region: str) -> Optional[Tuple[bytes, int, bool]]:
    """区域外边界 → (L3BE 包, 边数 E, 是否命中缓存)；空区域 None。

    只属于一个区域三角形的边是边界边。三角剖分对角线自动排除：四边形的两个
    子三角形属于同一区域，对角线出现两次。
    """
    key = outline_key(idx, instance, scheme, region)
    hit = _cached(idx.workspace, key)
    if hit is not None:
        return hit
    arrays = _region_arrays(idx, instance, scheme, region)
    if arrays is None:
        return None
    face_mask, render_indices, vtx_node_row, coords_global = arrays

    region_tris = render_indices[face_mask]          # [Rf_reg, 3] vertex indices
    v0, v1, v2 = region_tris[:, 0], region_tris[:, 1], region_tris[:, 2]

    # Convert to node rows: vertices are NOT shared across element boundaries,
    # but same-position vertices of adjacent elements have the same vtx_node_row.
    # Deduplicating edges by node-row pairs correctly collapses interior edges
    # (which appear in two adjacent triangles) and retains only boundary edges.
    n0 = vtx_node_row[v0]
    n1 = vtx_node_row[v1]
    n2 = vtx_node_row[v2]

    edges = np.concatenate([
        np.stack([np.minimum(n0, n1), np.maximum(n0, n1)], axis=1),
        np.stack([np.minimum(n1, n2), np.maximum(n1, n2)], axis=1),
        np.stack([np.minimum(n0, n2), np.maximum(n0, n2)], axis=1),
    ])   # [3*Rf_reg, 2]  — node row pairs

    N_nodes = len(coords_global)
    packed = edges[:, 0].astype(np.int64) * N_nodes + edges[:, 1].astype(np.int64)
    unique_packed, counts = np.unique(packed, return_counts=True)
    boundary_packed = unique_packed[counts == 1]

    boundary_edges = np.stack([
        (boundary_packed // N_nodes).astype(np.int32),
        (boundary_packed %  N_nodes).astype(np.int32),
    ], axis=1)   # [E_boundary, 2]  — node row pairs
    return _store(idx.workspace, key, _edge_payload(coords_global, boundary_edges),
                  len(boundary_edges))


# ─── 导入时预热 ───────────────────────────────────────────────────────────────

def warm_color_views(idx, strict: bool = False) -> dict:
    """预计算每个实例 × 着色方案（含每个单元集合单选）的着色包与图例条目，
    以及每个区域的单元边线 / 外轮廓。已在磁盘上的大条目只检查存在、不重读。

    请求本身就会报参数错误的组合（ValidationError / NotFoundError，例如集合
    在 L1 中查不到）记为跳过，与接口行为一致；其他异常 strict 时抛出。
    多选单元集合的任意组合不穷举，首次请求时计算并缓存。
    返回 {"color_code", "legend_entries", "region_mesh_edges", "region_outline",
          "skipped", "seconds"}。
    """
    t0 = time.time()
    stats = {"color_code": 0, "legend_entries": 0, "region_mesh_edges": 0,
             "region_outline": 0, "skipped": 0}
    ws = idx.workspace

    def _done(kind, key):
        if key is None or not cache_store.payload_exists(ws, cache_store.MODEL_BUCKET, key):
            return False
        return kind == "color_code" or cache_store.json_get(
            ws, cache_store.MODEL_BUCKET, key + ("count",)) is not cache_store.MISS

    def _run(kind, key, fn, *args):
        try:
            if _done(kind, key):
                stats[kind] += 1
                return
            fn(*args)
            stats[kind] += 1
        except (ValidationError, NotFoundError) as exc:
            stats["skipped"] += 1
            logger.info("color views %s %s skipped: %s", kind, args[1:], exc)
        except Exception:
            if strict:
                raise
            stats["skipped"] += 1
            logger.exception("color views %s %s failed", kind, args[1:])

    try:
        all_schemes = color_service.get_all_schemes(idx)
    except Exception:
        if strict:
            raise
        all_schemes = {"schemes": ["etype"], "elsets": []}
    elsets = all_schemes["elsets"] if "elset" in all_schemes["schemes"] else []
    known = set(idx.source_elem_etype.keys())

    for inst in sorted(idx.source_elem_etype.keys()):
        etype_arr    = idx.source_elem_etype.get(inst)
        elem_row_arr = idx.render_source_elem_row.get(inst)
        if etype_arr is None or elem_row_arr is None:
            continue
        try:
            schemes = [sc for sc in color_service.get_schemes(idx, inst)["schemes"] if sc != "elset"]
        except Exception:
            if strict:
                raise
            schemes = ["etype"]
        # 前端把同一份 set_names 广播给所有实例：每个实例都会收到每个集合
        combos = [(sc, None) for sc in schemes] + [("elset", [sn]) for sn in elsets]
        for scheme, sets in combos:
            _run("color_code", color_service.color_code_payload_key(idx, inst, scheme, sets),
                 color_service.color_code_payload, idx, inst, scheme, sets)
            _run("legend_entries", None, color_service.get_legend_entries, idx, inst, scheme, sets)
            if scheme == "elset":
                owner, _ = color_service._split_instance_prefix(sets[0], known)
                # 别的实例的集合在本实例上是空区域，请求时直接返回空，不必预算
                regions = sets if owner in (None, canon_instance(inst)) else []
            else:
                try:
                    _, regions = color_service._face_codes(
                        idx, inst, scheme, None, etype_arr, elem_row_arr)
                except (ValidationError, NotFoundError):
                    regions = []
            for region in regions:
                _run("region_mesh_edges", mesh_edges_key(idx, inst, scheme, region),
                     region_mesh_edges, idx, inst, scheme, region)
                _run("region_outline", outline_key(idx, inst, scheme, region),
                     region_outline, idx, inst, scheme, region)
    stats["seconds"] = round(time.time() - t0, 1)
    return stats
