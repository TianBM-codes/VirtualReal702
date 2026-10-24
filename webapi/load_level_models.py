import math
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ImportTestLoadLevelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0, le=9223372036854775807)
    work_condition_id: Optional[str] = Field(default=None, strict=True, max_length=100)
    measurement_id: Optional[str] = Field(default=None, strict=True, max_length=100)
    file_path: str = Field(min_length=1)
    axis_label: Optional[str] = None
    unit: Optional[str] = Field(default=None, max_length=32)
    ignore_last_reading: bool = True


class SimrightSensorName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("测点名称不能为空")
        return self


class SimrightSensorBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    node_id: int = Field(ge=0)
    part_id: Optional[int] = None
    part_ori_id: Optional[Union[int, str]] = None
    quantity: Literal["scalar", "stress", "strain"] = "scalar"
    var_name: Optional[str] = None
    direction: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)
    component_vars: Optional[Dict[str, str]] = None
    shear_convention: Optional[Literal["tensor", "engineering"]] = None
    simulation_scale: float = 1.0

    @model_validator(mode="after")
    def validate_binding(self):
        self.name = self.name.strip()
        if not self.name or not math.isfinite(self.simulation_scale):
            raise ValueError("测点名称和仿真缩放系数无效")
        if self.part_ori_id is not None and self.part_id is None:
            raise ValueError("part_ori_id 必须同时提供 part_id")
        if self.quantity == "scalar":
            if not self.var_name or not self.var_name.strip():
                raise ValueError("标量查询需要 var_name")
        else:
            if self.direction is None or not all(math.isfinite(x) for x in self.direction) or math.hypot(*self.direction) == 0:
                raise ValueError("张量投影需要有限非零 direction")
            if self.quantity == "strain" and self.shear_convention is None:
                raise ValueError("应变投影需要 shear_convention")
            if self.component_vars is not None and (set(self.component_vars) != {"11", "22", "33", "12", "13", "23"}
                    or any(not value.strip() for value in self.component_vars.values())):
                raise ValueError("component_vars 必须包含六个张量分量的变量名")
        return self


class SimrightLoadLevelComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0, le=9223372036854775807)
    work_condition_id: Optional[str] = Field(default=None, strict=True, max_length=100)
    measurement_id: Optional[str] = Field(default=None, strict=True, max_length=100)
    example_mode: bool = True
    simright_base_url: Optional[HttpUrl] = None
    profile: Literal["markdown", "excel"] = "markdown"
    axis_label: Optional[str] = None
    sensors: Optional[List[SimrightSensorName]] = None
    steps: Optional[List[Union[int, str, None]]] = None
    shell_layer: str = "bottom"
    import_sha256: Optional[str] = None

    @model_validator(mode="after")
    def validate_mapping(self):
        sensors = self.sensors or []
        if len({item.name for item in sensors}) != len(sensors):
            raise ValueError("测点名称不能重复")
        return self
