import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from typing import Optional

import h5py

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.l3.core.state import OdbRegistry
from src.l3.services.result_service import compute_scalar_range, frame_scalars


@dataclass
class Case:
    workspace: str
    odb_id: str
    instance: str
    result_group: Optional[str]
    step: str
    field: str
    frame: int
    component_idx: Optional[int]
    mode: str


def _pick_case(workspace: str, preferred_field: Optional[str]) -> Case:
    db = os.path.join(workspace, "manifest.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    instances = [
        row["instance_name"]
        for row in con.execute("select instance_name from instances order by instance_name")
    ]
    rows = con.execute(
        "select result_group, step_name, field_name from result_files "
        "order by result_group, step_name, field_name"
    ).fetchall()
    con.close()
    if not instances:
        raise RuntimeError(f"no instances in {db}")
    if preferred_field:
        selected = [row for row in rows if row["field_name"] == preferred_field]
    else:
        selected = []
    if not selected:
        preferred = ["U", "S", "E", "S_MISES", "E_MAX_PRINCIPAL"]
        for field in preferred:
            selected = [row for row in rows if row["field_name"] == field]
            if selected:
                break
    if not selected and rows:
        selected = [rows[0]]
    if not selected:
        raise RuntimeError(f"no result_files in {db}")
    row = selected[0]
    return Case(
        workspace=workspace,
        odb_id=os.path.basename(os.path.normpath(workspace)),
        instance=instances[0],
        result_group=row["result_group"],
        step=row["step_name"],
        field=row["field_name"],
        frame=0,
        component_idx=None,
        mode="smooth",
    )


def _geometry_stats(workspace: str, instance: str) -> dict:
    path = os.path.join(workspace, "l2", "render", f"{instance}_render.h5")
    with h5py.File(path, "r") as f:
        pos = f["render/positions"].shape if "render/positions" in f else None
        idx = f["render/indices"].shape if "render/indices" in f else None
        return {
            "positions": pos,
            "indices": idx,
            "has_normals": "render/normals" in f,
            "file_mb": round(os.path.getsize(path) / 1024 / 1024, 3),
        }


def _time(label, fn):
    t0 = time.perf_counter()
    value = fn()
    dt = time.perf_counter() - t0
    print(f"{label}: {dt:.6f}s")
    return dt, value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=r"D:\WorkSpace\OtherProjects\VirtualReal702\model\22")
    ap.add_argument("--field", default="")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    case = _pick_case(args.workspace, args.field or None)
    print("CASE", json.dumps(asdict(case), ensure_ascii=False, indent=2))
    print("GEOMETRY", json.dumps(_geometry_stats(case.workspace, case.instance), ensure_ascii=False))

    registry = OdbRegistry()
    load_dt, _ = _time("registry.load", lambda: registry.load(case.odb_id, case.workspace, "ready"))

    results = {"load": load_dt, "range": [], "scalars": []}
    for i in range(args.repeat):
        dt, rng = _time(
            f"range[{i}]",
            lambda: compute_scalar_range(
                registry=registry,
                odb_id=case.odb_id,
                instance=case.instance,
                step=case.step,
                field=case.field,
                frame_idx=case.frame,
                component_idx=case.component_idx,
                render_mode=case.mode,
                result_group=case.result_group,
            ),
        )
        results["range"].append({"seconds": dt, "value": rng})

        dt, payload = _time(
            f"scalars[{i}]",
            lambda: frame_scalars(
                registry=registry,
                odb_id=case.odb_id,
                instance=case.instance,
                step=case.step,
                field=case.field,
                frame_idx=case.frame,
                component_idx=case.component_idx,
                render_mode=case.mode,
                result_group=case.result_group,
                override_min=rng[0] if rng else None,
                override_max=rng[1] if rng else None,
            ),
        )
        u, legend, pos = payload
        results["scalars"].append(
            {
                "seconds": dt,
                "len": int(len(u)),
                "mb": round(u.nbytes / 1024 / 1024, 3),
                "legend": [float(legend[0]), float(legend[1])],
                "position": pos,
            }
        )

    print("SUMMARY", json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
