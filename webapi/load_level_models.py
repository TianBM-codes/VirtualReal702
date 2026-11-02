from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ImportTestLoadLevelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0, le=9223372036854775807)
    work_condition_id: Optional[str] = Field(default=None, strict=True, max_length=100)
    measurement_id: Optional[str] = Field(default=None, strict=True, max_length=100)
    file_path: str = Field(min_length=1)
    axis_label: Optional[str] = None
    unit: Optional[str] = Field(default=None, max_length=32)
    ignore_last_reading: bool = True
