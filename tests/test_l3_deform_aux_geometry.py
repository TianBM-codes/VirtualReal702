"""
Tests for deformed line/point/coupling overlay geometry (batch 1–3) and the
single-U-read merge (frame_deformed_with_aux).

Convention: written here, run by the developer locally (`pytest tests/ -v`).
"""
import h5py
import numpy as np
import pytest

from src.l3.services.result_service import (
    frame_deformed_aux_geometry,
    frame_deformed_with_aux,
    _disp_at_rows,
)

INSTANCE = "PART-1-1"
STEP = "Step-1"


def _u_frame():
    """U displacement for 5 nodes at frame 0; U[row] = [row+.1, row+.2, row+.3]."""
    rows = np.arange(5, dtype=np.float32)
    u = np.stack([rows + 0.1, rows + 0.2, rows + 0.3], axis=1)  # [5, 3]
    return u


def _write_u(write_nodal_result_file, workspace, u_frame0):
    # data shape [num_frames, num_nodes, 3]
    data = u_frame0[None, :, :].astype(np.float32)  # 1 frame
    return write_nodal_result_file(
        workspace, INSTANCE, step=STEP, field="U", data=data,
    )


def _surface_path(workspace):
    d = workspace / "l2" / "geometry"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{INSTANCE}_surface.h5"


def _sections_dict(sections):
    return {name: arr for name, arr in sections}


# ── _disp_at_rows padding ────────────────────────────────────────────────────

def test_disp_at_rows_pads_missing_rows_with_zero():
    disp = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)  # 2 nodes
    rows = np.array([0, 1, 5], dtype=np.int64)  # row 5 is out of range
    out = _disp_at_rows(disp, rows)
    assert out.shape == (3, 3)
    np.testing.assert_array_equal(out[0], [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(out[1], [4.0, 5.0, 6.0])
    np.testing.assert_array_equal(out[2], [0.0, 0.0, 0.0])  # padded → no motion


def test_disp_at_rows_empty_rows():
    disp = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
    out = _disp_at_rows(disp, np.array([], dtype=np.int64))
    assert out.shape == (0, 3)


# ── aux deform correctness ───────────────────────────────────────────────────

def test_deformed_aux_geometry_applies_node_displacement(
    workspace, make_registry, write_nodal_result_file,
):
    registry = make_registry(workspace, instance=INSTANCE)
    u = _u_frame()
    _write_u(write_nodal_result_file, workspace, u)

    # lines: 1 segment, endpoints at node rows 1 and 3
    line_pos = np.array([[[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]]], dtype=np.float32)  # [1,2,3]
    line_rows = np.array([[1, 3]], dtype=np.int32)                               # [1,2]
    # points: 1 point at node row 2
    point_pos = np.array([[2.0, 2.0, 2.0]], dtype=np.float32)                    # [1,3]
    point_rows = np.array([2], dtype=np.int32)                                   # [1]
    # couplings: 1 seg, endpoints at rows 0 and 4 (already [N*2,3])
    coup_pos = np.array([[0.0, 0.0, 0.0], [4.0, 4.0, 4.0]], dtype=np.float32)    # [2,3]
    coup_rows = np.array([0, 4], dtype=np.int32)                                 # [2]

    with h5py.File(_surface_path(workspace), "w") as f:
        f.create_dataset("lines/positions", data=line_pos)
        f.create_dataset("lines/node_rows", data=line_rows)
        f.create_dataset("points/positions", data=point_pos)
        f.create_dataset("points/node_rows", data=point_rows)
        f.create_dataset("couplings/positions", data=coup_pos)
        f.create_dataset("couplings/node_rows", data=coup_rows)

    scale = 2.0
    sec = _sections_dict(frame_deformed_aux_geometry(
        registry=registry, odb_id="odb", instance=INSTANCE,
        step=STEP, frame_idx=0, scale=scale,
    ))

    assert set(sec.keys()) == {"line_positions", "point_positions", "coupling_positions"}

    # lines: [N*2, 3], row order (seg0_end0, seg0_end1)
    exp_line = np.array([
        [1.0, 0.0, 0.0] + scale * u[1],
        [3.0, 0.0, 0.0] + scale * u[3],
    ], dtype=np.float32)
    np.testing.assert_allclose(sec["line_positions"], exp_line, rtol=1e-5)

    exp_point = np.array([[2.0, 2.0, 2.0] + scale * u[2]], dtype=np.float32)
    np.testing.assert_allclose(sec["point_positions"], exp_point, rtol=1e-5)

    exp_coup = np.array([
        [0.0, 0.0, 0.0] + scale * u[0],
        [4.0, 4.0, 4.0] + scale * u[4],
    ], dtype=np.float32)
    np.testing.assert_allclose(sec["coupling_positions"], exp_coup, rtol=1e-5)


def test_deformed_aux_geometry_missing_node_rows_returns_empty(
    workspace, make_registry, write_nodal_result_file,
):
    """Legacy surface.h5 with positions but no node_rows → no aux sections."""
    registry = make_registry(workspace, instance=INSTANCE)
    _write_u(write_nodal_result_file, workspace, _u_frame())

    with h5py.File(_surface_path(workspace), "w") as f:
        f.create_dataset("lines/positions",
                         data=np.array([[[1.0, 0, 0], [2.0, 0, 0]]], dtype=np.float32))
        # deliberately NO lines/node_rows

    sections = frame_deformed_aux_geometry(
        registry=registry, odb_id="odb", instance=INSTANCE,
        step=STEP, frame_idx=0, scale=1.0,
    )
    assert sections == []


def test_deformed_aux_geometry_node_without_u_stays_put(
    workspace, make_registry, write_nodal_result_file,
):
    """A point whose node row exceeds the U array gets zero displacement."""
    registry = make_registry(workspace, instance=INSTANCE)
    _write_u(write_nodal_result_file, workspace, _u_frame())  # 5 nodes only

    point_pos = np.array([[7.0, 8.0, 9.0]], dtype=np.float32)
    point_rows = np.array([99], dtype=np.int32)  # far beyond U → no motion
    with h5py.File(_surface_path(workspace), "w") as f:
        f.create_dataset("points/positions", data=point_pos)
        f.create_dataset("points/node_rows", data=point_rows)

    sec = _sections_dict(frame_deformed_aux_geometry(
        registry=registry, odb_id="odb", instance=INSTANCE,
        step=STEP, frame_idx=0, scale=5.0,
    ))
    np.testing.assert_allclose(sec["point_positions"], point_pos, rtol=1e-6)


def test_deformed_aux_geometry_no_surface_file_returns_empty(
    workspace, make_registry, write_nodal_result_file,
):
    registry = make_registry(workspace, instance=INSTANCE)
    _write_u(write_nodal_result_file, workspace, _u_frame())
    # no surface.h5 written at all
    assert frame_deformed_aux_geometry(
        registry=registry, odb_id="odb", instance=INSTANCE,
        step=STEP, frame_idx=0, scale=1.0,
    ) == []


# ── combined single-read path ────────────────────────────────────────────────

def test_deformed_with_aux_matches_surface_and_aux(
    workspace, make_registry, write_nodal_result_file,
):
    """frame_deformed_with_aux deforms the surface AND returns aux sections,
    from a single U read.  Verifies both halves against orig + scale*U[row]."""
    registry = make_registry(workspace, instance=INSTANCE)
    idx = registry.get("odb")
    u = _u_frame()
    _write_u(write_nodal_result_file, workspace, u)

    # Surface render.h5: 3 verts → node rows 0,1,2; one triangle.
    render_dir = workspace / "l2" / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    surf_pos = np.array([[0.0, 0.0, 0.0],
                         [1.0, 0.0, 0.0],
                         [0.0, 1.0, 0.0]], dtype=np.float32)
    vtx_node_row = np.array([0, 1, 2], dtype=np.int32)
    with h5py.File(render_dir / f"{INSTANCE}_render.h5", "w") as f:
        f.create_dataset("render/positions", data=surf_pos)
        f.create_dataset("render/indices", data=np.array([[0, 1, 2]], dtype=np.int32))
    idx.vtx_node_row[INSTANCE] = vtx_node_row

    # Aux: one point at node row 1
    with h5py.File(_surface_path(workspace), "w") as f:
        f.create_dataset("points/positions",
                         data=np.array([[5.0, 5.0, 5.0]], dtype=np.float32))
        f.create_dataset("points/node_rows", data=np.array([1], dtype=np.int32))

    scale = 3.0
    positions, normals, aux = frame_deformed_with_aux(
        registry=registry, odb_id="odb", instance=INSTANCE,
        step=STEP, frame_idx=0, scale=scale,
    )

    exp_surface = surf_pos + scale * u[vtx_node_row]
    np.testing.assert_allclose(positions, exp_surface, rtol=1e-5)
    assert positions.shape == (3, 3)
    assert normals.shape == (3, 3)

    sec = _sections_dict(aux)
    exp_point = np.array([[5.0, 5.0, 5.0] + scale * u[1]], dtype=np.float32)
    np.testing.assert_allclose(sec["point_positions"], exp_point, rtol=1e-5)
