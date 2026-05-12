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
from db import get_connection, ensure_tables_exist, clear_unv_tables


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


def _db_node_elements(project_id: str) -> dict:
    conn = get_connection()
    _cursor = conn.cursor()
    try:
        node_sql = """SELECT nid, x, y, z FROM t_mt_py_test_node WHERE pid=%s ORDER BY nid"""
        elem_sql = """SELECT point1, point2 FROM t_mt_py_test_element WHERE pid=%s ORDER BY element_no"""
        _cursor.execute(node_sql, (f"{project_id}",))
        nodes = _cursor.fetchall()
        if not nodes:
            return {"node_ids": np.array([], dtype=int), "node2idx": {}, "node_coords": [], "eles": []}

        node_ids = np.array(nodes, dtype=int)[:, 0].flatten()
        node2idx = {int(nd): ii for ii, nd in enumerate(node_ids)}
        node_coords = np.array(nodes)[:, 1:].flatten().tolist()

        _cursor.execute(elem_sql, (f"{project_id}",))
        eles_fetchall = _cursor.fetchall()
        if not eles_fetchall:
            eles = []
        else:
            eles = [node2idx[ii] for ii in np.array(eles_fetchall).flatten()]

        return {
            "node_ids": node_ids,
            "node2idx": node2idx,
            "node_coords": node_coords,
            "eles": eles,
        }
    except Exception as e:
        logger.error("数据库查询失败: %s", e)
        raise
    finally:
        _cursor.close()
        conn.close()


def _db_node_shapes(project_id: str, node_ids: list, order: int) -> Optional[dict]:
    conn = get_connection()
    _cursor = conn.cursor()
    try:
        freq_sql = """SELECT f.mode_no, f.frequency, s.modal_shape FROM t_mt_py_test_modal_frequency f LEFT JOIN t_mt_py_test_modal_shape s ON f.pid=s.pid AND f.mode_no=s.mode_no WHERE f.pid=%s AND f.mode_no=%s ORDER BY f.mode_no"""
        _cursor.execute(freq_sql, (f"{project_id}", order,))
        modal = _cursor.fetchone()
        if modal is None or modal[2] is None:
            return None

        shape = json.loads(modal[2])
        real_modal_shape = []
        imag_modal_shape = []
        for n_id in node_ids:
            node_data = shape.get(str(n_id))
            if node_data:
                real_modal_shape.extend(node_data['real'])
                imag_modal_shape.extend(node_data['imag'])
        return {
            "order": modal[0],
            "frequency": f"{modal[1]}",
            "unit": "Hz",
            "real": real_modal_shape,
            "imag": imag_modal_shape,
        }
    except Exception as e:
        logger.error("数据库查询失败: %s", e)
        raise
    finally:
        _cursor.close()
        conn.close()


def _db_frequency(project_id: str) -> list:
    conn = get_connection()
    _cursor = conn.cursor()
    try:
        freq_sql = """SELECT mode_no, frequency FROM t_mt_py_test_modal_frequency WHERE pid=%s ORDER BY mode_no"""
        """读取模态阶次对应的频率数据
        """
        _cursor.execute(freq_sql, (f"{project_id}",))
        modal = _cursor.fetchall()
        modal_shape = [{"label": "Undeformed", "value": 0, "show_name": ""}]
        for ii, iter_modal in enumerate(modal):
            obj = {"label": f"EMA {iter_modal[0]} - {iter_modal[1]} Hz", "value": iter_modal[0]}
            obj["show_name"] = f"Mode {obj["value"]}"
            modal_shape.append(obj)
        return modal_shape
    except Exception as e:
        logger.error("数据库查询失败: %s", e)
        raise
    finally:
        _cursor.close()
        conn.close()


def get_box_max_scalar_size(position):
    """
    计算模型包围盒的最大尺寸
    """
    box_xyz = np.reshape(np.array(position), (-1, 3))
    box_x_max = np.max(box_xyz[:, 0])
    box_x_min = np.min(box_xyz[:, 0])
    box_y_max = np.max(box_xyz[:, 1])
    box_y_min = np.min(box_xyz[:, 1])
    box_z_max = np.max(box_xyz[:, 2])
    box_z_min = np.min(box_xyz[:, 2])
    return np.max([box_x_max - box_x_min, box_y_max - box_y_min, box_z_max - box_z_min])


# ── 对外接口（被 router 调用）────────────────────────────────────────────

def get_geometry(project_id: str, order: int, max_scalar_size: float, coefficient: float, component: str="usum", animation: bool=False) -> dict:
    """
    模型接口: 返回原始/变形后坐标 + 选定分量幅值 + 云图数据（如果 animation=True）。
    """
    db_node_data = _db_node_elements(project_id)
    pos = db_node_data["node_coords"]
    ids = db_node_data["node_ids"]
    index = db_node_data["eles"]

    if not pos:
        return {
            "ids": [], "componentData": [], "maxValue": 0.0, "minValue": 0.0,
            "scaleFactor": 1.0, "originPos": [], "newPos": [], "elementIndex": [],
            "real": [], "imag": [],
        }

    db_shape = _db_node_shapes(project_id, ids, order)
    real = db_shape["real"] if db_shape is not None else []
    imag = db_shape["imag"] if db_shape is not None else []

    max_scalar_size = get_box_max_scalar_size(pos)
    obj = {
        "ids":           ids.tolist(),
        "componentData": [],
        "maxValue":      0.0,
        "minValue":      0.0,
        "scaleFactor":   1.0,
        "originPos":     pos,
        "newPos":        [],
        "elementsIndex":  index,
        "real":          [],
        "imag":          [],
    }
    if animation:
        obj["real"] = real
        obj["imag"] = imag

    if order == 0:
        N = len(pos) // 3
        obj["componentData"] = [0.0] * N
        return obj

    if not real:
        return obj

    scale = _scale_factor(real, imag, max_scalar_size, coefficient)
    component_data, vmin, vmax = _component_data(real, imag, component)
    obj["componentData"] = component_data
    obj["maxValue"] = vmax
    obj["minValue"] = vmin
    obj["scaleFactor"] = scale
    obj["newPos"] = _new_pos(pos, real, scale)
    return obj


def get_modes_select(model_id: str) -> list:
    """
    #2 模态阶次下拉。
    第一项固定为 Undeformed（order=0），其余按数据排列。
    返回 [{label: "Undeformed", value: 0}, {label: "EMA 1 - 12.34 Hz", value: 1}, ...]
    """
    return _db_frequency(model_id)


def get_animation(project_id: str, order: int) -> dict:
    """
    #5 动画数据。order=0 返回全零数组（无位移）。
    返回实部和虚部 flat 数组，前端自行做 cos(ωt)/sin(ωt) 动画。
    """
    db_node_data = _db_node_elements(project_id)
    pos = db_node_data["node_coords"]
    ids = db_node_data["node_ids"]

    if not pos:
        return {"real": [], "imag": []}

    if order == 0:
        N3 = len(pos)
        return {"real": [0.0] * N3, "imag": [0.0] * N3}

    db_shape = _db_node_shapes(project_id, ids, order)
    if db_shape is None:
        return {"real": [], "imag": []}
    return {"real": db_shape["real"], "imag": db_shape["imag"]}


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

    return get_geometry(model_id, order, max_scalar_size, coefficient, component=comp, animation=False)
