"""全量预计算计划：枚举范围、调用次数与 count_calls 一致、无表面实例只算位移。"""
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from src.l3.core.errors import NotFoundError
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services import precompute_plan as plan


@pytest.fixture
def manifest(tmp_path):
    with sqlite3.connect(tmp_path / "manifest.db") as conn:
        conn.executescript("""
            CREATE TABLE result_files (result_group TEXT, step_name TEXT, field_name TEXT,
                                       file_path TEXT, components TEXT);
            CREATE TABLE result_blocks (result_group TEXT, step_name TEXT, field_name TEXT,
                                        instance_name TEXT);
            CREATE TABLE frames (result_group TEXT, step_name TEXT, frame_idx INT);
        """)
        conn.executemany("INSERT INTO result_files VALUES (?,?,?,?,?)", [
            ("g", "S1", "U", "u.h5", json.dumps(["U1", "U2", "U3"])),
            ("g", "S1", "S_MISES", "m.h5", "[]"),
            ("h", "S1", "U", "u2.h5", json.dumps(["U1", "U2", "U3"])),
        ])
        conn.executemany("INSERT INTO result_blocks VALUES (?,?,?,?)", [
            ("g", "S1", "U", "SOLID"), ("g", "S1", "U", "BEAM"),
            ("g", "S1", "S_MISES", "SOLID"), ("h", "S1", "U", "SOLID"),
        ])
        conn.executemany("INSERT INTO frames VALUES (?,?,?)",
                         [("g", "S1", 0), ("g", "S1", 3), ("g", "S1", 7)])
    return ManifestRepo(str(tmp_path))


def test_list_targets_uses_real_frames_components_and_blocks(manifest):
    targets = {(t.result_group, t.field): t for t in plan.list_targets(manifest)}
    u = targets[("g", "U")]
    assert u.frames == (0, 3, 7)                     # 真实帧编号，不是 range(num_frames)
    assert u.instances == ("BEAM", "SOLID")
    assert u.component_options == (None, 0, 1, 2)
    assert targets[("g", "S_MISES")].component_options == (None,)
    only_h = plan.list_targets(manifest, result_group="h", group_only=True)
    assert [(t.result_group, t.field) for t in only_h] == [("h", "U")]


def test_run_plan_matches_count_and_skips_surface_for_beams(manifest, monkeypatch):
    calls = []

    def record(name):
        def _fn(*args, **kw):
            calls.append((name, args, kw))
        return _fn

    for name in ("compute_scalar_range", "frame_scalars", "frame_colors",
                 "frame_vertex_displacements", "deform_scale_stats"):
        monkeypatch.setattr(plan, name, record(name))
    idx = SimpleNamespace(render_source_elem_row={"SOLID": np.array([0]),
                                                  "BEAM": np.array([], dtype=np.int64)})
    targets = plan.list_targets(manifest, result_group="g", group_only=True)
    stats = plan.run_plan(None, "odb", idx, targets, strict=True)

    assert stats["done"] == stats["total"] == plan.count_calls(idx, targets) == len(calls)
    assert not [c for c in calls if c[1][2] == "BEAM" and c[0] != "frame_vertex_displacements"]
    scalar_keys = {(a[2], a[4], a[5], kw["component_idx"], kw["render_mode"])
                   for name, a, kw in calls if name == "frame_scalars"}
    assert ("SOLID", "U", 0, None, "flat") in scalar_keys
    assert ("SOLID", "U", 7, 2, "smooth") in scalar_keys
    colors = {(kw["component"], kw["render_mode"]) for name, _a, kw in calls
              if name == "frame_colors"}
    assert colors == {(c, m) for c in ("U1", "U2", "U3") for m in ("smooth", "flat")}


def test_no_data_is_skipped_but_other_failures_are_strict(manifest, monkeypatch):
    for name in ("compute_scalar_range", "frame_colors",
                 "frame_vertex_displacements", "deform_scale_stats"):
        monkeypatch.setattr(plan, name, lambda *a, **kw: None)

    def no_data(*a, **kw):
        raise NotFoundError("all NaN")

    monkeypatch.setattr(plan, "frame_scalars", no_data)
    idx = SimpleNamespace(render_source_elem_row={})
    targets = plan.list_targets(manifest, result_group="g", group_only=True)
    stats = plan.run_plan(None, "odb", idx, targets, strict=True)
    assert stats["skipped"] > 0 and stats["failed"] == 0

    def broken(*a, **kw):
        raise OSError("disk read error")

    monkeypatch.setattr(plan, "frame_scalars", broken)
    with pytest.raises(plan.PlanError, match="g/S1/.*disk read error"):
        plan.run_plan(None, "odb", idx, targets, strict=True)
    relaxed = plan.run_plan(None, "odb", idx, targets, strict=False)
    assert relaxed["failed"] > 0


def test_frequency_step_adds_modal_animation_with_suggested_scale(manifest, monkeypatch):
    with sqlite3.connect(manifest.workspace + "/manifest.db") as conn:
        conn.execute("CREATE TABLE steps (result_group TEXT, step_name TEXT, procedure TEXT)")
        conn.execute("INSERT INTO steps VALUES ('g', 'S1', 'FREQUENCY')")
    calls = []
    for name in ("compute_scalar_range", "frame_scalars", "frame_colors",
                 "frame_vertex_displacements", "deform_scale_stats"):
        monkeypatch.setattr(plan, name, lambda *a, **kw: None)
    monkeypatch.setattr(plan, "suggest_deform_scale", lambda reg, odb, step, frame, rg: 10.0 + frame)
    monkeypatch.setattr(plan, "cached_modal_animation",
                        lambda reg, odb, inst, step, frame, **kw: calls.append((inst, frame, kw)))
    idx = SimpleNamespace(render_source_elem_row={"SOLID": np.array([0]),
                                                  "BEAM": np.array([], dtype=np.int64)})
    targets = plan.list_targets(manifest, result_group="g", group_only=True)
    assert {t.field: t.modal for t in targets} == {"U": True, "S_MISES": False}
    stats = plan.run_plan(None, "odb", idx, targets, strict=True)

    assert stats["done"] == stats["total"] == plan.count_calls(idx, targets)
    # 只给有表面的实例、每阶一包；scale 取该阶建议值，n_frames 取前端默认 20
    assert calls == [("SOLID", f, {"scale": 10.0 + f, "n_frames": 20, "result_group": "g"})
                     for f in (0, 3, 7)]
