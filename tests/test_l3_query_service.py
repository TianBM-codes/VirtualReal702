import sqlite3
import zlib

import numpy as np

from src.l3.core.errors import ValidationError
from src.l3.services.query_service import _octree_candidates, bbox, pick


def test_pick_filters_by_etype_and_elem_row(workspace, make_registry, write_geometry_file):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    write_geometry_file(
        workspace,
        instance,
        node_labels=np.array([10, 11, 12, 13, 14, 15], dtype=np.int32),
        element_defs={
            "S4R": {"labels": [101, 102], "conn": [[0, 1, 2, -1], [0, 1, 2, -1]]},
            "C3D8R": {"labels": [201, 202], "conn": [[3, 4, 5, -1], [3, 4, 5, -1]]},
        },
    )

    idx.render_source_elem_row[instance] = np.array([1, 1, 1, 1], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R", b"S4R", b"C3D8R", b"C3D8R"], dtype="S8")

    result = pick(
        registry=registry,
        odb_id="odb",
        instance=instance,
        render_face_idx=0,
    )

    assert result.pick_mode == "element"
    assert result.odb.elem_label == 102
    assert result.odb.elem_node_labels == [10, 11, 12]
    assert result.render_face_indices == [0, 1]


def test_pick_rejects_unknown_component(workspace, make_registry, write_geometry_file, write_nodal_result_file):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    write_geometry_file(
        workspace, instance,
        node_labels=np.array([10, 11, 12], dtype=np.int32),
        element_defs={"S4R": {"labels": [101], "conn": [[0, 1, 2, -1]]}},
    )
    write_nodal_result_file(
        workspace, instance, step="Step-1", field="U",
        data=np.array([[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]]], dtype=np.float32),
    )
    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")
    idx.source_node_rows[instance] = np.array([[0, 1, 2]], dtype=np.int32)

    try:
        pick(registry=registry, odb_id="odb", instance=instance,
             render_face_idx=0, step="Step-1", field="U", frame_idx=0,
             component="S11")
    except ValidationError as exc:
        assert "S11" in exc.message
    else:
        raise AssertionError("Expected ValidationError for unknown component")


def test_pick_rejects_negative_frame_idx(workspace, make_registry, write_geometry_file, write_nodal_result_file):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    write_geometry_file(
        workspace, instance,
        node_labels=np.array([10, 11, 12], dtype=np.int32),
        element_defs={"S4R": {"labels": [101], "conn": [[0, 1, 2, -1]]}},
    )
    write_nodal_result_file(
        workspace, instance, step="Step-1", field="U",
        data=np.array([[[1.0, 0.0, 0.0]]], dtype=np.float32),
    )
    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")
    idx.source_node_rows[instance] = np.array([[0, 1, 2]], dtype=np.int32)

    try:
        pick(registry=registry, odb_id="odb", instance=instance,
             render_face_idx=0, step="Step-1", field="U", frame_idx=-1,
             component="U1")
    except ValidationError as exc:
        assert ">= 0" in exc.message
    else:
        raise AssertionError("Expected ValidationError for negative frame_idx")


def test_pick_node_mode_respects_node_idx(workspace, make_registry, write_geometry_file):
    """node_idx selects the correct candidate node, not always the first."""
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    write_geometry_file(
        workspace, instance,
        node_labels=np.array([10, 11, 12], dtype=np.int32),
        element_defs={"S4R": {"labels": [101], "conn": [[0, 1, 2, -1]]}},
    )
    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")
    idx.source_node_rows[instance] = np.array([[0, 1, 2]], dtype=np.int32)

    result0 = pick(registry=registry, odb_id="odb", instance=instance,
                   render_face_idx=0, pick_mode="node", node_idx=0)
    result2 = pick(registry=registry, odb_id="odb", instance=instance,
                   render_face_idx=0, pick_mode="node", node_idx=2)

    assert result0.odb.node_label == 10   # node row 0 → label 10
    assert result2.odb.node_label == 12   # node row 2 → label 12
    assert result0.odb.candidate_node_labels == [10, 11, 12]


def test_pick_rejects_out_of_range_face_idx(workspace, make_registry):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")
    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")

    try:
        pick(registry=registry, odb_id="odb", instance=instance, render_face_idx=1)
    except ValidationError as exc:
        assert "out of range" in exc.message
    else:
        raise AssertionError("Expected ValidationError")


def test_pick_returns_current_value_from_nodal_result(
    workspace,
    make_registry,
    write_geometry_file,
    write_nodal_result_file,
    register_result_block,
):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    write_geometry_file(
        workspace,
        instance,
        node_labels=np.array([10, 11, 12], dtype=np.int32),
        element_defs={
            "S4R": {"labels": [101], "conn": [[0, 1, 2, -1]]},
        },
    )
    result_file = write_nodal_result_file(
        workspace,
        instance,
        step="Step-1",
        field="U",
        data=np.array(
            [
                [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]],
                [[4.0, 0.0, 0.0], [5.0, 0.0, 0.0], [6.0, 0.0, 0.0]],
            ],
            dtype=np.float32,
        ),
    )
    register_result_block(
        workspace,
        step="Step-1",
        field="U",
        instance=instance,
        position="NODAL",
        file_path=result_file,
    )

    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")
    idx.source_node_rows[instance] = np.array([[1, 2, 0]], dtype=np.int32)

    result = pick(
        registry=registry,
        odb_id="odb",
        instance=instance,
        render_face_idx=0,
        step="Step-1",
        field="U",
        frame_idx=1,
        component="U1",
    )

    # face_node_rows = [1, 2, 0] → frame 1 values at those rows = [5.0, 6.0, 4.0]
    assert result.result is not None
    assert result.result.raw_values == [5.0, 6.0, 4.0]
    assert result.result.display_value == 5.0


def test_bbox_persists_unique_elem_and_etype_rows(workspace, make_registry):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    idx.coords_global[instance] = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [2.0, 0.0, 0.0],
            [3.0, 0.0, 0.0],
            [2.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    idx.source_node_rows[instance] = np.array(
        [
            [0, 1, 2],
            [0, 1, 2],
            [3, 4, 5],
            [3, 4, 5],
        ],
        dtype=np.int32,
    )
    idx.render_source_elem_row[instance] = np.array([1, 1, 1, 1], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R", b"S4R", b"C3D8R", b"C3D8R"], dtype="S8")

    result = bbox(
        registry=registry,
        odb_id="odb",
        instance=instance,
        bbox_min=[-1.0, -1.0, -1.0],
        bbox_max=[4.0, 2.0, 1.0],
        mode="intersect",
        set_name="picked",
    )

    assert result.elem_count == 2
    assert result.render_face_count == 4

    conn = sqlite3.connect(workspace / "manifest.db")
    row = conn.execute(
        "SELECT elem_count, render_rows, elem_rows, etype_rows FROM user_set_instances WHERE instance_name=?",
        (instance,),
    ).fetchone()
    conn.close()

    assert row[0] == 2
    render_rows = np.frombuffer(zlib.decompress(row[1]), dtype=np.int32)
    elem_rows = np.frombuffer(zlib.decompress(row[2]), dtype=np.int32)
    etype_rows = np.frombuffer(zlib.decompress(row[3]), dtype="S8")

    assert render_rows.tolist() == [0, 1, 2, 3]
    assert elem_rows.tolist() == [1, 1]
    # np.unique sorts by composite key: C3D8R gets et_idx=0 → key=1, S4R gets et_idx=1 → key=3
    # so C3D8R comes first in the unique-sorted output
    assert etype_rows.tolist() == [b"C3D8R", b"S4R"]


def test_octree_candidates_returns_only_overlapping_leaves():
    # 3-node octree: root (internal) → leaf A covers x∈[0,1], leaf B covers x∈[2,3]
    octree = {
        "node_bbox": np.array([
            [0, 0, 0, 3, 1, 1],   # root
            [0, 0, 0, 1, 1, 1],   # leaf A  → face_indices[0:1] = [0]
            [2, 0, 0, 3, 1, 1],   # leaf B  → face_indices[1:2] = [1]
        ], dtype=np.float32),
        "node_children": np.array([
            [1, 2, -1, -1, -1, -1, -1, -1],
            [-1, -1, -1, -1, -1, -1, -1, -1],
            [-1, -1, -1, -1, -1, -1, -1, -1],
        ], dtype=np.int32),
        "node_is_leaf": np.array([0, 1, 1], dtype=np.uint8),
        "face_indices": np.array([0, 1], dtype=np.int32),
        "leaf_offsets": np.array([0, 0, 1, 2], dtype=np.int32),
    }

    lo = np.array([-0.5, -0.5, -0.5], dtype=np.float32)
    hi = np.array([1.5,  1.5,  1.5], dtype=np.float32)
    assert set(_octree_candidates(octree, lo, hi).tolist()) == {0}

    lo2 = np.array([-0.5, -0.5, -0.5], dtype=np.float32)
    hi2 = np.array([3.5,  1.5,  1.5], dtype=np.float32)
    assert set(_octree_candidates(octree, lo2, hi2).tolist()) == {0, 1}

    lo3 = np.array([5.0, 5.0, 5.0], dtype=np.float32)
    hi3 = np.array([6.0, 6.0, 6.0], dtype=np.float32)
    assert len(_octree_candidates(octree, lo3, hi3)) == 0


def test_bbox_octree_excludes_distant_faces(workspace, make_registry):
    # Left cluster at x~0, right cluster at x~100; query only covers left
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    idx.coords_global[instance] = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
        [100.0, 0.0, 0.0], [101.0, 0.0, 0.0], [100.0, 1.0, 0.0],
    ], dtype=np.float32)
    idx.source_node_rows[instance] = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    idx.render_source_elem_row[instance] = np.array([0, 1], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R", b"S4R"], dtype="S8")
    idx.octree[instance] = {
        "node_bbox": np.array([
            [0, 0, 0, 101, 1, 1],
            [0, 0, 0, 1, 1, 1],
            [100, 0, 0, 101, 1, 1],
        ], dtype=np.float32),
        "node_children": np.array([
            [1, 2, -1, -1, -1, -1, -1, -1],
            [-1, -1, -1, -1, -1, -1, -1, -1],
            [-1, -1, -1, -1, -1, -1, -1, -1],
        ], dtype=np.int32),
        "node_is_leaf": np.array([0, 1, 1], dtype=np.uint8),
        "face_indices": np.array([0, 1], dtype=np.int32),
        "leaf_offsets": np.array([0, 0, 1, 2], dtype=np.int32),
    }

    result = bbox(
        registry=registry,
        odb_id="odb",
        instance=instance,
        bbox_min=[-1.0, -1.0, -1.0],
        bbox_max=[2.0, 2.0, 2.0],
        mode="intersect",
    )

    assert result.elem_count == 1
    assert result.render_face_count == 1


def test_bbox_returns_empty_counts_when_nothing_matches(workspace, make_registry):
    instance = "PART-1-1"
    registry = make_registry(workspace, instance=instance)
    idx = registry.get("odb")

    idx.coords_global[instance] = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    idx.source_node_rows[instance] = np.array([[0, 1, 2]], dtype=np.int32)
    idx.render_source_elem_row[instance] = np.array([0], dtype=np.int32)
    idx.source_elem_etype[instance] = np.array([b"S4R"], dtype="S8")

    result = bbox(
        registry=registry,
        odb_id="odb",
        instance=instance,
        bbox_min=[10.0, 10.0, 10.0],
        bbox_max=[11.0, 11.0, 11.0],
        mode="intersect",
    )

    assert result.set_name is None
    assert result.elem_count == 0
    assert result.render_face_count == 0
