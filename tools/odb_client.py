"""
ODB L3 Service — Python 调用封装
=================================

把所有 L3 REST 接口包成函数，固定入参和返回格式。
将来换 API（改地址、改协议）只改这一个文件，调用方不用动。

依赖：
    pip install requests numpy

用法：
    from tools.odb_client import ODBClient

    c = ODBClient("http://localhost:18765")

    # 提交 ODB 文件
    job = c.submit_job("/data/raw/car.odb", display_name="车身")
    odb_id = job["odb_id"]

    # 等处理完再读数据
    while c.get_job(odb_id)["status"] != "ready":
        time.sleep(5)

    # 读几何
    geo = c.get_render_buffers(odb_id, "PART-1-1")
    print(geo["positions"].shape)   # [Rf*3, 3] float32
"""

import json
import struct
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests

# ── L3BE 解码器 ────────────────────────────────────────────────────────────────

_DTYPE_MAP = {
    1: np.int8, 2: np.uint8, 3: np.int16, 4: np.uint16,
    5: np.int32, 6: np.uint32, 7: np.int64, 8: np.uint64,
    9: np.float32, 10: np.float64,
}


def decode_l3be(data: bytes) -> Dict[str, np.ndarray]:
    """
    解码 L3BE 二进制包，返回 {section名: np.ndarray}。

    L3BE 是 L3 服务所有几何/结果二进制接口的统一格式，
    包含若干命名 numpy 数组（坐标、颜色、标签等）。
    """
    magic, version, _flags, _hdr_size, n_sections, tbl_off, pay_off = \
        struct.unpack_from("<4sHHIIII", data, 0)
    if magic != b"L3BE":
        raise ValueError(f"不是 L3BE 格式（magic={magic!r}）")

    result: Dict[str, np.ndarray] = {}
    for i in range(n_sections):
        entry_off = tbl_off + i * 80
        name_b, dtype_code, ndim, s0, s1, s2, s3, offset, nbytes, _flags2 = \
            struct.unpack_from("<32sHH4IQQI", data, entry_off)
        name = name_b.rstrip(b"\x00").decode("ascii")
        shape = [s0, s1, s2, s3][:ndim]
        dtype = _DTYPE_MAP[dtype_code]
        arr = np.frombuffer(
            data, dtype=dtype,
            count=nbytes // np.dtype(dtype).itemsize,
            offset=offset,
        ).reshape(shape).copy()
        result[name] = arr
    return result


# ── 异常 ───────────────────────────────────────────────────────────────────────

class ODBClientError(Exception):
    """API 返回错误或网络异常时抛出。"""
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


# ── 客户端 ─────────────────────────────────────────────────────────────────────

class ODBClient:
    """
    L3 服务 Python 客户端。

    所有方法按接口文档的分组组织：
        作业管理        submit_job / get_job / list_jobs / delete_job / retry_job
        模型结构        get_overview
        几何数据        get_render_buffers / get_feature_edges / get_element_mesh_edges
        结果数据        get_frame_colors / get_section_mesh / get_raw_values
        节点字段表      get_fields / get_node_table
        自定义场        post_user_field / list_user_fields /
                        get_user_field_colors / delete_user_field
        查询接口        pick / bbox_query / query_render_faces /
                        nearest_face / surface_patch
        属性上色        get_color_schemes / get_color_code
        模态分析        modal_load / modal_geometry / modal_modes /
                        modal_components / modal_deformed /
                        modal_animation / modal_colormap
        Simright 兼容   simright_query
        健康检查        health_live / health_ready
    """

    def __init__(self, base_url: str = "http://localhost:18765", timeout: int = 60):
        """
        base_url : 服务地址，末尾不要带斜杠
        timeout  : HTTP 请求超时秒数（对大文件可适当调大）
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # ── 内部工具 ───────────────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return self.base_url + path

    def _check(self, resp: requests.Response) -> requests.Response:
        if not resp.ok:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ODBClientError(resp.status_code, detail)
        return resp

    def _get_json(self, path: str, **params) -> Any:
        resp = self._check(self._session.get(
            self._url(path), params=params, timeout=self.timeout
        ))
        return resp.json()

    def _post_json(self, path: str, body: dict) -> Any:
        resp = self._check(self._session.post(
            self._url(path), json=body, timeout=self.timeout
        ))
        return resp.json()

    def _delete_json(self, path: str, **params) -> Any:
        resp = self._check(self._session.delete(
            self._url(path), params=params, timeout=self.timeout
        ))
        return resp.json()

    def _get_binary(self, path: str, **params) -> Tuple[bytes, Dict[str, str]]:
        """返回 (response_body_bytes, response_headers_dict)"""
        resp = self._check(self._session.get(
            self._url(path), params=params, timeout=self.timeout
        ))
        return resp.content, dict(resp.headers)

    # ═══════════════════════════════════════════════════════════════════════════
    # 一、作业管理
    # ═══════════════════════════════════════════════════════════════════════════

    def submit_job(self, odb_path: str, display_name: str = "") -> Dict:
        """
        提交一个 ODB 或 INP 文件，启动 L1+L2 处理流程。

        参数
            odb_path     : 服务器本地文件路径（绝对路径）
            display_name : 可选展示名，默认用文件名

        返回
            {
              "odb_id":       "a3f7c2d1-...",
              "display_name": "车身模型",
              "status":       "submitted"
            }
        """
        body: Dict[str, Any] = {"odb_path": odb_path}
        if display_name:
            body["display_name"] = display_name
        return self._post_json("/api/jobs", body)

    def get_job(self, odb_id: str) -> Dict:
        """
        查询作业状态。

        返回
            {
              "odb_id":          "...",
              "status":          "ready",          # submitted/l1_running/l1_done/l2_running/ready/error
              "is_render_ready": True,
              "node_count":      284000,
              "instance_count":  3,
              "created_at":      "2026-04-08T10:00:00Z",
              "l1_done_at":      "...",
              "l2_done_at":      "...",
              "error_msg":       null
            }

        is_render_ready=True 后才能调用几何/结果接口。
        """
        return self._get_json(f"/api/jobs/{odb_id}")

    def list_jobs(self) -> List[Dict]:
        """返回所有作业列表，每项结构同 get_job()。"""
        return self._get_json("/api/jobs")

    def delete_job(self, odb_id: str, hard: bool = False) -> Dict:
        """
        删除作业。

        hard=False（默认）：仅从数据库删除，保留磁盘文件。
        hard=True         ：同时删除磁盘上的 workspace 目录。

        运行中的作业（l1_running/l2_running）无法删除，会抛出 ODBClientError(409)。
        """
        params = {"hard": "true"} if hard else {}
        resp = self._check(self._session.delete(
            self._url(f"/api/jobs/{odb_id}"), params=params, timeout=self.timeout
        ))
        return resp.json()

    def retry_job(self, odb_id: str) -> Dict:
        """
        重试失败的作业（仅 status=='error' 时可用）。

        返回新的作业状态字典。
        """
        return self._post_json(f"/api/jobs/{odb_id}/retry", {})

    # ═══════════════════════════════════════════════════════════════════════════
    # 二、模型结构信息
    # ═══════════════════════════════════════════════════════════════════════════

    def get_overview(self, odb_id: str) -> Dict:
        """
        获取模型总览（实例列表、步骤列表、结果场列表）。

        返回
            {
              "instances":        ["PART-1-1", "PART-2-1"],
              "steps":            ["Step-1", "Step-2"],
              "fields":           ["U", "S", "RF"],
              "default_step":     "Step-1",
              "default_frame_idx": 0,
              "default_field":    "U"
            }
        """
        resp = self._get_json(f"/api/odb/{odb_id}/meta/overview")
        return resp.get("data", resp)

    # ═══════════════════════════════════════════════════════════════════════════
    # 三、几何数据（返回 numpy 数组）
    # ═══════════════════════════════════════════════════════════════════════════

    def get_render_buffers(self, odb_id: str, instance: str) -> Dict:
        """
        获取表面三角网格（Triangle Soup），用于渲染。

        返回
            {
              "positions":  np.ndarray [Rf*3, 3] float32,  # 三角面顶点 XYZ
              "normals":    np.ndarray [Rf*3, 3] float32,  # 法向量（可能为 None）
              "face_count": int                            # 三角面数量 Rf
            }

        每个三角面有 3 个顶点，positions[i*3:(i+1)*3] 是第 i 个面的 3 个顶点。
        """
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/geometry/{instance}/render-buffers"
        )
        sections = decode_l3be(raw)
        return {
            "positions":  sections["positions"],
            "normals":    sections.get("normals"),
            "face_count": int(headers.get("X-Face-Count", len(sections["positions"]) // 3)),
        }

    def get_feature_edges(self, odb_id: str, instance: str) -> Dict:
        """
        获取特征边（外轮廓线 + ≥30° 折痕），用于线框显示。

        返回
            {
              "edge_positions": np.ndarray [E*2, 3] float32,  # 每两行为一条边的两个端点
              "edge_count":     int
            }
        """
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/geometry/{instance}/feature-edges"
        )
        sections = decode_l3be(raw)
        return {
            "edge_positions": sections["edge_positions"],
            "edge_count":     int(headers.get("X-Edge-Count", len(sections["edge_positions"]) // 2)),
        }

    def get_element_mesh_edges(self, odb_id: str, instance: str) -> Dict:
        """
        获取所有单元边界线（比 feature_edges 密，包含内部网格线）。

        返回结构同 get_feature_edges()。
        """
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/geometry/{instance}/element-mesh-edges"
        )
        sections = decode_l3be(raw)
        return {
            "edge_positions": sections["edge_positions"],
            "edge_count":     int(headers.get("X-Edge-Count", len(sections["edge_positions"]) // 2)),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 四、结果数据
    # ═══════════════════════════════════════════════════════════════════════════

    def get_frame_colors(
        self,
        odb_id: str,
        instance: str,
        step: str,
        field: str,
        frame: int = 0,
        component: str = "USUM",
        mode: str = "smooth",
    ) -> Dict:
        """
        获取某帧结果的逐顶点 RGBA 颜色（用于云图渲染）。

        参数
            component : 分量名，如 U1/U2/U3/USUM（位移），S11/S22/MISES（应力）等
            mode      : "smooth"（节点平均，过渡平滑）/ "flat"（单元均值，阶梯状）

        返回
            {
              "color_per_vertex": np.ndarray [Rf*3, 4] uint8,  # RGBA 每顶点颜色
              "legend_range":     np.ndarray [2] float32,      # [最小值, 最大值]
              "val_min":          float,
              "val_max":          float,
              "component":        str,
              "frame":            int
            }
        """
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/results/frame-colors",
            instance=instance, step=step, field=field,
            frame=frame, component=component, mode=mode,
        )
        sections = decode_l3be(raw)
        return {
            "color_per_vertex": sections["color_per_vertex"],
            "legend_range":     sections["legend_range"],
            "val_min":          float(headers.get("X-Val-Min", sections["legend_range"][0])),
            "val_max":          float(headers.get("X-Val-Max", sections["legend_range"][1])),
            "component":        headers.get("X-Component", component),
            "frame":            int(headers.get("X-Frame", frame)),
        }

    def get_section_mesh(
        self,
        odb_id: str,
        instance: str,
        axis: str = "Z",
        position: float = 0.0,
    ) -> Dict:
        """
        获取剖切截面的三角网格和轮廓线。

        参数
            axis     : 切割平面法向，"X" / "Y" / "Z"
            position : 切割平面在该轴上的坐标（模型坐标系）

        返回
            {
              "vertices":   np.ndarray [T*3, 3] float32,  # 截面三角形顶点
              "edge_verts": np.ndarray [E*2, 3] float32,  # 截面轮廓线段端点
              "tri_count":  int,
              "edge_count": int,
              "axis":       str,
              "position":   float
            }
        """
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/results/section-mesh",
            instance=instance, axis=axis, position=position,
        )
        sections = decode_l3be(raw)
        return {
            "vertices":   sections["vertices"],
            "edge_verts": sections["edge_verts"],
            "tri_count":  int(headers.get("X-Tri-Count", len(sections["vertices"]) // 3)),
            "edge_count": int(headers.get("X-Edge-Count", len(sections["edge_verts"]) // 2)),
            "axis":       headers.get("X-Axis", axis),
            "position":   float(headers.get("X-Position", position)),
        }

    def get_raw_values(
        self,
        odb_id: str,
        instance: str,
        step: str,
        field: str,
        position: str,
        frame: int = 0,
    ) -> Dict:
        """
        获取原始数值结果（不上色），面向计算后处理。

        参数
            position : "NODAL" / "ELEMENT_NODAL" / "INTEGRATION_POINT"

        返回（NODAL 时）
            {
              "position":     "NODAL",
              "components":   ["U1", "U2", "U3"],
              "node_labels":  np.ndarray [N] int32,
              "values":       np.ndarray [N, ncomp] float32
            }

        返回（ELEMENT_NODAL / INTEGRATION_POINT 时）
            {
              "position":    "INTEGRATION_POINT",
              "components":  ["S11", "S22", ...],
              "etype_groups": ["C3D8R", ...],
              "groups": {
                "C3D8R": {
                  "elem_labels": np.ndarray [M] int32,
                  "values":      np.ndarray [M, n_ip, ncomp] float32
                },
                ...
              }
            }
        """
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/results/raw-values",
            instance=instance, step=step, field=field,
            frame=frame, position=position,
        )
        sections = decode_l3be(raw)
        components = json.loads(headers.get("X-Components", "[]"))
        pos = headers.get("X-Position", position)

        if pos == "NODAL":
            return {
                "position":    pos,
                "components":  components,
                "node_labels": sections["node_labels"],
                "values":      sections["values"],
            }

        etype_groups = json.loads(headers.get("X-Etype-Groups", "[]"))
        groups = {}
        for etype in etype_groups:
            safe = etype.replace("-", "_").replace(" ", "_")
            groups[etype] = {
                "elem_labels": sections[f"el_{safe}"],
                "values":      sections[f"v_{safe}"],
            }
        return {
            "position":     pos,
            "components":   components,
            "etype_groups": etype_groups,
            "groups":       groups,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 五、节点字段表
    # ═══════════════════════════════════════════════════════════════════════════

    def get_fields(self, odb_id: str, instance: str, step: str) -> List[Dict]:
        """
        查询某实例+步骤下可用的结果场及分量列表。

        返回
            [
              {"field": "U",  "positions": ["NODAL"],               "components": ["U1","U2","U3","USUM"]},
              {"field": "S",  "positions": ["INTEGRATION_POINT"],   "components": ["S11","MISES",...]},
              ...
            ]
        """
        resp = self._get_json(
            f"/api/odb/{odb_id}/fields",
            instance=instance, step=step,
        )
        return resp.get("fields", resp)

    def get_node_table(
        self,
        odb_id: str,
        instance: str,
        step: str,
        frame_idx: int,
        node_labels: List[int],
        items: List[Dict[str, str]],
    ) -> Dict:
        """
        批量查询指定节点的多列结果值，返回 [N × M] 矩阵。

        参数
            node_labels : ODB 节点号列表
            items       : 列定义列表，每项 {"field": "U", "component": "USUM"}

        返回
            {
              "node_labels": np.ndarray [N] int32,
              "values":      np.ndarray [N, M] float32,  # NaN = 节点不在该实例
              "columns":     [{"field": "U", "component": "USUM"}, ...],
              "node_count":  N,
              "col_count":   M
            }

        示例：
            table = c.get_node_table(
                odb_id, "PART-1-1", "Step-1", frame_idx=5,
                node_labels=[101, 102, 103],
                items=[{"field": "U", "component": "USUM"},
                       {"field": "S", "component": "MISES"}],
            )
            print(table["values"])   # shape [3, 2]
        """
        resp = self._check(self._session.post(
            self._url(f"/api/odb/{odb_id}/results/node-table"),
            json={
                "instance":    instance,
                "step":        step,
                "frame_idx":   frame_idx,
                "node_labels": node_labels,
                "items":       items,
            },
            timeout=self.timeout,
        ))
        raw = resp.content
        headers = dict(resp.headers)
        sections = decode_l3be(raw)
        return {
            "node_labels": sections["node_labels"],
            "values":      sections["values"],
            "columns":     json.loads(headers.get("X-Columns", "[]")),
            "node_count":  int(headers.get("X-Node-Count", len(node_labels))),
            "col_count":   int(headers.get("X-Col-Count", len(items))),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 六、自定义结果场（user-field）
    # ═══════════════════════════════════════════════════════════════════════════

    def post_user_field(
        self,
        odb_id: str,
        name: str,
        instance: str,
        value: float,
        element_labels: List[int],
    ) -> Dict:
        """
        上传自定义标量场（如疲劳损伤、安全系数）。

        对同一 (name, instance) 重复调用会覆盖之前的值。

        参数
            name           : 字段名，如 "fatigue_damage"
            value          : 标量值（所有指定单元共用同一个值）
            element_labels : 应用该值的 ODB 单元号列表

        返回
            {"name": "...", "instance": "...", "elem_count": 4}
        """
        return self._post_json(
            f"/api/odb/{odb_id}/results/user-field",
            {"name": name, "instance": instance,
             "value": value, "element_labels": element_labels},
        )

    def list_user_fields(self, odb_id: str, instance: Optional[str] = None) -> List[Dict]:
        """
        列出已上传的自定义场。

        返回
            [{"name": "fatigue_damage", "instance": "PART-1-1", "elem_count": 4, "value": 0.85}, ...]
        """
        params: Dict[str, Any] = {}
        if instance:
            params["instance"] = instance
        resp = self._get_json(f"/api/odb/{odb_id}/results/user-fields", **params)
        return resp.get("fields", resp)

    def get_user_field_colors(
        self,
        odb_id: str,
        name: str,
        instance: str,
        val_min: Optional[float] = None,
        val_max: Optional[float] = None,
    ) -> Dict:
        """
        把自定义场渲染为云图颜色。集合内单元按 jet 色阶上色，集合外为灰色。

        参数
            val_min / val_max : 颜色映射范围，省略则自动使用实际值范围

        返回
            {
              "color_per_vertex": np.ndarray [Rf*3, 4] uint8,
              "legend_range":     np.ndarray [2] float32,
              "val_min": float,
              "val_max": float,
              "field_name": str
            }
        """
        params: Dict[str, Any] = {"name": name, "instance": instance}
        if val_min is not None:
            params["val_min"] = val_min
        if val_max is not None:
            params["val_max"] = val_max

        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/results/user-field-colors", **params
        )
        sections = decode_l3be(raw)
        return {
            "color_per_vertex": sections["color_per_vertex"],
            "legend_range":     sections["legend_range"],
            "val_min":          float(headers.get("X-Val-Min", sections["legend_range"][0])),
            "val_max":          float(headers.get("X-Val-Max", sections["legend_range"][1])),
            "field_name":       headers.get("X-Field-Name", name),
        }

    def delete_user_field(self, odb_id: str, name: str, instance: str) -> Dict:
        """
        删除一个自定义场。不存在时抛出 ODBClientError(404)。

        返回
            {"deleted": true, "name": "...", "instance": "..."}
        """
        return self._delete_json(
            f"/api/odb/{odb_id}/results/user-field",
            name=name, instance=instance,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 七、查询接口
    # ═══════════════════════════════════════════════════════════════════════════

    def pick(
        self,
        odb_id: str,
        instance: str,
        render_face_idx: int,
        pick_mode: str = "element",
        node_idx: Optional[int] = None,
        step: Optional[str] = None,
        field: Optional[str] = None,
        frame_idx: Optional[int] = None,
        component: Optional[str] = None,
        component_idx: Optional[int] = None,
        include_coords: bool = False,
        deform_scale: float = 1.0,
    ) -> Dict:
        """
        点选查询：给定三角面片索引，返回对应的单元/节点信息及结果值。

        参数
            render_face_idx : 三角面全局索引（从几何接口对应）
            pick_mode       : "element"（查单元）/ "node"（查节点）
            node_idx        : node 模式时指定最近顶点 0/1/2
            step/field/frame_idx/component : 不传则不查结果值
            component_idx   : 显式指定分量列索引（S11→0, S22→1...），优先于 component 名
            include_coords  : node 模式时是否返回节点坐标
            deform_scale    : 变形放大系数

        返回（element 模式）
            {
              "pick_mode": "element",
              "instance":  "PART-1-1",
              "render_face_idx": 1234,
              "render_face_indices": [1234, 1235, ...],
              "odb": {"elem_label": 5678, "elem_type": "C3D8R", "elem_node_labels": [...]},
              "result": {"field": "U", "component": "USUM", "display_value": 0.0124},
              "mises": 125.4
            }
        """
        params: Dict[str, Any] = {
            "instance":       instance,
            "render_face_idx": render_face_idx,
            "pick_mode":      pick_mode,
            "include_coords": include_coords,
            "deform_scale":   deform_scale,
        }
        if node_idx is not None:
            params["node_idx"] = node_idx
        if step is not None:
            params["step"] = step
        if field is not None:
            params["field"] = field
        if frame_idx is not None:
            params["frame_idx"] = frame_idx
        if component is not None:
            params["component"] = component
        if component_idx is not None:
            params["component_idx"] = component_idx
        return self._get_json(f"/api/odb/{odb_id}/query/pick", **params)

    def bbox_query(
        self,
        odb_id: str,
        instance: str,
        bbox_min: List[float],
        bbox_max: List[float],
        mode: str = "intersect",
        set_name: Optional[str] = None,
    ) -> Dict:
        """
        包围盒查询：返回与给定 3D 矩形区域相交（或完全包含）的单元集合。

        参数
            bbox_min  : [x, y, z] 包围盒最小角（世界坐标）
            bbox_max  : [x, y, z] 包围盒最大角
            mode      : "intersect"（有顶点在盒内即命中）/ "contained"（全部顶点在盒内）
            set_name  : 传了则将结果持久化为命名集合

        返回
            {
              "set_name":          "MyRegion",  # 或 null
              "elem_count":        142,
              "render_face_count": 388,
              "elem_labels":       [5001, ...]   # elem_count>2000 时为 null
            }
        """
        body: Dict[str, Any] = {
            "instance": instance,
            "bbox_min": bbox_min,
            "bbox_max": bbox_max,
            "mode":     mode,
        }
        if set_name:
            body["set_name"] = set_name
        return self._post_json(f"/api/odb/{odb_id}/query/bbox", body)

    def query_render_faces(
        self,
        odb_id: str,
        instance: str,
        render_face_indices: List[int],
        mode: str = "element",
    ) -> Dict:
        """
        批量面片解析：给定一批三角面索引，查出它们属于哪些单元/节点。

        参数
            mode : "element"（返回单元信息）/ "node"（返回节点信息）

        返回（element 模式）
            {
              "mode":              "element",
              "elem_count":        3,
              "elem_labels":       [5001, 5002, 5010],
              "elem_face_indices": [100, 101, ...],  # 扩展到命中单元的全部面
              "elem_ids_per_face": [0, 0, 1, ...]    # 每个面属于第几个单元
            }

        返回（node 模式）
            {
              "mode":           "node",
              "node_count":     12,
              "node_labels":    [101, 102, ...],
              "node_positions": [[10.5, 20.3, 0.0], ...]  # >5000 时为 null
            }
        """
        return self._post_json(
            f"/api/odb/{odb_id}/query/render-faces",
            {"instance": instance, "render_face_indices": render_face_indices, "mode": mode},
        )

    def nearest_face(
        self,
        odb_id: str,
        instance: str,
        x: float,
        y: float,
        z: float,
    ) -> Dict:
        """
        最近面查询：给定空间中任意一点（不必在模型表面），找到最近的三角面。

        常与 surface_patch() 配合使用：先找最近面得到法线，再用法线做矩形选取。

        返回
            {
              "instance":      "PART-1-1",
              "render_face_idx": 142,
              "elem_label":    1023,
              "elem_type":     "C3D8R",
              "normal":        [0.0, 0.0, 1.0],
              "closest_point": [0.0, 0.0, 0.2],
              "distance":      0.05
            }
        """
        return self._get_json(
            f"/api/odb/{odb_id}/query/nearest-face",
            instance=instance, x=x, y=y, z=z,
        )

    def surface_patch(
        self,
        odb_id: str,
        instance: str,
        center: List[float],
        normal: List[float],
        width: float,
        height: float,
        up_hint: Optional[List[float]] = None,
    ) -> Dict:
        """
        法向矩形选取：以 center 为中心，在 normal 定义的平面上铺一个 width×height 矩形，
        选取所有被覆盖的面/单元/节点。

        典型工作流：
            nf = c.nearest_face(odb_id, "PART-1-1", x=0, y=0, z=0.15)
            patch = c.surface_patch(odb_id, "PART-1-1",
                center=nf["closest_point"], normal=nf["normal"],
                width=0.05, height=0.03)

        参数
            up_hint : 定义矩形"高度"方向，默认全局 Y 轴 [0, 1, 0]

        返回
            {
              "face_count":          45,
              "elem_count":          23,
              "node_count":          67,
              "render_face_indices": [100, 101, ...],
              "elem_labels":         [1001, ...],     # >2000 时为 null
              "node_labels":         [201, ...],      # >2000 时为 null
              "node_positions":      [[0.01, 0.0, 0.2], ...]  # >5000 时为 null
            }
        """
        body: Dict[str, Any] = {
            "instance": instance,
            "center":   center,
            "normal":   normal,
            "width":    width,
            "height":   height,
        }
        if up_hint is not None:
            body["up_hint"] = up_hint
        return self._post_json(f"/api/odb/{odb_id}/query/surface-patch", body)

    # ═══════════════════════════════════════════════════════════════════════════
    # 八、属性上色
    # ═══════════════════════════════════════════════════════════════════════════

    def get_color_schemes(self, odb_id: str, instance: str) -> Dict:
        """
        查询可用的属性上色方案。

        返回
            {
              "schemes": ["etype", "material", "section_type", "elset"],
              "elsets":  ["Set-1", "Set-2", "Nozzle"]
            }
        """
        return self._get_json(f"/api/odb/{odb_id}/color-code/{instance}/schemes")

    def get_color_code(
        self,
        odb_id: str,
        instance: str,
        scheme: str,
        set_names: Optional[List[str]] = None,
    ) -> Dict:
        """
        获取属性上色的逐顶点 RGB 颜色。

        参数
            scheme    : "etype"（单元类型）/ "material"（材料）/
                        "section_type"（截面类型）/ "elset"（单元集合）
            set_names : 仅 scheme="elset" 时有效，指定要高亮的集合名列表

        返回
            {
              "color_per_vertex": np.ndarray [Rf*3, 3] float32,  # RGB 0.0~1.0
              "face_count":       int,
              "legend":           [{"id": 0, "name": "C3D8R", "r": 0.8, "g": 0.2, "b": 0.1}, ...]
            }
        """
        params: Dict[str, Any] = {"scheme": scheme}
        if set_names:
            params["set_names"] = ",".join(set_names)
        raw, headers = self._get_binary(
            f"/api/odb/{odb_id}/color-code/{instance}", **params
        )
        sections = decode_l3be(raw)
        try:
            legend = json.loads(headers.get("X-Color-Legend", "[]"))
        except Exception:
            legend = []
        return {
            "color_per_vertex": sections["color_per_vertex"],
            "face_count":       int(headers.get("X-Face-Count", len(sections["color_per_vertex"]) // 3)),
            "legend":           legend,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 九、模态分析
    # ═══════════════════════════════════════════════════════════════════════════

    def modal_load(self, path: str) -> str:
        """
        注册模态分析 JSON 文件，返回 model_id。

        path     : 服务器本地文件路径（绝对路径）
        model_id : 由文件名（不含扩展名）派生，后续模态接口均用此 ID
        """
        resp = self._post_json("/api/modal/load", {"path": path})
        return resp["model_id"]

    def modal_geometry(self, model_id: str) -> Dict:
        """
        获取模态模型的节点坐标和三角化索引。

        返回
            {
              "origin_pos": list[float],  # flat [N*3]，节点坐标
              "index":      list[int]     # 三角化索引，可直接给 Three.js indexed BufferGeometry
            }
        """
        resp = self._get_json(f"/api/modal/{model_id}/geometry")
        data = resp.get("data", resp)
        return {"origin_pos": data["originPos"], "index": data["index"]}

    def modal_modes(self, model_id: str) -> List[Dict]:
        """
        获取模态阶次下拉列表。

        返回
            [{"label": "EMA 1 - 12.3 Hz", "value": 1}, ...]
        """
        resp = self._get_json(f"/api/modal/{model_id}/modes")
        return resp.get("data", resp)

    def modal_components(self, model_id: str) -> List[str]:
        """
        获取可用分量列表。

        返回
            ["U-Modulus:usum", "DOF UX", "DOF UY", "DOF UZ"]
        """
        resp = self._get_json(f"/api/modal/{model_id}/components")
        return resp.get("data", resp)

    def modal_deformed(
        self,
        model_id: str,
        order: int,
        max_scalar_size: float = 0.1,
        coefficient: float = 1.0,
    ) -> Dict:
        """
        获取变形振型数据（USUM 合位移）。

        参数
            order          : 模态阶次（从 modal_modes() 获取）
            max_scalar_size: 最大位移相对模型包围盒的比例，默认 0.1（10%）
            coefficient    : 额外放大系数

        返回
            {
              "component_data": list[float],  # 每节点合位移幅值 [N]
              "max_value":      float,
              "min_value":      float,
              "scale_factor":   float,        # 实际变形放大系数
              "new_pos":        list[float]   # 变形后节点坐标 flat [N*3]
            }
        """
        resp = self._get_json(
            f"/api/modal/{model_id}/deformed",
            order=order, max_scalar_size=max_scalar_size, coefficient=coefficient,
        )
        data = resp.get("data", resp)
        return {
            "component_data": data["componentData"],
            "max_value":      data["maxValue"],
            "min_value":      data["minValue"],
            "scale_factor":   data["scaleFactor"],
            "new_pos":        data["newPos"],
        }

    def modal_animation(self, model_id: str, order: int) -> Dict:
        """
        获取模态动画数据（实部 + 虚部）。

        前端动画计算：pos(t) = origin_pos + real*cos(ωt) + imag*sin(ωt)

        返回
            {
              "real": list[float],  # 实部位移 flat [N*3]
              "imag": list[float]   # 虚部位移 flat [N*3]
            }
        """
        resp = self._get_json(f"/api/modal/{model_id}/animation", order=order)
        return resp.get("data", resp)

    def modal_colormap(
        self,
        model_id: str,
        order: int,
        component: str = "usum",
        max_scalar_size: float = 0.1,
        coefficient: float = 1.0,
    ) -> Dict:
        """
        获取指定分量的云图数据。

        参数
            component : "usum" / "ux" / "uy" / "uz"

        返回结构同 modal_deformed()。
        """
        resp = self._get_json(
            f"/api/modal/{model_id}/colormap",
            order=order, component=component,
            max_scalar_size=max_scalar_size, coefficient=coefficient,
        )
        data = resp.get("data", resp)
        return {
            "component_data": data["componentData"],
            "max_value":      data["maxValue"],
            "min_value":      data["minValue"],
            "scale_factor":   data["scaleFactor"],
            "new_pos":        data["newPos"],
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 十、Simright 兼容层
    # ═══════════════════════════════════════════════════════════════════════════

    def simright_query(self, name: str, args: Dict[str, Any]) -> Any:
        """
        调用 Simright 3DLite DATA-API 兼容接口。

        参数
            name : 操作名，支持：
                   "loadcases" / "variables" / "assemble" / "extremeValue" /
                   "nodeInfo" / "elementInfo" / "XYCurveData1" / "freqValue"
            args : 操作参数，内容因 name 而异（包含 odb_id 等）

        成功返回 data 字段内容；失败抛出 ODBClientError。
        """
        resp = self._check(self._session.post(
            self._url("/applications/3dlite/api/v1/query"),
            json={"name": name, "args": args},
            timeout=self.timeout,
        )).json()
        if resp.get("code", 0) != 0:
            raise ODBClientError(resp["code"], resp.get("message", "simright error"))
        return resp.get("data")

    # ═══════════════════════════════════════════════════════════════════════════
    # 十一、健康检查
    # ═══════════════════════════════════════════════════════════════════════════

    def health_live(self) -> bool:
        """进程存活检查，始终返回 True（网络不通则抛出异常）。"""
        self._check(self._session.get(
            self._url("/api/health/live"), timeout=5
        ))
        return True

    def health_ready(self) -> bool:
        """
        服务就绪检查（数据库可用则 True）。

        可用于等待服务启动完成：
            while not c.health_ready():
                time.sleep(1)
        """
        try:
            self._check(self._session.get(
                self._url("/api/health/ready"), timeout=5
            ))
            return True
        except ODBClientError:
            return False
