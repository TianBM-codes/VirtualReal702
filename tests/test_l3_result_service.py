import numpy as np

from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.infra.colormap import apply_jet
from src.l3.services.result_service import frame_colors, frame_scalars


def test_frame_colors_smooth_and_flat_respect_multi_etype_groups(
    workspace,
    make_registry,
    write_nodal_result_file,
):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    idx.source_node_rows[instance] = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    idx.render_source_elem_row[instance] = np.array([0, 0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R", b"C3D8R"], dtype="S8")

    write_nodal_result_file(
        workspace,
        instance,
        step="Step-1",
        field="U",
        data=np.array(
            [
                [
                    [0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0],
                    [10.0, 0.0, 0.0],
                    [10.0, 0.0, 0.0],
                    [10.0, 0.0, 0.0],
                ]
            ],
            dtype=np.float32,
        ),
    )

    smooth_colors, smooth_legend = frame_colors(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="U",
        frame_idx=0,
        component="U1",
        render_mode="smooth",
    )
    flat_colors, flat_legend = frame_colors(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="U",
        frame_idx=0,
        component="U1",
        render_mode="flat",
    )

    expected = apply_jet(np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0], dtype=np.float32))
    assert smooth_legend.tolist() == [0.0, 10.0]
    assert flat_legend.tolist() == [0.0, 10.0]
    assert np.array_equal(smooth_colors, expected)
    assert np.array_equal(flat_colors, expected)


def test_frame_colors_rejects_negative_frame_idx(workspace, make_registry):
    registry = make_registry(workspace)

    try:
        frame_colors(
            registry=registry,
            odb_id="odb",
            instance="PART-1-1",
            step="Step-1",
            field="U",
            frame_idx=-1,
        )
    except ValidationError as exc:
        assert "must be >= 0" in exc.message
    else:
        raise AssertionError("Expected ValidationError")


def test_frame_colors_errors_when_result_file_missing(workspace, make_registry):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.source_node_rows[instance] = np.array([[0, 1, 2]], dtype=np.int32)

    try:
        frame_colors(
            registry=registry,
            odb_id="odb",
            instance=instance,
            step="Step-1",
            field="U",
            frame_idx=0,
        )
    except NotFoundError as exc:
        assert "Result file not found" in exc.message
    else:
        raise AssertionError("Expected NotFoundError")


def test_frame_colors_rejects_frame_idx_above_num_frames(
    workspace,
    make_registry,
    write_nodal_result_file,
):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.source_node_rows[instance] = np.array([[0, 1, 2]], dtype=np.int32)
    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")

    write_nodal_result_file(
        workspace,
        instance,
        step="Step-1",
        field="U",
        data=np.array([[[1.0], [2.0], [3.0]]], dtype=np.float32),
    )

    try:
        frame_colors(
            registry=registry,
            odb_id="odb",
            instance=instance,
            step="Step-1",
            field="U",
            frame_idx=1,
            component="U1",
            render_mode="smooth",
        )
    except ValidationError as exc:
        assert "out of range" in exc.message
    else:
        raise AssertionError("Expected ValidationError")


def test_frame_scalars_maps_sparse_nodal_labels_back_to_geometry_rows(
    workspace,
    make_registry,
    write_geometry_file,
):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    write_geometry_file(
        workspace,
        instance,
        node_labels=np.array([1, 2, 3, 4], dtype=np.int32),
        element_defs={"S4R": {"labels": [10], "conn": [[0, 1, 2, 3]]}},
    )
    idx.source_node_rows[instance] = np.array([[0, 1, 3]], dtype=np.int32)

    path = workspace / "l1" / "results" / "Step-1__d_U_T_SET_149.h5"
    import h5py

    with h5py.File(path, "w") as f:
        grp = f.create_group("NODAL").create_group(instance)
        grp.create_dataset("data", data=np.array([[[42.0, -7.0, 3.0]]], dtype=np.float32))
        grp.create_dataset("labels", data=np.array([4], dtype=np.int32))

    u, legend, position = frame_scalars(
        registry=registry,
        odb_id="odb",
        instance=instance,
        step="Step-1",
        field="d_U_T_SET_149",
        frame_idx=0,
        component_idx=1,
    )

    assert position == "NODAL"
    assert legend.tolist() == [-7.0, -7.0]
    assert np.isnan(u[0]) and np.isnan(u[1])
    assert np.isfinite(u[2])
