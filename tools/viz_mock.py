#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
viz_mock.py — 直接读 l1_raw npy 文件，可视化 mock 模型 + 位移云图。

Usage:
    python3 tools/viz_mock.py --workspace /tmp/mock_odb
    python3 tools/viz_mock.py --workspace /tmp/mock_odb --frame 2
    python3 tools/viz_mock.py --workspace /tmp/mock_odb --frame 3 --deform 5.0
    python3 tools/viz_mock.py --workspace /tmp/mock_odb --all-frames
"""

import argparse
import json
import os

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_instance(raw_dir, inst_name):
    """Load geometry + all frame displacements for one instance."""
    s    = inst_name.replace("/", "__").replace("\\", "__").replace(" ", "_")
    gdir = os.path.join(raw_dir, "geom", s)
    coords = np.load(os.path.join(gdir, "node_coords.npy"))   # [N, 3]
    conn   = np.load(os.path.join(gdir, "elems", "S4R", "conn.npy"))  # [M, 4]

    rdir   = os.path.join(raw_dir, "results", "Step-1_U", s, "NODAL")
    frames = sorted(f for f in os.listdir(rdir) if f.startswith("f") and f.endswith(".npy"))
    disp   = np.stack([np.load(os.path.join(rdir, f)) for f in frames])  # [F, N, 3]

    return coords, conn, disp


def quad_to_tris(conn):
    """Split [M, 4] quad connectivity into [2M, 3] triangle rows."""
    t1 = conn[:, [0, 1, 2]]
    t2 = conn[:, [0, 2, 3]]
    return np.concatenate([t1, t2], axis=0)


def make_polys(coords, conn):
    """Return list of quad vertex arrays for Poly3DCollection."""
    return [coords[quad] for quad in conn]


def usum(disp_frame):
    """[N, 3] → [N] magnitude."""
    return np.linalg.norm(disp_frame, axis=1)


def colorize(values, cmap="jet"):
    """Map scalar [N] → RGBA [N, 4] using global min/max."""
    vmin, vmax = values.min(), values.max()
    if vmax - vmin < 1e-12:
        norm_vals = np.zeros_like(values)
    else:
        norm_vals = (values - vmin) / (vmax - vmin)
    cm = plt.get_cmap(cmap)
    return cm(norm_vals), vmin, vmax


def face_color_from_node_colors(conn, node_colors):
    """Average node RGBA colors over each quad face → [M, 4]."""
    return node_colors[conn].mean(axis=1)


# ─── Single-frame plot ────────────────────────────────────────────────────────

def plot_frame(raw_dir, inst_names, frame_idx, deform_scale, save_path=None):
    fig = plt.figure(figsize=(16, 9))
    ax  = fig.add_subplot(111, projection="3d")

    all_usum = []
    geom_data = []
    for inst_name in inst_names:
        coords, conn, disp = load_instance(raw_dir, inst_name)
        fi      = min(frame_idx, disp.shape[0] - 1)
        d_frame = disp[fi]                                  # [N, 3]
        deformed = coords + d_frame * deform_scale          # [N, 3]
        us       = usum(d_frame)                            # [N]
        all_usum.append(us)
        geom_data.append((deformed, conn, us))

    # Global color scale across all instances
    global_min = min(u.min() for u in all_usum)
    global_max = max(u.max() for u in all_usum)
    cm = plt.get_cmap("jet")

    for deformed, conn, us in geom_data:
        if global_max - global_min < 1e-12:
            norm_us = np.zeros_like(us)
        else:
            norm_us = (us - global_min) / (global_max - global_min)

        node_rgba  = cm(norm_us)                           # [N, 4]
        face_rgba  = face_color_from_node_colors(conn, node_rgba)  # [M, 4]
        polys      = make_polys(deformed, conn)

        pc = Poly3DCollection(polys, zsort="min")
        pc.set_facecolor(face_rgba)
        pc.set_edgecolor([0.2, 0.2, 0.2, 0.3])
        pc.set_linewidth(0.3)
        ax.add_collection3d(pc)

    # Bounding box
    all_pts = np.concatenate(
        [load_instance(raw_dir, n)[0] for n in inst_names], axis=0
    )
    margin = 0.1
    ax.set_xlim(all_pts[:, 0].min() - margin, all_pts[:, 0].max() + margin)
    ax.set_ylim(all_pts[:, 1].min() - margin, all_pts[:, 1].max() + margin)
    ax.set_zlim(all_pts[:, 2].min() - margin, all_pts[:, 2].max() + margin)

    # Colorbar via ScalarMappable
    sm = plt.cm.ScalarMappable(
        cmap="jet",
        norm=mcolors.Normalize(vmin=global_min, vmax=global_max),
    )
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.6, pad=0.1)
    cbar.set_label("USUM (displacement magnitude)")

    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_title(
        "Mock ODB — {} instances, Frame {} (deform ×{})".format(
            len(inst_names), frame_idx, deform_scale
        )
    )
    ax.view_init(elev=30, azim=-60)

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print("Saved: {}".format(save_path))
    else:
        plt.tight_layout()
        plt.show()

    plt.close(fig)


# ─── All-frames grid ──────────────────────────────────────────────────────────

def plot_all_frames(raw_dir, inst_names, deform_scale, save_path=None):
    # Only use first instance for the multi-frame view (cleaner)
    inst_name = inst_names[0]
    coords, conn, disp = load_instance(raw_dir, inst_name)
    n_frames = disp.shape[0]

    fig = plt.figure(figsize=(5 * n_frames, 5))
    fig.suptitle(
        "{} — all frames (deform ×{})".format(inst_name, deform_scale),
        fontsize=13,
    )

    global_min = usum(disp.reshape(-1, 3)).min()
    global_max = usum(disp.reshape(-1, 3)).max()
    cm         = plt.get_cmap("jet")

    for fi in range(n_frames):
        ax = fig.add_subplot(1, n_frames, fi + 1, projection="3d")
        d_frame  = disp[fi]
        deformed = coords + d_frame * deform_scale
        us       = usum(d_frame)

        norm_us   = (us - global_min) / max(global_max - global_min, 1e-12)
        node_rgba = cm(norm_us)
        face_rgba = face_color_from_node_colors(conn, node_rgba)
        polys     = make_polys(deformed, conn)

        pc = Poly3DCollection(polys, zsort="min")
        pc.set_facecolor(face_rgba)
        pc.set_edgecolor([0.3, 0.3, 0.3, 0.4])
        pc.set_linewidth(0.3)
        ax.add_collection3d(pc)

        ax.set_xlim(coords[:, 0].min() - 0.1, coords[:, 0].max() + 0.1)
        ax.set_ylim(coords[:, 1].min() - 0.1, coords[:, 1].max() + 0.1)
        ax.set_zlim(-0.5, 0.5)
        ax.set_title("Frame {}".format(fi))
        ax.set_axis_off()
        ax.view_init(elev=25, azim=-50)

    sm = plt.cm.ScalarMappable(
        cmap="jet",
        norm=mcolors.Normalize(vmin=global_min, vmax=global_max),
    )
    sm.set_array([])
    fig.colorbar(sm, ax=fig.axes, shrink=0.6, label="USUM")

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print("Saved: {}".format(save_path))
    else:
        plt.tight_layout()
        plt.show()

    plt.close(fig)


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Visualize mock l1_raw data")
    p.add_argument("--workspace",  required=True)
    p.add_argument("--frame",      type=int,   default=0,    help="Frame index (default 0)")
    p.add_argument("--deform",     type=float, default=10.0, help="Deformation scale (default 10)")
    p.add_argument("--all-frames", action="store_true",      help="Show all frames side by side")
    p.add_argument("--save",       default=None,             help="Save to PNG instead of showing")
    return p.parse_args()


if __name__ == "__main__":
    args    = parse_args()
    raw_dir = os.path.join(args.workspace, "l1_raw")

    meta_path = os.path.join(raw_dir, "dump_meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    inst_names = sorted(meta["instances"].keys())

    print("Instances: {}".format(inst_names))

    if args.all_frames:
        plot_all_frames(raw_dir, inst_names, args.deform, save_path=args.save)
    else:
        plot_frame(raw_dir, inst_names, args.frame, args.deform, save_path=args.save)
