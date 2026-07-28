"""
Overview metadata service.

在 manifest 的原始 overview（instances / steps / fields）之上补出"时间范围"，
让调用方在填 node-time-value 的 `time` 之前就知道合法区间，不用先试错一次
再看报错里的范围。

时间范围的口径与 node_time_value_service 完全一致——两边共用同一个
`_build_timeline`，避免两处各算一遍导致对不上。

补出来的字段
------------
顶层：
  global_time_range  {"min", "max", "steps": [...]} | null
      不传 step（全局时间口径）时 `time` 的合法区间。只串联有时间语义的
      STATIC/DYNAMIC step；没有这类 step 时为 null（表示该结果组不能按
      全局时间查，必须显式传 step）。

每个 step：
  time_axis          "time" | "mode"
      该 step 的 frame_value 是什么语义：STATIC/DYNAMIC 是时间；
      FREQUENCY/BUCKLE 是模态阶次/频率/特征值（不是时间，不能插值）。
  time_range         {"min", "max"} | null
      传了该 step 时 `time` 的合法区间（= 该 step 的 frame_value 范围）。
      time_axis="mode" 时这里是阶次/频率的范围。
  global_time_range  {"min", "max"} | null
      该 step 在全局时间轴上占的区间；time_axis="mode" 的 step 不参与
      全局时间轴，为 null。
"""
from typing import Optional

from ..core.errors import AppError
from ..infra.manifest_repo import ManifestRepo
from .node_time_value_service import _TIME_PROCEDURES, _build_timeline


def _range(values) -> Optional[dict]:
    return {"min": min(values), "max": max(values)} if values else None


def get_overview(repo: ManifestRepo, result_group: Optional[str] = None) -> dict:
    """repo.get_overview() 的结果 + 时间范围标注（见模块 docstring）。"""
    data = repo.get_overview(result_group=result_group)

    # 全局时间轴：没有 STATIC/DYNAMIC step 时 _build_timeline 会抛错，
    # 这只说明"不能按全局时间查"，不是错误——overview 照常返回，标 null
    global_by_step = {}
    try:
        points, _mode = _build_timeline(repo, None, result_group)
    except AppError:
        points = []
    for p in points:
        global_by_step.setdefault(p["step"], []).append(p["global_time"])

    all_global = [t for times in global_by_step.values() for t in times]
    global_range = _range(all_global)
    if global_range is not None:
        global_range["steps"] = list(global_by_step.keys())
    data["global_time_range"] = global_range

    for step in data.get("steps", []):
        name = step.get("step_name")
        is_time = (step.get("procedure") or "").upper() in _TIME_PROCEDURES
        step["time_axis"] = "time" if is_time else "mode"

        frame_values = [float(r["frame_value"] or 0.0)
                        for r in repo.get_frames(name, result_group)]
        step["time_range"] = _range(frame_values)
        step["global_time_range"] = _range(global_by_step.get(name, []))

    return data
