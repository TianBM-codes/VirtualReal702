"""run_plan_parallel 的调度：每个目标恰好派发一次、条目合并、失败与 worker 崩溃都要报错。

用一个假 worker（python -c 脚本）代替真实计算，只验证父进程的协议与调度。
"""
import sys
from types import SimpleNamespace

import pytest

from src.l3.services import precompute_plan as plan

FAKE_WORKER = r"""
import json, sys
for line in sys.stdin:
    t = json.loads(line)
    if t["field"] == "BAD":
        print(json.dumps({"ok": False, "error": "g/S1/BAD 计算失败：boom"}), flush=True)
        continue
    if t["field"] == "CRASH":
        sys.exit(3)
    n = len(t["frames"]) * len(t["instances"])
    print(json.dumps({"ok": True, "rss_mb": 100,
                      "stats": {"done": n, "skipped": 0, "failed": 0, "by_kind": {"scalars": n}},
                      "entries": [[sys.argv[1], t["field"] + ".npz"]]}), flush=True)
"""


def _targets(fields):
    return [plan.Target(result_group="g", step="S1", field=f, file_path=f"{f}.h5",
                        instances=("A", "B"), frames=(0, 1, 2), n_components=0) for f in fields]


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setattr(plan, "_worker_cmd", lambda odb_id, ws: [sys.executable, "-c", FAKE_WORKER, ws])


IDX = SimpleNamespace(render_source_elem_row={})


def test_every_target_dispatched_once_and_entries_merged(fake_worker):
    fields = [f"F{i}" for i in range(9)]
    entries = set()
    stats = plan.run_plan_parallel("odb", "WS", IDX, _targets(fields), max_workers=3, entries=entries)
    assert entries == {("WS", f"{f}.npz") for f in fields}
    assert stats["done"] == 9 * 6
    assert 1 <= stats["workers"] <= 3


def test_failure_stops_and_raises(fake_worker):
    with pytest.raises(plan.PlanError, match="BAD 计算失败"):
        plan.run_plan_parallel("odb", "WS", IDX, _targets(["F0", "BAD", "F1"]),
                               max_workers=2, entries=set())


def test_worker_crash_is_reported(fake_worker):
    with pytest.raises(plan.PlanError, match="意外退出"):
        plan.run_plan_parallel("odb", "WS", IDX, _targets(["CRASH"]), max_workers=1, entries=set())


def test_target_json_round_trip():
    t = _targets(["S"])[0]
    assert plan.target_from_json(plan.target_to_json(t)) == t


OOM_WORKER = r"""
import json, os, sys
marker = os.path.join(sys.argv[1], "big_seen")
for line in sys.stdin:
    t = json.loads(line)
    if t["field"] == "ALWAYS_OOM" or (t["field"] == "BIG" and not os.path.exists(marker)):
        open(marker, "w").close()
        print(json.dumps({"ok": False, "oom": True,
                          "error": "g/S1/BIG 计算失败：Unable to allocate 13.8 MiB"}), flush=True)
        continue
    n = len(t["frames"]) * len(t["instances"])
    print(json.dumps({"ok": True, "rss_mb": 1,
                      "stats": {"done": n, "skipped": 0, "failed": 0, "by_kind": {"scalars": n}},
                      "entries": [[sys.argv[1], t["field"] + ".npz"]]}), flush=True)
"""


@pytest.fixture
def oom_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(plan, "_worker_cmd",
                        lambda odb_id, ws: [sys.executable, "-c", OOM_WORKER, str(tmp_path)])
    return tmp_path


def test_oom_with_other_workers_retries_on_fewer_processes(oom_worker):
    fields = ["F0", "BIG", "F1", "F2", "F3"]
    entries = set()
    messages = []
    stats = plan.run_plan_parallel("odb", "WS", IDX, _targets(fields), max_workers=2,
                                   entries=entries, progress=lambda m, p: messages.append(m))
    assert {e[1] for e in entries} == {f"{f}.npz" for f in fields}
    assert stats["done"] == len(fields) * 6
    assert any("内存不足" in m for m in messages)


def test_oom_on_single_worker_fails(oom_worker):
    with pytest.raises(plan.PlanError, match="Unable to allocate"):
        plan.run_plan_parallel("odb", "WS", IDX, _targets(["ALWAYS_OOM"]), max_workers=1,
                               entries=set())


def test_worker_detects_memory_error_through_chain():
    from src.l3.services.precompute_worker import _is_memory_error

    try:
        try:
            raise MemoryError("Unable to allocate")
        except MemoryError as inner:
            raise plan.PlanError("计算失败") from inner
    except plan.PlanError as exc:
        assert _is_memory_error(exc)
    assert not _is_memory_error(ValueError("x"))


def test_surface_stub_counts_same_as_full_index():
    import numpy as np

    full = SimpleNamespace(render_source_elem_row={"A": np.arange(5), "B": np.array([], dtype=np.int64)})
    stub = plan.surface_stub(full)
    targets = _targets(["S", "U"])
    assert plan.count_calls(stub, targets) == plan.count_calls(full, targets)
    assert plan._has_surface(stub, "A") and not plan._has_surface(stub, "B")
    # 不在表里的实例按"有表面"处理，与完整索引一致
    assert plan._has_surface(stub, "C") == plan._has_surface(full, "C")
