"""
模态分析 JSON 接口。

接口列表
--------
POST  /api/model/testMesh/geometry
POST  /api/model/testMesh/modelSelect
POST  /api/model/testMesh/animation
POST  /api/model/testMesh/colormap
"""

import logging

from typing import Optional, Union

from fastapi import APIRouter
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from . import service
from ..l3.api.response import ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model/testMesh", tags=["modal"])


class _ProjectRequest(BaseModel):
    # 兼容旧字段名 project，同时推荐统一使用 project_id。前端可传 int 或 str。
    model_config = ConfigDict(populate_by_name=True)
    project_id: Union[int, str] = Field(validation_alias=AliasChoices("project_id", "project"))


class GeometryRequest(_ProjectRequest):
    order: Optional[int] = 0
    max_scalar_size: float = 1.0
    coefficient: float = 1.0
    component: str = "usum"
    animation: bool = False


class ModelSelectRequest(_ProjectRequest):
    pass


class AnimationRequest(_ProjectRequest):
    order: Optional[int] = 0

    @field_validator("order", mode="before")
    @classmethod
    def _coerce_order(cls, v):
        if v == "" or v is None:
            return 0
        return v


class ColormapRequest(AnimationRequest):
    component: str = "usum"
    max_scalar_size: float = 1.0
    coefficient: float = 1.0
    animation: bool = False


@router.post("/geometry")
async def mesh_geometry(req: GeometryRequest):
    """返回节点坐标、三角化索引，以及可选的模态位移/云图数据。"""
    return ok(
        service.get_geometry(
            req.project_id,
            req.order,
            req.max_scalar_size,
            req.coefficient,
            req.component,
            req.animation,
        )
    )


@router.post("/modelSelect")
async def get_modes(req: ModelSelectRequest):
    """返回模态阶次下拉列表，第一项固定为 Undeformed。"""
    return ok(service.get_modes_select(req.project_id))


@router.post("/animation")
async def get_animation(req: AnimationRequest):
    """返回实部/虚部 flat 数组，前端可据此做动画。"""
    return ok(service.get_animation(req.project_id, req.order))


@router.post("/colormap")
async def get_colormap(req: ColormapRequest):
    """返回选定分量的云图数据。"""
    return ok(
        service.get_colormap(
            req.project_id,
            req.order,
            req.component,
            req.max_scalar_size,
            req.coefficient,
        )
    )
