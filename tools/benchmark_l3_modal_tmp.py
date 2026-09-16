import argparse
import asyncio
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.l3.api.routes.results import get_modal_animation, get_modal_shape
from src.l3.core.state import registry


async def _time(label, fn):
    t0 = time.perf_counter()
    value = await fn()
    dt = time.perf_counter() - t0
    body = value.body
    print(f"{label}: {dt:.6f}s mb={len(body)/1024/1024:.3f}")
    return {"seconds": dt, "payload_mb": round(len(body) / 1024 / 1024, 3)}


async def main_async():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--instance", default="CHANGE")
    ap.add_argument("--result-group", default="bench_sol103")
    ap.add_argument("--step", default="SUBCASE_1")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--n-frames", type=int, default=20)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    odb_id = os.path.basename(os.path.normpath(args.workspace))
    registry.load(odb_id, args.workspace, "ready")
    summary = {"modal_shape": [], "modal_animation": []}
    for i in range(args.repeat):
        summary["modal_shape"].append(await _time(
            f"modal_shape[{i}]",
            lambda: get_modal_shape(
                odb_id=odb_id,
                instance=args.instance,
                step=args.step,
                frame=args.frame,
                result_group=args.result_group,
            ),
        ))
    for i in range(args.repeat):
        summary["modal_animation"].append(await _time(
            f"modal_animation[{i}]",
            lambda: get_modal_animation(
                odb_id=odb_id,
                instance=args.instance,
                step=args.step,
                frame=args.frame,
                scale=args.scale,
                n_frames=args.n_frames,
                result_group=args.result_group,
            ),
        ))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main_async())
