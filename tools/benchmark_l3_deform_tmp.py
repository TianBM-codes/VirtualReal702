import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.l3.core.state import OdbRegistry
from src.l3.services.result_service import (
    frame_deformed_with_aux,
    frame_vertex_displacements,
)


def _time(label, fn):
    t0 = time.perf_counter()
    value = fn()
    dt = time.perf_counter() - t0
    print(f"{label}: {dt:.6f}s")
    return dt, value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--odb-id", default="")
    ap.add_argument("--instance", default="CHANGE")
    ap.add_argument("--result-group", default="bench_sol103")
    ap.add_argument("--step", default="SUBCASE_1")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    odb_id = args.odb_id or os.path.basename(os.path.normpath(args.workspace))
    print("CASE", json.dumps(vars(args), ensure_ascii=False, indent=2))
    registry = OdbRegistry()
    _time("registry.load", lambda: registry.load(odb_id, args.workspace, "ready"))

    summary = {"displacements": [], "deformed_with_normals": []}
    for i in range(args.repeat):
        dt, disp = _time(
            f"vertex_displacements[{i}]",
            lambda: frame_vertex_displacements(
                registry,
                odb_id,
                args.instance,
                args.step,
                args.frame,
                args.result_group,
            ),
        )
        summary["displacements"].append({
            "seconds": dt,
            "shape": list(disp.shape),
            "mb": round(disp.nbytes / 1024 / 1024, 3),
        })

        dt, payload = _time(
            f"deformed_with_normals[{i}]",
            lambda: frame_deformed_with_aux(
                registry,
                odb_id,
                args.instance,
                args.step,
                args.frame,
                args.scale,
                args.result_group,
            ),
        )
        pos, normals, aux = payload
        summary["deformed_with_normals"].append({
            "seconds": dt,
            "positions_shape": list(pos.shape),
            "normals_shape": list(normals.shape),
            "positions_mb": round(pos.nbytes / 1024 / 1024, 3),
            "normals_mb": round(normals.nbytes / 1024 / 1024, 3),
            "aux_sections": len(aux),
        })

    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
