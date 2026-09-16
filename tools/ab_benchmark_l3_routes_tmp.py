#!/usr/bin/env python
"""Route-level A/B timing helper for two VirtualReal702 code roots."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from typing import Any


def _response_size(resp: Any) -> int:
    body = getattr(resp, "body", None)
    if body is not None:
        return len(body)
    content = getattr(resp, "content", None)
    if content is not None:
        return len(content)
    return 0


def _headers(resp: Any) -> dict[str, str]:
    headers = getattr(resp, "headers", {}) or {}
    return {str(k): str(v) for k, v in dict(headers).items()}


async def _call(case: str, routes: Any, args: argparse.Namespace) -> Any:
    common = {
        "odb_id": args.odb_id,
        "instance": args.instance,
        "step": args.step,
        "frame": args.frame,
        "result_group": args.result_group,
    }
    if case == "deformed":
        return await routes.get_deformed_positions(scale=args.scale, **common)
    if case == "modal-shape":
        return await routes.get_modal_shape(**common)
    if case == "modal-animation":
        return await routes.get_modal_animation(
            scale=args.scale,
            n_frames=args.n_frames,
            **common,
        )
    raise ValueError(f"unknown case: {case}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--case", choices=["deformed", "modal-shape", "modal-animation"], required=True)
    ap.add_argument("--repeat", type=int, default=4)
    ap.add_argument("--odb-id", default="bench")
    ap.add_argument("--instance", default="CHANGE")
    ap.add_argument("--step", default="Nastran")
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--n-frames", type=int, default=20)
    ap.add_argument("--result-group", default="bench_sol103")
    args = ap.parse_args()

    code_root = os.path.abspath(args.code_root)
    workspace = os.path.abspath(args.workspace)
    sys.path.insert(0, code_root)
    os.chdir(code_root)

    state = importlib.import_module("src.l3.core.state")
    routes = importlib.import_module("src.l3.api.routes.results")
    state.registry.load(args.odb_id, workspace, "ready")

    samples = []
    for i in range(args.repeat):
        t0 = time.perf_counter()
        resp = asyncio.run(_call(args.case, routes, args))
        dt = time.perf_counter() - t0
        headers = _headers(resp)
        samples.append({
            "i": i,
            "seconds": dt,
            "mb": _response_size(resp) / (1024 * 1024),
            "cache": headers.get("x-cache") or headers.get("X-Cache") or "",
            "headers": {
                k: headers[k]
                for k in sorted(headers)
                if k.lower().startswith("x-")
            },
        })

    print(json.dumps({
        "label": args.label or os.path.basename(code_root),
        "code_root": code_root,
        "workspace": workspace,
        "case": args.case,
        "frame": args.frame,
        "scale": args.scale,
        "n_frames": args.n_frames,
        "repeat": args.repeat,
        "samples": samples,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
