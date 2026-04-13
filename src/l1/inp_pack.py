#!/usr/bin/env python3
"""
inp_pack.py — INP → L1 HDF5 exporter (standalone subprocess entry point)

Called by runner_thread._run_l1_inp as a subprocess so that numpy/h5py/MKL
are initialised inside the child process, not inside the uvicorn parent.
This avoids the Intel Fortran runtime (libifcoremd.dll) registering a
console-ctrl handler in the parent process on Windows.

Usage:
    python src/l1/inp_pack.py --inp /path/to/model.inp --workspace /path/to/ws
"""
import argparse
import sys
import os

# Ensure the project root (parent of src/) is on sys.path when this script
# is executed directly (e.g. python src/l1/inp_pack.py ...).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))  # src/l1 -> src -> project root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse INP and export L1 HDF5")
    parser.add_argument("--inp", required=True, help="Path to .inp file")
    parser.add_argument("--workspace", required=True, help="Workspace directory")
    args = parser.parse_args()

    from src.inp import parse_inp
    from src.inp.exporter import export_l1

    print(f"[inp_pack] parsing {args.inp}", flush=True)
    model = parse_inp(args.inp)
    diag_errors = [d for d in model.diagnostics if getattr(d, "level", "") == "ERROR"]
    if diag_errors:
        for d in diag_errors:
            print(f"[inp_pack] ERROR: {d}", flush=True)

    print("[inp_pack] exporting L1 HDF5", flush=True)
    export_l1(model, args.workspace)
    print("[inp_pack] done", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        print(f"\n[inp_pack] FAILED: {exc}", flush=True)
        traceback.print_exc()
        sys.exit(1)
