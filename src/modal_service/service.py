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
from services.model_update.analysis.project_config_service import get_test_display_scale_factors


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
            eles = []
            missing_element_nodes = set()
            flat_element_nodes = np.array(eles_fetchall).flatten().tolist()
            for raw_node_id in flat_element_nodes:
                node_idx = node2idx.get(int(raw_node_id))
                if node_idx is None:
                    missing_element_nodes.add(int(raw_node_id))
                    continue
                eles.append(node_idx)
            if missing_element_nodes:
                logger.warning(
                    "testMesh geometry skipped element node ids missing from test nodes: project_id=%s missing=%s",
                    project_id,
                    sorted(missing_element_nodes),
                )

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


def _db_modal_node_ids(project_id: str, order: Optional[int] = None) -> set:
    conn = get_connection()
    _cursor = conn.cursor()
    try:
        sql = (
            "SELECT s.modal_shape FROM t_mt_py_test_modal_shape s "
            "WHERE s.pid=%s"
        )
        params = [f"{project_id}"]
        if order is not None:
            sql += " AND s.mode_no=%s"
            params.append(int(order))
        sql += " ORDER BY s.mode_no"
        _cursor.execute(sql, tuple(params))

        node_ids = set()
        for row in _cursor.fetchall():
            modal_shape = row[0] if isinstance(row, (list, tuple)) else row
            if not modal_shape:
                continue
            loaded = json.loads(modal_shape)
            if not isinstance(loaded, dict):
                continue
            for node_id, value in loaded.items():
                if value is None:
                    continue
                try:
                    node_ids.add(int(node_id))
                except (TypeError, ValueError):
                    continue
        return node_ids
    except Exception as e:
        logger.error("数据库查询失败: %s", e)
        raise
    finally:
        _cursor.close()
        conn.close()


def _filter_node_bundle(db_node_data: dict, valid_node_ids: set) -> dict:
    node_ids = db_node_data["node_ids"]
    if len(node_ids) == 0:
        return {"node_ids": np.array([], dtype=int), "node2idx": {}, "node_coords": [], "eles": []}

    keep_ids = [int(nid) for nid in node_ids.tolist() if int(nid) in valid_node_ids]
    if not keep_ids:
        return {"node_ids": np.array([], dtype=int), "node2idx": {}, "node_coords": [], "eles": []}

    keep_set = set(keep_ids)
    old_coords = np.asarray(db_node_data["node_coords"], dtype=np.float64).reshape(-1, 3)
    old_id_to_idx = {int(nid): idx for idx, nid in enumerate(node_ids.tolist())}
    new_ids = np.asarray(keep_ids, dtype=int)
    new_node2idx = {int(nid): idx for idx, nid in enumerate(keep_ids)}
    new_coords = old_coords[[old_id_to_idx[int(nid)] for nid in keep_ids]].reshape(-1).tolist()

    new_eles = []
    old_ids = node_ids.tolist()
    for start_idx, end_idx in np.asarray(db_node_data["eles"], dtype=int).reshape(-1, 2):
        p1 = int(old_ids[int(start_idx)])
        p2 = int(old_ids[int(end_idx)])
        if p1 in keep_set and p2 in keep_set:
            new_eles.extend([new_node2idx[p1], new_node2idx[p2]])

    return {
        "node_ids": new_ids,
        "node2idx": new_node2idx,
        "node_coords": new_coords,
        "eles": new_eles,
    }


def _db_node_shapes(project_id: str, node_ids: list, order: int) -> Optional[dict]:
    conn = get_connection()
    _cursor = conn.cursor()
    try:
        display_scales = get_test_display_scale_factors(int(project_id), cursor=_cursor)
        modal_scale = float(display_scales["dynamic"])
        freq_sql = """SELECT f.mode_no, f.frequency, s.modal_shape FROM t_mt_py_test_modal_frequency f LEFT JOIN t_mt_py_test_modal_shape s ON f.pid=s.pid AND f.mode_no=s.mode_no WHERE f.pid=%s AND f.mode_no=%s ORDER BY f.mode_no"""
        _cursor.execute(freq_sql, (f"{project_id}", order,))
        modal = _cursor.fetchone()
        if modal is None or modal[2] is None:
            return None

        shape = json.loads(modal[2])
        real_modal_shape = []
        imag_modal_shape = []
        active_node_ids = []
        for n_id in node_ids:
            node_data = shape.get(str(n_id))
            if node_data:
                active_node_ids.append(int(n_id))
                real_modal_shape.extend([float(value) * modal_scale for value in node_data['real']])
                imag_modal_shape.extend([float(value) * modal_scale for value in node_data['imag']])
        return {
            "order": modal[0],
            "frequency": f"{modal[1]}",
            "unit": "Hz",
            "real": real_modal_shape,
            "imag": imag_modal_shape,
            "active_node_ids": active_node_ids,
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
            obj["show_name"] = f"Mode {obj['value']}"
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

def get_geometry(project_id: str, order: int, max_scalar_size: float, coefficient: float, component: str="usum", animation: bool=False, flip: bool=False) -> dict:
    """
    模型接口: 返回原始/变形后坐标 + 选定分量幅值 + 云图数据（如果 animation=True）。
    """
    db_node_data = _db_node_elements(project_id)
    valid_node_ids = _db_modal_node_ids(project_id, None if int(order) == 0 else int(order))
    if valid_node_ids:
        db_node_data = _filter_node_bundle(db_node_data, valid_node_ids)
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
    if flip and real:
        real = [-v for v in real]
        imag = [-v for v in imag]

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


def _bbox_min_max(pos_flat: list):
    """返回 flat 坐标数组的 (min[3], max[3]),空数组返回 (None, None)。"""
    if not pos_flat:
        return None, None
    xyz = np.reshape(np.asarray(pos_flat, dtype=np.float64), (-1, 3))
    return xyz.min(axis=0), xyz.max(axis=0)


def _fem_modal_stats(project_id, order: int, step: Optional[str],
                     frame: Optional[int], result_group: Optional[str]) -> dict:
    """
    读取 FEM 侧(project 分支,BDF/OP2 workspace)指定模态帧的统计量。

    返回 dict:
      available   bool,FEM 数据是否可用
      reason      不可用原因(可用时为 None)
      step/frame/result_group  实际使用的定位(step 缺省取第一个 FREQUENCY 步,
                               frame 缺省取 order-1,即默认试验阶次与 FEM 阶次一一对应)
      frequency   该阶 FEM 频率(Hz,取 frames.frame_value)
      bbox_min/bbox_max/max_disp  见 result_service.deform_scale_stats

    与 L3 同进程运行(app.py 同时挂载两套路由),直接复用 L3 的 registry 单例。
    独立启动 modal_service(端口 8001)时 registry 为空,自动降级为不可用。
    """
    out = {"available": False, "reason": None, "step": step, "frame": frame,
           "result_group": result_group, "frequency": None,
           "bbox_min": None, "bbox_max": None, "max_disp": 0.0}
    try:
        from src.l3.core.state import registry
        from src.l3.infra.manifest_repo import ManifestRepo
        from src.l3.services.result_service import deform_scale_stats

        idx = registry.get(str(project_id))
        if idx is None:
            out["reason"] = f"FEM workspace '{project_id}' 未加载(project 未创建或 L1 未完成)"
            return out

        manifest = ManifestRepo(idx.workspace)
        if step is None:
            freq_steps = [s for s in manifest.list_steps_by_group(result_group)
                          if (s.get("procedure") or "").upper() == "FREQUENCY"]
            if not freq_steps:
                out["reason"] = "FEM workspace 中没有 FREQUENCY(模态)步"
                return out
            step = freq_steps[0]["step_name"]
        out["step"] = step

        frames = manifest.get_frames(step, result_group)
        if frame is None:
            frame = order - 1          # 默认按阶次一一对应
        if frame < 0 or (frames and frame >= len(frames)):
            out["frame"] = frame
            out["reason"] = f"FEM 模态帧 {frame} 超出范围 [0, {len(frames)})"
            return out
        out["frame"] = frame
        if frames:
            try:
                out["frequency"] = float(frames[frame]["frame_value"])
            except (KeyError, TypeError, ValueError, IndexError):
                pass

        stats = deform_scale_stats(registry, str(project_id), step, frame, result_group)
        out.update(bbox_min=stats["bbox_min"], bbox_max=stats["bbox_max"],
                   max_disp=stats["max_disp"])
        if stats["bbox_min"] is None:
            out["reason"] = "FEM 实例包围盒缺失"
        elif stats["max_disp"] <= 0.0:
            out["reason"] = "FEM U 场缺失或位移全为 0"
        else:
            out["available"] = True
    except Exception as e:  # registry 未初始化 / workspace 数据异常等,降级不阻断试验侧
        logger.warning("FEM 模态统计读取失败: %s", e)
        out["reason"] = f"FEM 数据读取失败: {e}"
    return out


def get_sync_animation(project_id, order: int, step: Optional[str] = None,
                       fem_frame: Optional[int] = None, result_group: Optional[str] = None,
                       n_frames: int = 20, coefficient: float = 1.0,
                       component: str = "usum", flip: bool = False,
                       include_frames: bool = True) -> dict:
    """
    试验网格 / FEM 模型同屏同步动画数据。

    两个模型不同步的根源是各自归一化:试验侧按试验包围盒、FEM 侧按装配包围盒
    分别计算放大倍数,且动画相位各走各的。本接口统一两者:

    1. 统一幅度基准:参考尺寸 L = 试验包围盒与 FEM 装配包围盒并集的最大边,
       目标最大变形 target = L / 10 / coefficient(与 FEM deform-suggest-scale
       的 1/10 约定一致)。
         试验 scaleFactor = target / 试验振型最大复幅值
         FEM  scale       = target / FEM 最大位移分量
       两个模型放大后的最大变形量都恰好是 target,视觉幅度一致。

    2. 统一动画相位:test.frames 预计算 n_frames 帧变形坐标,第 i 帧相位
       θ = 2π·i/n_frames,位移 = real·sinθ + imag·cosθ(试验振型 imag≈0 时
       与 FEM /results/modal-animation 的 sinθ 完全同相)。前端用同一个帧
       计数器同时翻两边的 buffer 即严格逐帧同步。

    FEM 侧体量大,仍走既有二进制接口:前端拿本接口返回的 fem.scale 与相同的
    n_frames 调 GET /api/odb/{project_id}/results/modal-animation。
    FEM 数据不可用时自动降级(fem.available=false),试验侧照常返回,
    scale 退化为只按试验包围盒归一化——兼容单独显示试验模型的场景。
    """
    n_frames = max(4, min(int(n_frames), 120))
    if not coefficient:
        coefficient = 1.0

    db_node_data = _db_node_elements(project_id)
    valid_node_ids = _db_modal_node_ids(project_id, None if int(order) == 0 else int(order))
    if valid_node_ids:
        db_node_data = _filter_node_bundle(db_node_data, valid_node_ids)
    pos = db_node_data["node_coords"]
    ids = db_node_data["node_ids"]
    index = db_node_data["eles"]

    test_obj = {
        "ids": [], "originPos": [], "elementsIndex": [],
        "componentData": [], "maxValue": 0.0, "minValue": 0.0,
        "scaleFactor": 1.0, "frequency": None, "unit": "Hz",
        "frames": [],
    }
    sync_obj = {"n_frames": n_frames, "phase": "sin(2*pi*i/n_frames)",
                "refSize": 0.0, "targetDeform": 0.0}

    if not pos:
        fem = _fem_modal_stats(project_id, order, step, fem_frame, result_group)
        return {"test": test_obj, "fem": _fem_public(fem, 0.0), "sync": sync_obj}

    test_obj["ids"] = ids.tolist()
    test_obj["originPos"] = pos
    test_obj["elementsIndex"] = index

    fem = _fem_modal_stats(project_id, order, step, fem_frame, result_group)

    # 参考尺寸:试验包围盒 ∪ FEM 装配包围盒
    t_min, t_max = _bbox_min_max(pos)
    lo, hi = t_min, t_max
    if fem["bbox_min"] is not None:
        lo = np.minimum(lo, np.asarray(fem["bbox_min"], dtype=np.float64))
        hi = np.maximum(hi, np.asarray(fem["bbox_max"], dtype=np.float64))
    ref_size = float(np.max(hi - lo))
    target = ref_size / 10.0 / float(coefficient)
    sync_obj["refSize"] = ref_size
    sync_obj["targetDeform"] = target

    fem_scale = (target / fem["max_disp"]) if fem["available"] else 0.0

    if order == 0:
        test_obj["componentData"] = [0.0] * (len(pos) // 3)
        return {"test": test_obj, "fem": _fem_public(fem, fem_scale), "sync": sync_obj}

    db_shape = _db_node_shapes(project_id, ids, order)
    if db_shape is None or not db_shape["real"]:
        return {"test": test_obj, "fem": _fem_public(fem, fem_scale), "sync": sync_obj}

    real = np.asarray(db_shape["real"], dtype=np.float64)
    imag = np.asarray(db_shape["imag"], dtype=np.float64)
    if flip:
        real = -real
        imag = -imag
    test_obj["frequency"] = db_shape["frequency"]

    max_amp = float(np.max(np.sqrt(real * real + imag * imag)))
    scale = (target / max_amp) if max_amp > 1e-15 else 1.0
    test_obj["scaleFactor"] = scale

    comp_vals, vmin, vmax = _component_data(real.tolist(), imag.tolist(), component)
    test_obj["componentData"] = comp_vals
    test_obj["minValue"] = vmin
    test_obj["maxValue"] = vmax

    if include_frames:
        p = np.asarray(pos, dtype=np.float64)
        theta = 2.0 * np.pi * np.arange(n_frames) / n_frames
        # [n_frames, N*3] = pos + scale * (real·sinθ + imag·cosθ)
        frames = p[np.newaxis, :] + scale * (
            np.sin(theta)[:, np.newaxis] * real[np.newaxis, :]
            + np.cos(theta)[:, np.newaxis] * imag[np.newaxis, :]
        )
        test_obj["frames"] = frames.tolist()

    return {"test": test_obj, "fem": _fem_public(fem, fem_scale), "sync": sync_obj}


def _fem_public(fem: dict, fem_scale: float) -> dict:
    """内部统计 dict → 对外响应字段(不暴露 bbox/max_disp 细节)。"""
    return {
        "available": fem["available"],
        "reason": fem["reason"],
        "scale": fem_scale,
        "step": fem["step"],
        "frame": fem["frame"],
        "result_group": fem["result_group"],
        "frequency": fem["frequency"],
    }


def get_modes_select(model_id: str) -> list:
    """
    #2 模态阶次下拉。
    第一项固定为 Undeformed（order=0），其余按数据排列。
    返回 [{label: "Undeformed", value: 0}, {label: "EMA 1 - 12.34 Hz", value: 1}, ...]
    """
    return _db_frequency(model_id)


def get_animation(project_id: str, order: int, flip: bool=False) -> dict:
    """
    #5 动画数据。order=0 返回全零数组（无位移）。
    返回实部和虚部 flat 数组，前端自行做 cos(ωt)/sin(ωt) 动画。
    """
    db_node_data = _db_node_elements(project_id)
    valid_node_ids = _db_modal_node_ids(project_id, None if int(order) == 0 else int(order))
    if valid_node_ids:
        db_node_data = _filter_node_bundle(db_node_data, valid_node_ids)
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
    real = db_shape["real"]
    imag = db_shape["imag"]
    if flip and real:
        real = [-v for v in real]
        imag = [-v for v in imag]
    return {"real": real, "imag": imag}


def get_colormap(model_id: str, order: int, component: str,
                 max_scalar_size: float, coefficient: float, flip: bool=False) -> dict:
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

    return get_geometry(model_id, order, max_scalar_size, coefficient, component=comp, animation=False, flip=flip)
