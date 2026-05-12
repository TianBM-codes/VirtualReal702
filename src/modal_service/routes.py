"""
模态分析 JSON 接口（直接读 JSON 文件，数据层可换成数据库）

接口列表
--------
POST  /api/model/testMesh/geometry                  节点坐标 + 三角化索引
POST  /api/model/testMesh/modelSelect               模态阶次下拉列表
POST  /api/model/testMesh/animation                 实部/虚部（前端动画用）
POST  /api/model/testMesh/colormap                  选定分量的云图数据
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import service
from ...l3.api.response import ok, err

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model/testMesh", tags=["modal"])

STR_NO_PROJECT_ID = "项目ID缺失"
STR_ERROR_OMP = "模态分量错误"
STR_NO_PARAM = "缺少参数"
STR_ERROR = "请求错误"
STR_SUCCESS = "成功"


# ── 接口 2：几何数据 ───────────────────────────────────────────────────────

@router.post("/geometry")
async def mesh_geometry(project_id: str, order: int, max_scalar_size: float, coefficient: float, component: str, animation: bool):
    """返回节点坐标（originPos）和三角化后的索引数组（index）。"""
    return ok(service.get_geometry(project_id, order, max_scalar_size, coefficient, component, animation))


# ── 接口 3：模态阶次列表 ───────────────────────────────────────────────────

@router.post("/modelSelect")
async def get_modes(project: str):
    """返回模态阶次下拉列表：[{label, value}, ...]，第一项为 Undeformed。"""
    return ok(service.get_modes_select(project))


# ── 接口 6：动画数据 ───────────────────────────────────────────────────────

@router.post("/animation")
async def get_animation(project: str, order: int):
    """返回实部和虚部 flat 数组（各 [N*3]），前端用于 cos/sin 动画。"""
    return ok(service.get_animation(project, order))

# ── 接口 7：云图数据 ───────────────────────────────────────────────────────

@router.post("/colormap")
async def get_colormap(
    project:        str,
    order:           int,
    component:       str   = "usum",
    max_scalar_size: float,
    coefficient:     float,
    animation:       bool
):
    """
    返回选定分量的云图数据（结构与 /deformed 相同）。
    component: 'usum'|'ux'|'uy'|'uz' 或前端下拉标签 'U-Modulus:usum'|'DOF UX'|...
    """
    return ok(service.get_colormap(project, order, component, max_scalar_size, coefficient, animation))