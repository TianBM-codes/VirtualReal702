"""meta/overview 的时间范围标注：全局区间 + 每个 step 的区间/轴语义。

口径必须与 node-time-value 一致（两边共用 _build_timeline），所以这里也直接
断言"overview 报的范围 = node-time-value 实际接受的范围"。
"""
import sqlite3
from pathlib import Path

import pytest

from src.l1.manifest_schema import MANIFEST_SCHEMA
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services import meta_service

INSTANCE = "PART-1-1"


def _manifest(workspace: Path, steps: list) -> ManifestRepo:
    """steps: [(step_name, step_number, procedure, [frame_value...], total_time, time_period)]"""
    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.executescript(MANIFEST_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO instances (instance_name, part_name, geom_path)"
            " VALUES (?,?,?)",
            (INSTANCE, "PART-1", f"l1/geometry/{INSTANCE}.h5"),
        )
        for name, number, proc, fvs, total, period in steps:
            conn.execute(
                "INSERT INTO steps (result_group, step_name, step_number, procedure,"
                " num_frames, total_time, time_period) VALUES (NULL,?,?,?,?,?,?)",
                (name, number, proc, len(fvs), total, period),
            )
            for fi, fv in enumerate(fvs):
                conn.execute(
                    "INSERT INTO frames (result_group, step_name, frame_idx, frame_value)"
                    " VALUES (NULL,?,?,?)",
                    (name, fi, fv),
                )
        conn.commit()
    return ManifestRepo(str(workspace))


def _steps_by_name(data):
    return {s["step_name"]: s for s in data["steps"]}


def test_two_static_steps_report_local_and_global_ranges(workspace):
    repo = _manifest(workspace, [
        ("Step-1", 1, "STATIC", [0.0, 0.5, 1.0], 0.0, 1.0),
        ("Step-2", 2, "STATIC", [0.0, 0.5, 1.0], 1.0, 1.0),
    ])
    data = meta_service.get_overview(repo)

    # 全局时间轴：Step-1 [0,1] 接 Step-2 [1,2]
    assert data["global_time_range"] == {"min": 0.0, "max": 2.0,
                                         "steps": ["Step-1", "Step-2"]}

    steps = _steps_by_name(data)
    # step 内局部时间：两个 step 都是 [0,1]
    assert steps["Step-1"]["time_range"] == {"min": 0.0, "max": 1.0}
    assert steps["Step-2"]["time_range"] == {"min": 0.0, "max": 1.0}
    # 各自在全局轴上占的段
    assert steps["Step-1"]["global_time_range"] == {"min": 0.0, "max": 1.0}
    assert steps["Step-2"]["global_time_range"] == {"min": 1.0, "max": 2.0}
    assert steps["Step-1"]["time_axis"] == "time"


def test_frequency_step_marked_as_mode_axis_and_excluded_from_global(workspace):
    repo = _manifest(workspace, [
        ("Modal", 0, "FREQUENCY", [1.0, 2.0, 3.0], 0.0, 0.0),
        ("sag", 1, "STATIC", [0.0, 1.0], 0.0, 1.0),
    ])
    data = meta_service.get_overview(repo)

    steps = _steps_by_name(data)
    modal = steps["Modal"]
    # frame_value 是阶次/频率，不是时间 → 标 mode，且不进全局时间轴
    assert modal["time_axis"] == "mode"
    assert modal["time_range"] == {"min": 1.0, "max": 3.0}
    assert modal["global_time_range"] is None

    assert data["global_time_range"] == {"min": 0.0, "max": 1.0, "steps": ["sag"]}
    assert steps["sag"]["time_axis"] == "time"


def test_no_time_step_reports_null_global_range(workspace):
    """全是 FREQUENCY → 不能按全局时间查，global_time_range=null，但 overview 照常返回。"""
    repo = _manifest(workspace, [
        ("Modal", 0, "FREQUENCY", [1.0, 2.0], 0.0, 0.0),
    ])
    data = meta_service.get_overview(repo)

    assert data["global_time_range"] is None
    assert data["steps"][0]["time_range"] == {"min": 1.0, "max": 2.0}
    assert data["instances"][0]["instance_name"] == INSTANCE


def test_step_without_frames_reports_null_range(workspace):
    repo = _manifest(workspace, [("Empty", 1, "STATIC", [], 0.0, 0.0)])
    data = meta_service.get_overview(repo)
    assert data["steps"][0]["time_range"] is None


def test_range_matches_what_node_time_value_accepts(workspace):
    """overview 报的全局区间端点，node-time-value 必须真的能查（口径一致）。"""
    pytest.importorskip("numpy")
    from src.l3.services.node_time_value_service import _build_timeline

    repo = _manifest(workspace, [
        ("Step-1", 1, "STATIC", [0.0, 1.0], 0.0, 1.0),
        ("Step-2", 2, "STATIC", [0.0, 1.0], 1.0, 1.0),
    ])
    reported = meta_service.get_overview(repo)["global_time_range"]

    points, mode = _build_timeline(repo, None, None)
    times = [p["time"] for p in points]
    assert mode == "global"
    assert reported["min"] == min(times) and reported["max"] == max(times)
