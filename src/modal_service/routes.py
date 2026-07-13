"""
模态分析 JSON 接口。

接口列表
--------
POST  /api/model/testMesh/geometry
POST  /api/model/testMesh/modelSelect
POST  /api/model/testMesh/animation
POST  /api/model/testMesh/colormap
POST  /api/model/testMesh/syncAnimation
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


class ModelSelectRequest(_ProjectRequest):
    pass


class AnimationRequest(_ProjectRequest):
    order: Optional[int] = 0
    flip: bool = False

    @field_validator("order", mode="before")
    @classmethod
    def _coerce_order(cls, v):
        if v == "" or v is None:
            return 0
        return v


class GeometryRequest(AnimationRequest):
    max_scalar_size: float = 1.0
    coefficient: float = 1.0
    component: str = "usum"
    animation: bool = False


class ColormapRequest(AnimationRequest):
    component: str = "usum"
    max_scalar_size: float = 1.0
    coefficient: float = 1.0
    animation: bool = False


class SyncAnimationRequest(AnimationRequest):
    # FEM 侧定位(project 分支 BDF/OP2 workspace);step 缺省自动取第一个
    # FREQUENCY 步,fem_frame 缺省取 order-1(试验阶次与 FEM 阶次一一对应)
    step: Optional[str] = None
    fem_frame: Optional[int] = None
    result_group: Optional[str] = None
    n_frames: int = Field(default=20, ge=4, le=120)
    coefficient: float = 1.0
    component: str = "usum"
    include_frames: bool = True


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
            req.flip,
        )
    )


@router.post("/modelSelect")
async def get_modes(req: ModelSelectRequest):
    """返回模态阶次下拉列表，第一项固定为 Undeformed。"""
    return ok(service.get_modes_select(req.project_id))


@router.post("/animation")
async def get_animation(req: AnimationRequest):
    """返回实部/虚部 flat 数组，前端可据此做动画。"""
    return ok(service.get_animation(req.project_id, req.order, req.flip))


@router.post("/syncAnimation")
async def get_sync_animation(req: SyncAnimationRequest):
    """
    试验网格 / FEM 模型同屏同步动画数据。

    返回统一幅度基准后的试验侧预计算动画帧(test.frames,相位 sin(2πi/n)),
    以及 FEM 侧应使用的放大倍数(fem.scale,配合相同 n_frames 调
    GET /api/odb/{project_id}/results/modal-animation)。前端用同一个帧计数器
    驱动两边 buffer 即逐帧同步。FEM 数据不可用时 fem.available=false,
    试验侧照常返回,可用于单独显示。
    """
    return ok(
        service.get_sync_animation(
            req.project_id,
            req.order,
            step=req.step,
            fem_frame=req.fem_frame,
            result_group=req.result_group,
            n_frames=req.n_frames,
            coefficient=req.coefficient,
            component=req.component,
            flip=req.flip,
            include_frames=req.include_frames,
        )
    )


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
            req.flip,
        )
    )
