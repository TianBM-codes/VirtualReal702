"""
模态分析 JSON 接口（直接读 JSON 文件，数据层可换成数据库）

接口列表
--------
POST /api/modal/load                                 注册 JSON 文件路径，返回 model_id
GET  /api/modal/{model_id}/geometry                  节点坐标 + 三角化索引
GET  /api/modal/{model_id}/modes                     模态阶次下拉列表
GET  /api/modal/{model_id}/components                分量下拉列表（固定）
GET  /api/modal/{model_id}/deformed                  变形振型 + USUM 云图数据
GET  /api/modal/{model_id}/animation                 实部/虚部（前端动画用）
GET  /api/modal/{model_id}/colormap                  选定分量的云图数据
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ...services import modal_service
from ..response import ok

router = APIRouter(prefix="/api/modal", tags=["modal"])


# ── 辅助 ──────────────────────────────────────────────────────────────────

def _check(model_id: str):
    if not modal_service.is_registered(model_id):
        raise HTTPException(
            status_code=404,
            detail=f"model_id '{model_id}' 未注册。请先调用 POST /api/modal/load。",
        )


# ── 接口 1：注册 JSON 文件 ─────────────────────────────────────────────────

class LoadRequest(BaseModel):
    path: str

@router.post("/load")
async def load_model(req: LoadRequest):
    """
    注册一个模态 JSON 文件路径，返回 model_id。
    重复注册同一文件不会报错。
    """
    try:
        model_id = modal_service.register(req.path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"注册失败: {e}")

    return ok({"model_id": model_id})


# ── 接口 2：几何数据 ───────────────────────────────────────────────────────

@router.get("/{model_id}/geometry")
async def get_geometry(model_id: str):
    """
    返回节点坐标（originPos）和三角化后的索引数组（index）。
    前端用这两个建 Three.js indexed BufferGeometry。
    """
    _check(model_id)
    try:
        return ok(modal_service.get_geometry(model_id))
    except Exception as e:
        logger.exception("get_geometry failed for %s", model_id)
        raise HTTPException(status_code=500, detail=str(e))


# ── 接口 3：模态阶次列表 ───────────────────────────────────────────────────

@router.get("/{model_id}/modes")
async def get_modes(model_id: str):
    """
    返回模态阶次下拉列表：[{label: "EMA 1 - 12.3 Hz", value: 1}, ...]
    """
    _check(model_id)
    try:
        return ok(modal_service.get_modes(model_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 接口 4：分量列表（固定，不依赖数据）──────────────────────────────────

@router.get("/{model_id}/components")
async def get_components(model_id: str):
    """
    返回可用分量列表：["U-Modulus:usum", "DOF UX", "DOF UY", "DOF UZ"]
    """
    _check(model_id)
    return ok(modal_service.get_components())


# ── 接口 5：变形振型 ───────────────────────────────────────────────────────

@router.get("/{model_id}/deformed")
async def get_deformed(
    model_id:        str,
    order:           int,
    max_scalar_size: float = 0.1,
    coefficient:     float = 1.0,
):
    """
    返回变形振型数据（USUM 分量）：
      componentData  每节点合位移幅值 [N]
      maxValue / minValue
      scaleFactor    变形放大系数
      newPos         变形后坐标 flat [N*3]
    """
    _check(model_id)
    try:
        return ok(modal_service.get_deformed(model_id, order, max_scalar_size, coefficient))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 接口 6：动画数据 ───────────────────────────────────────────────────────

@router.get("/{model_id}/animation")
async def get_animation(model_id: str, order: int):
    """
    返回实部和虚部 flat 数组，前端自行做 cos(ωt)/sin(ωt) 动画。
    real / imag 均为 flat [N*3]。
    """
    _check(model_id)
    try:
        return ok(modal_service.get_animation(model_id, order))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 接口 7：云图数据 ───────────────────────────────────────────────────────

@router.get("/{model_id}/colormap")
async def get_colormap(
    model_id:        str,
    order:           int,
    component:       str   = "usum",
    max_scalar_size: float = 0.1,
    coefficient:     float = 1.0,
):
    """
    返回选定分量的云图数据（结构与 /deformed 相同）。

    component 取值：'usum' | 'ux' | 'uy' | 'uz'
    或前端下拉标签：'U-Modulus:usum' | 'DOF UX' | 'DOF UY' | 'DOF UZ'
    """
    _check(model_id)
    try:
        return ok(modal_service.get_colormap(model_id, order, component, max_scalar_size, coefficient))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
