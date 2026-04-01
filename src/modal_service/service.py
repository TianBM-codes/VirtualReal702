"""
模态分析服务层。

设计原则
--------
- 无内存缓存：每次请求都从数据源重新读取，保证数据最新。
- 数据源可替换：所有读取逻辑集中在 _load_raw() 里，
  将来从 JSON 换成数据库只需改这一个函数，其余不动。

当前数据源：JSON 文件（通过 POST /api/modal/load 注册路径）

将来换数据库时替换：
    def _load_raw(model_id: str) -> dict:
        row = db.query("SELECT ... FROM modal WHERE id=?", model_id)
        return row_to_dict(row)
"""
import hashlib
import json
import logging
import math
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import numpy as np

# ── 注册表：model_id → 文件路径（后续换成 DB 连接信息）────────────────────
_registry: Dict[str, str] = {}


def register(path: str) -> str:
    """注册一个 JSON 文件，返回 model_id（路径的 MD5 前 8 位）。"""
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在：{abs_path}")
    model_id = hashlib.md5(abs_path.encode()).hexdigest()[:8]
    _registry[model_id] = abs_path
    return model_id


def is_registered(model_id: str) -> bool:
    return model_id in _registry


# ── 数据读取层（换 DB 只改这里）──────────────────────────────────────────
def _load_raw(model_id: str) -> dict:
    """
    从数据源读取模态数据，返回原始 dict。

    当前实现：读 JSON 文件。
    换 DB 时：改成查询对应表，返回相同结构的 dict 即可。
    """
    path = _registry.get(model_id)
    if path is None:
        raise KeyError(f"model_id '{model_id}' 未注册")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_mode(raw: dict, order: int) -> dict:
    """从原始数据里取指定阶次（1-based）的模态。"""
    for shape in raw["modal_shape"]:
        if int(shape["order"]) == order:
            return shape
    orders = [s["order"] for s in raw["modal_shape"]]
    raise ValueError(f"阶次 {order} 不存在，可用：{orders}")


# ── 计算工具函数 ──────────────────────────────────────────────────────────

def _scale_factor(real_flat: list, imag_flat: list,
                  max_scalar_size: float, coefficient: float) -> float:
    """
    scaleFactor = maxScalarSize / coefficient / max_i(√(real[i]²+imag[i]²))

    分母是整个 flat 数组里单个分量的最大复数幅值。
    保证放大后最大变形量 = maxScalarSize / coefficient。
    """
    r  = np.asarray(real_flat, dtype=np.float64)
    im = np.asarray(imag_flat, dtype=np.float64)
    max_amp = float(np.max(np.sqrt(r * r + im * im)))
    if max_amp < 1e-15:
        return 1.0
    return float(max_scalar_size / coefficient / max_amp)


def _component_data(real_flat: list, imag_flat: list,
                    component: str) -> Tuple[List[float], float, float]:
    """
    按选定分量计算每节点幅值，返回 (values, minVal, maxVal)。

    component 取值：'usum' | 'ux' | 'uy' | 'uz'

    所有分量均为复数模：
      ux[j] = √(real_x[j]² + imag_x[j]²)
      usum[j] = √(ux² + uy² + uz²)   (Hermitian 范数，详见科普 §20)
    """
    count = len(real_flat) // 3
    r  = np.asarray(real_flat, dtype=np.float64).reshape(count, 3)
    im = np.asarray(imag_flat, dtype=np.float64).reshape(count, 3)

    ux = np.sqrt(r[:, 0] ** 2 + im[:, 0] ** 2)
    uy = np.sqrt(r[:, 1] ** 2 + im[:, 1] ** 2)
    uz = np.sqrt(r[:, 2] ** 2 + im[:, 2] ** 2)

    if   component == "ux":   vals = ux
    elif component == "uy":   vals = uy
    elif component == "uz":   vals = uz
    else:                     vals = np.sqrt(ux ** 2 + uy ** 2 + uz ** 2)

    return vals.tolist(), float(vals.min()), float(vals.max())


def _new_pos(pos_flat: list, real_flat: list, scale: float) -> List[float]:
    """
    newPos[i] = originPos[i] + real[i] * scaleFactor

    用实部做变形（代表某个相位截面的位移形状），不用幅值。
    """
    p = np.asarray(pos_flat,  dtype=np.float64)
    r = np.asarray(real_flat, dtype=np.float64)
    return (p + r * scale).tolist()


def _triangulate(index_flat: list, item_size: int) -> List[int]:
    """
    把 flat 连接表三角化，返回 flat 三角形索引数组。
      item_size=3 → 已是三角形，直接返回
      item_size=4 → QUAD4，扇形剖分成 2 个三角形
      其他        → 只取前 3 个节点（简化）
    """
    conn = np.asarray(index_flat, dtype=np.int32).reshape(-1, item_size)
    if item_size == 3:
        return conn.flatten().tolist()
    elif item_size == 4:
        E    = len(conn)
        tris = np.empty((E * 2, 3), dtype=np.int32)
        tris[0::2] = conn[:, [0, 1, 2]]
        tris[1::2] = conn[:, [0, 2, 3]]
        return tris.flatten().tolist()
    else:
        return conn[:, :3].flatten().tolist()


# ── 对外接口（被 router 调用）────────────────────────────────────────────

def get_geometry(model_id: str) -> dict:
    """
    #1 模型接口。
    返回原始节点坐标和三角化后的索引数组。
    前端用这两个建 Three.js BufferGeometry（indexed mesh）。

    注意：elements.index 里存的是节点 ID（来自 points.ids），
    不一定是 0-based 行号，需要先映射。
    """
    raw   = _load_raw(model_id)
    pts   = raw["points"]
    elems = raw["elements"]

    # 自动判断 elements.index 里存的是节点 ID 还是 0-based 行号：
    # 如果最大值 < 节点数，说明已经是行号，直接用；否则做 ID→行号映射。
    raw_idx = elems["index"]
    N       = len(pts["ids"])
    if raw_idx and max(raw_idx) < N:
        # 直接是 0-based 行号
        idx_rows = raw_idx
    else:
        # 需要从节点 ID 映射
        id_to_row = {nid: i for i, nid in enumerate(pts["ids"])}
        try:
            idx_rows = [id_to_row[nid] for nid in raw_idx]
        except KeyError as e:
            logger.error("get_geometry: index value %s not found in points.ids", e)
            raise ValueError(f"elements.index 包含未知节点 ID: {e}") from e

    # 原始 quad 边（去重），用于前端画干净的线框（不含三角化对角线）
    item_size = elems["ItemSize"]
    quads     = np.asarray(idx_rows, dtype=np.int32).reshape(-1, item_size)
    edge_set: set = set()
    for q in quads:
        for k in range(item_size):
            a, b = int(q[k]), int(q[(k + 1) % item_size])
            edge_set.add((min(a, b), max(a, b)))
    edges_flat = [v for e in sorted(edge_set) for v in e]

    return {
        "originPos": pts["position"],
        "itemSize":  pts["ItemSize"],
        "index":     _triangulate(idx_rows, item_size),
        "edges":     edges_flat,               # flat [E*2] 原始单元边，行号索引
    }


def get_modes(model_id: str) -> List[dict]:
    """
    #2 模态阶次下拉。
    第一项固定为 Undeformed（order=0），其余按数据排列。
    返回 [{label: "Undeformed", value: 0}, {label: "EMA 1 - 12.34 Hz", value: 1}, ...]
    """
    raw    = _load_raw(model_id)
    result = [{"label": "Undeformed", "value": 0}]
    for shape in raw["modal_shape"]:
        order = int(shape["order"])
        freq  = shape.get("frequency", 0)
        unit  = shape.get("unit", "Hz")
        result.append({
            "label": f"EMA {order} - {freq} {unit}",
            "value": order,
        })
    return result


def get_components() -> List[str]:
    """
    #3 模态分量下拉（固定列表，不依赖数据）。
    """
    return ["U-Modulus:usum", "DOF UX", "DOF UY", "DOF UZ"]


def get_deformed(model_id: str, order: int,
                 max_scalar_size: float, coefficient: float) -> dict:
    """
    #4 变形振型数据。order=0 返回原始位置 + 零位移。

    返回：
      componentData  每节点 USUM 幅值 [N]
      maxValue       componentData 最大值
      minValue       componentData 最小值
      scaleFactor    变形放大系数
      newPos         变形后坐标 flat [N*3]
    """
    raw = _load_raw(model_id)
    pos = raw["points"]["position"]

    if order == 0:
        N = len(pos) // 3
        return {
            "componentData": [0.0] * N,
            "maxValue":      0.0,
            "minValue":      0.0,
            "scaleFactor":   1.0,
            "newPos":        list(pos),
        }

    mode = _get_mode(raw, order)
    real = mode["position"]["real"]
    imag = mode["position"]["imag"]

    scale                         = _scale_factor(real, imag, max_scalar_size, coefficient)
    component_data, vmin, vmax    = _component_data(real, imag, "usum")

    return {
        "componentData": component_data,
        "maxValue":      vmax,
        "minValue":      vmin,
        "scaleFactor":   scale,
        "newPos":        _new_pos(pos, real, scale),
    }


def get_animation(model_id: str, order: int) -> dict:
    """
    #5 动画数据。order=0 返回全零数组（无位移）。
    返回实部和虚部 flat 数组，前端自行做 cos(ωt)/sin(ωt) 动画。
    """
    raw = _load_raw(model_id)
    if order == 0:
        N3 = len(raw["points"]["position"])
        return {"real": [0.0] * N3, "imag": [0.0] * N3}
    mode = _get_mode(raw, order)
    return {
        "real": mode["position"]["real"],
        "imag": mode["position"]["imag"],
    }


def get_colormap(model_id: str, order: int, component: str,
                 max_scalar_size: float, coefficient: float) -> dict:
    """
    #6 云图数据。

    component 取值（前端传下拉 value 的 ':' 后面部分，或直接传内部码）：
      'usum' | 'ux' | 'uy' | 'uz'

    返回与 #4 相同结构，但 componentData 按选定分量计算。
    """
    # 将前端下拉值映射到内部分量码
    _comp_map = {
        "usum": "usum", "U-Modulus:usum": "usum",
        "ux":   "ux",   "DOF UX": "ux",
        "uy":   "uy",   "DOF UY": "uy",
        "uz":   "uz",   "DOF UZ": "uz",
    }
    comp = _comp_map.get(component, "usum")

    raw = _load_raw(model_id)
    pos = raw["points"]["position"]

    if order == 0:
        N = len(pos) // 3
        return {
            "componentData": [0.0] * N,
            "maxValue":      0.0,
            "minValue":      0.0,
            "scaleFactor":   1.0,
            "newPos":        list(pos),
        }

    mode = _get_mode(raw, order)
    real = mode["position"]["real"]
    imag = mode["position"]["imag"]

    scale                       = _scale_factor(real, imag, max_scalar_size, coefficient)
    component_data, vmin, vmax  = _component_data(real, imag, comp)

    return {
        "componentData": component_data,
        "maxValue":      vmax,
        "minValue":      vmin,
        "scaleFactor":   scale,
        "newPos":        _new_pos(pos, real, scale),
    }
