"""First-open geometry must reuse resident buffers without changing payloads."""
import asyncio
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from src.l3.api.routes import geometry as G


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("filtered", [False, True])
def test_resident_geometry_matches_disk_and_preserves_shared_arrays(tmp_path, monkeypatch, chunked, filtered):
    instance = "PART"
    render_path = tmp_path / "l2/render/PART_render.h5"
    render_path.parent.mkdir(parents=True)
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
    indices = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    with h5py.File(render_path, "w") as f:
        f["render/positions"] = positions
        f["render/indices"] = indices
    idx = SimpleNamespace(workspace=str(tmp_path), render_positions={}, render_indices={})
    monkeypatch.setattr(G.registry, "get", lambda _: idx)
    monkeypatch.setattr(G, "ManifestRepo", lambda _: SimpleNamespace(
        get_user_set_render_rows=lambda *a: np.array([1], dtype=np.int32)))

    def call():
        kwargs = {"odb_id": "bench", "instance": instance,
                  "set_name": "selected" if filtered else None}
        if chunked:
            return asyncio.run(G.get_render_buffers_chunked(**kwargs, chunk_size=1))
        return asyncio.run(G.get_render_buffers(**kwargs))

    expected = call()
    positions.setflags(write=False)
    indices.setflags(write=False)
    idx.render_positions[instance] = positions
    idx.render_indices[instance] = indices
    monkeypatch.setattr(G.h5py, "File", lambda *a, **k: pytest.fail("reread resident render geometry"))
    actual = call()
    assert actual.body == expected.body
    assert actual.headers["X-Face-Count"] == ("1" if filtered else "2")
    np.testing.assert_array_equal(positions, [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    np.testing.assert_array_equal(indices, [[0, 1, 2], [0, 2, 3]])


def test_feature_edges_reuse_resident_coordinates_with_old_and_new_layouts(tmp_path, monkeypatch):
    geom = tmp_path / "l2/geometry"
    geom.mkdir(parents=True)
    surface = geom / "PART_surface.h5"
    coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    with h5py.File(surface, "w") as f:
        f["nodes/coords_global"] = coords
        f["feature_edges/edge_nodes"] = np.array([[0, 1], [1, 2]], dtype=np.int32)
    idx = SimpleNamespace(workspace=str(tmp_path), coords_global={})
    monkeypatch.setattr(G.registry, "get", lambda _: idx)
    expected = asyncio.run(G.get_feature_edges("bench", "PART"))
    coords.setflags(write=False)
    idx.coords_global["PART"] = coords
    with h5py.File(surface, "a") as f:
        del f["nodes/coords_global"]  # A second coordinate read would now fail.
    for separate_edges in (False, True):
        if separate_edges:
            with h5py.File(geom / "PART_edges.h5", "w") as f:
                f["feature_edges/edge_nodes"] = np.array([[0, 1], [1, 2]], dtype=np.int32)
        actual = asyncio.run(G.get_feature_edges("bench", "PART"))
        assert actual.body == expected.body
        assert actual.headers["X-Edge-Count"] == "2"


def test_missing_resident_indices_retains_legacy_soup_fallback(tmp_path):
    render = tmp_path / "render.h5"
    positions = np.zeros((3, 3), dtype=np.float32)
    with h5py.File(render, "w") as f:
        f["render/positions"] = positions
    idx = SimpleNamespace(render_positions={"PART": positions}, render_indices={})
    result_positions, result_indices = G._read_render_geometry(idx, "PART", str(render))
    assert result_positions is positions
    assert result_indices is None


@pytest.mark.parametrize("indices", [
    [[4, 5, 6], [4, 6, 7]],           # Dense offset interval: subtraction path.
    [[7, 4, 6], [6, 5, 7]],           # Dense but shuffled: preserve input order.
    [[0, 2, 3], [0, 3, 5]],           # Gaps: must retain sorted-unique mapping.
    [[0, 100, 200], [0, 200, 300]],   # Sparse interval: avoid a large histogram.
])
@pytest.mark.parametrize("chunk_size", [1, 2])
def test_chunk_mapping_matches_sorted_unique_for_dense_and_sparse_ids(indices, chunk_size):
    indices = np.asarray(indices, dtype=np.int32)
    positions = np.arange(301 * 3, dtype=np.float32).reshape(301, 3)
    chunks = G._build_render_chunks(positions, indices, chunk_size)
    for k, chunk in enumerate(chunks):
        original = indices[k * chunk_size:(k + 1) * chunk_size]
        expected_ids, inverse = np.unique(original.ravel(), return_inverse=True)
        np.testing.assert_array_equal(chunk["vertex_global_id"], expected_ids)
        np.testing.assert_array_equal(chunk["indices"], inverse.reshape(-1, 3))
        np.testing.assert_array_equal(chunk["positions"], positions[expected_ids])
        np.testing.assert_array_equal(
            chunk["positions"][chunk["indices"]], positions[original])
