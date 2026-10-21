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
