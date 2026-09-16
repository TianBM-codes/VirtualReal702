import argparse
import json
import os
import sys
import time

import h5py
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.l3.services.result_service import _compute_vertex_normals


def _time(label, fn):
    t0 = time.perf_counter()
    value = fn()
    dt = time.perf_counter() - t0
    print(f"{label}: {dt:.6f}s")
    return dt, value


def _vtk_normals(positions, indices):
    import pyvista as pv

    n = indices.shape[0]
    faces = np.empty((n, 4), dtype=np.int64)
    faces[:, 0] = 3
    faces[:, 1:] = indices.astype(np.int64, copy=False)
    mesh = pv.PolyData(positions, faces.ravel())
    mesh = mesh.compute_normals(
        point_normals=True,
        cell_normals=False,
        split_vertices=False,
        auto_orient_normals=False,
        consistent_normals=False,
        inplace=False,
    )
    return np.asarray(mesh.point_data["Normals"], dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-h5", required=True)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    with h5py.File(args.render_h5, "r") as f:
        positions = np.ascontiguousarray(f["render/positions"][:], dtype=np.float32)
        indices = np.ascontiguousarray(f["render/indices"][:], dtype=np.int32)
    print(
        "GEOMETRY",
        json.dumps(
            {
                "positions": list(positions.shape),
                "indices": list(indices.shape),
                "pos_mb": round(positions.nbytes / 1024 / 1024, 3),
                "idx_mb": round(indices.nbytes / 1024 / 1024, 3),
            },
            ensure_ascii=False,
        ),
    )

    summary = {"numpy": [], "pyvista": []}
    for i in range(args.repeat):
        dt, normals = _time(f"numpy_add_at[{i}]", lambda: _compute_vertex_normals(positions, indices))
        summary["numpy"].append({"seconds": dt, "shape": list(normals.shape)})

    for i in range(args.repeat):
        dt, normals = _time(f"pyvista_vtk[{i}]", lambda: _vtk_normals(positions, indices))
        summary["pyvista"].append({"seconds": dt, "shape": list(normals.shape)})

    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
