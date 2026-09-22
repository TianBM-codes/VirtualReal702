import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.l3.core.state import OdbRegistry
from src.l3.infra.l3be import build as l3be_build
from src.l3.services.result_service import frame_deformed_with_aux


def _time(label, fn):
    t0 = time.perf_counter()
    value = fn()
    dt = time.perf_counter() - t0
    print(f"{label}: {dt:.6f}s")
    return dt, value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--instance", default="CHANGE")
    ap.add_argument("--result-group", default="bench_sol103")
    ap.add_argument("--step", default="SUBCASE_1")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    odb_id = os.path.basename(os.path.normpath(args.workspace))
    registry = OdbRegistry()
    registry.load(odb_id, args.workspace, "ready")
    summary = []
    for i in range(args.repeat):
        compute_dt, payload = _time(
            f"compute[{i}]",
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
        positions, normals, aux = payload
        sections = [("positions", positions), ("normals", normals)]
        sections.extend(aux)
        pack_dt, blob = _time(f"l3be_build[{i}]", lambda: l3be_build(sections))
        summary.append({
            "compute_s": compute_dt,
            "pack_s": pack_dt,
            "payload_mb": round(len(blob) / 1024 / 1024, 3),
            "sections": len(sections),
        })
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
