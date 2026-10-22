"""
单元边线（element-mesh-edges）：优先读取 L2 edges.h5，兼容旧 surface.h5。
去重后转成端点坐标。只依赖模型几何 → 最终 L3BE 响应包走统一缓存
（cache_store model 桶，key 带坐标文件与边文件签名），模型加载后预热。
"""
import os
from typing import Optional, Tuple

import h5py
import numpy as np

from ..infra.l3be import build as l3be_build
from . import cache_store


def _surface_h5(workspace: str, instance: str) -> str:
    return os.path.join(workspace, "l2", "geometry", f"{instance}_surface.h5")


def _unique_edges(edge_nodes: np.ndarray, n_nodes: int) -> np.ndarray:
    """(a,b)==(b,a) 去重，按 (min, max) 字典序升序；与 np.unique(np.sort(e,1), axis=0)
    结果相同，但用 int64 合成键做一维 unique，大模型快一个量级。"""
    if len(edge_nodes) == 0:
        return np.zeros((0, 2), dtype=edge_nodes.dtype)
    e = np.sort(edge_nodes, axis=1).astype(np.int64)
    base = max(int(n_nodes), int(e.max()) + 1)
    key = np.unique(e[:, 0] * base + e[:, 1])
    out = np.empty((len(key), 2), dtype=edge_nodes.dtype)
    out[:, 0] = key // base
    out[:, 1] = key % base
    return out


def instance_unique_edges(workspace: str, instance: str, n_nodes: int) -> Optional[np.ndarray]:
    """整个 instance 去重后的单元边 [E, 2]（节点行号，按 (min, max) 升序）；缺数据时 None。

    region-mesh-edges 每次按区域筛边，原来每次都整表读 HDF5 + 去重（大区域约 100 ms）；
    边表只依赖 L2 几何，放进统一缓存的内存层，key 带边文件签名，L2 重跑后自然失效。
    """
    edges_h5 = os.path.join(workspace, "l2", "geometry", f"{instance}_edges.h5")
    surface_h5 = _surface_h5(workspace, instance)
    # 两阶段 L2 的边数据在独立文件中；旧工作区仍可从 surface.h5 读取。
    edge_source = edges_h5 if os.path.isfile(edges_h5) else surface_h5
    sig = cache_store.file_sig(edge_source)
    if sig is None:
        return None
    key = ("instance_unique_edges", workspace, instance, edge_source, sig, int(n_nodes))
    hit = cache_store.mem_get(key)
    if hit is not None:
        return hit
    with h5py.File(edge_source, "r") as f:
        if "element_mesh_edges/edge_nodes" not in f:
            return None
        edge_nodes = f["element_mesh_edges/edge_nodes"][:]   # [E, 2] int32
    edges = _unique_edges(edge_nodes, n_nodes)
    edges.setflags(write=False)
    cache_store.mem_put(key, edges)
    return edges


def element_mesh_edges_payload(workspace: str, instance: str) -> Optional[Tuple[bytes, int, bool]]:
    """→ (L3BE payload, 边数 E, 是否命中缓存)；surface.h5 或边数据缺失时返回 None。"""
    surface_h5 = _surface_h5(workspace, instance)
    sig = cache_store.file_sig(surface_h5)
    if sig is None:
        return None
    edges_h5 = os.path.join(workspace, "l2", "geometry", f"{instance}_edges.h5")
    edge_sig = cache_store.file_sig(edges_h5)
    edge_source = edges_h5 if edge_sig is not None else surface_h5
    key = ("element_mesh_edges_v2", instance, sig,
           "sidecar" if edge_sig is not None else "embedded", edge_sig)
    cached = cache_store.payload_get(workspace, cache_store.MODEL_BUCKET, key)
    meta = cache_store.json_get(workspace, cache_store.MODEL_BUCKET, key + ("count",))
    if cached is not None and meta is not cache_store.MISS:
        return cached, int(meta), True

    with h5py.File(edge_source, "r") as f:
        if "element_mesh_edges/edge_nodes" not in f:
            return None
        edge_nodes = f["element_mesh_edges/edge_nodes"][:]   # [E, 2] int32
    with h5py.File(surface_h5, "r") as f:
        coords = f["nodes/coords_global"][:]                 # [N, 3] float32

    # Adjacent elements share edges; L2 may emit each shared edge twice.
    edge_nodes = _unique_edges(edge_nodes, len(coords))
    E = len(edge_nodes)
    edge_positions = np.ascontiguousarray(coords[edge_nodes.ravel()].reshape(E * 2, 3))
    payload = l3be_build([("edge_positions", edge_positions)])

    cache_store.json_put(workspace, cache_store.MODEL_BUCKET, key + ("count",), E)
    cache_store.payload_put(workspace, cache_store.MODEL_BUCKET, key, payload)
    return payload, E, False


def warm_element_mesh_edges(idx, strict: bool = False) -> int:
    """预计算所有 instance 的单元边线响应包，返回成功个数。"""
    n = 0
    for inst in sorted(idx.source_elem_etype.keys()):
        try:
            payload = element_mesh_edges_payload(idx.workspace, inst)
            if payload is not None:
                n += 1
            elif strict and len(idx.render_source_elem_row.get(inst, ())):
                raise RuntimeError(f"实例 {inst} 缺少单元边线数据")
        except Exception:
            if strict:
                raise
            pass
    return n
