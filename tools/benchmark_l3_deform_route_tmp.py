import argparse
import asyncio
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.l3.api.routes.results import get_deformed_positions
from src.l3.core.state import registry


async def main_async():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--odb-id", default="")
    ap.add_argument("--instance", default="CHANGE")
    ap.add_argument("--result-group", default="bench_sol103")
    ap.add_argument("--step", default="SUBCASE_1")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--repeat", type=int, default=4)
    args = ap.parse_args()

    odb_id = args.odb_id or os.path.basename(os.path.normpath(args.workspace))
    registry.load(odb_id, args.workspace, "ready")
    summary = []
    for i in range(args.repeat):
        t0 = time.perf_counter()
        response = await get_deformed_positions(
            odb_id=odb_id,
            instance=args.instance,
            step=args.step,
            frame=args.frame,
            scale=args.scale,
            result_group=args.result_group,
        )
        dt = time.perf_counter() - t0
        body = response.body
        summary.append({
            "seconds": dt,
            "payload_mb": round(len(body) / 1024 / 1024, 3),
            "x_cache": response.headers.get("x-cache") or response.headers.get("X-Cache"),
        })
        print(f"route[{i}]: {dt:.6f}s cache={summary[-1]['x_cache']} mb={summary[-1]['payload_mb']}")
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main_async())
