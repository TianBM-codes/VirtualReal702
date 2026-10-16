import copy

import h5py
import numpy as np
import pytest

from src.modal_service import wireframe
from src.l3.core.errors import ValidationError


@pytest.fixture
def geometry_file(tmp_path):
    path = tmp_path / "geometry.h5"
    with h5py.File(path, "w") as f:
        f["nodes/labels"] = [200, 100, 300]
        f["nodes/coords_global"] = [[2., 0., 0.], [0., 0., 0.], [4., 0., 0.]]
    return str(path)


def _inputs():
    nodes = [dict(nid=i, x=x, y=0., z=0.) for i, x in [(1, 0.), (2, 2.), (3, 4.1), (4, 0.)]]
    edges = [dict(point1=1, point2=2), dict(point1=2, point2=3), dict(point1=3, point2=4)]
    matches = [dict(test_node_id=i, instance_name="A", fem_node_label=n, transform_json=None)
               for i, n in [(1, 100), (2, 200), (4, 100)]]
    return nodes, edges, matches


def test_disconnect_preserves_pairs_and_reports_duplicates(geometry_file):
    result = wireframe._build_geometry(*_inputs(), {"A": geometry_file}, "disconnect")
    assert result["ids"] == [1, 2, 4]
    assert result["elementsIndex"] == [0, 1]
    assert result["originPos"] == [0., 0., 0., 2., 0., 0., 0., 0., 0.]
    assert result["diagnostics"]["unmatched_node_ids"] == [3]
    assert result["diagnostics"]["skipped_edges"] == 2
    assert result["diagnostics"]["duplicate_mappings"][0]["test_node_ids"] == [1, 4]


def test_nearest_is_display_only_and_version_changes(geometry_file):
    inputs = _inputs()
    original = copy.deepcopy(inputs)
    result = wireframe._build_geometry(*inputs, {"A": geometry_file}, "nearest")
    assert inputs == original
    assert result["elementsIndex"] == [0, 1, 1, 2, 2, 3]
    assert result["mapping"][2]["fem_node_label"] == 300
    assert result["mapping"][2]["distance"] == pytest.approx(.1)
    assert result["diagnostics"]["nearest_count"] == 1
    other = wireframe._build_geometry(*inputs, {"A": geometry_file}, "disconnect")
    assert result["geometry_version"] != other["geometry_version"]


def test_selected_sparse_result_handles_duplicate_and_missing_nodes(tmp_path, monkeypatch):
    path = tmp_path / "results.h5"
    with h5py.File(path, "w") as f:
        f["NODAL/A/labels"] = [200, 100]
        f["NODAL/A/data"] = [[[2., 3., 4.], [5., 6., 7.]]]
    from src.l3.services import result_service
    monkeypatch.setattr(result_service, "_manifest_result_h5_path", lambda *a: str(path))
    mappings = [dict(instance="A", fem_node_label=n, node_row=0, test_node_id=i)
                for i, n in [(1, 100), (2, 200), (3, 100), (4, 300)]]
    values, missing = wireframe._read_selected_displacements(str(tmp_path), mappings, "Modal", 0, None)
    np.testing.assert_array_equal(values, [[5, 6, 7], [2, 3, 4], [5, 6, 7], [0, 0, 0]])
    assert missing == [4]


def test_version_mismatch_does_not_read_animation(monkeypatch):
    monkeypatch.setattr(wireframe, "get_geometry", lambda *a: {"geometry_version": "new"})
    with pytest.raises(ValidationError):
        wireframe.get_animation(1, 1, geometry_version="old")


def test_dense_result_reads_geometry_rows_and_preserves_order(tmp_path, monkeypatch):
    path = tmp_path / "dense.h5"
    with h5py.File(path, "w") as f:
        f["NODAL/A/data"] = [[[1., 2., 3.], [4., 5., 6.], [7., 8., 9.]]]
    from src.l3.services import result_service
    monkeypatch.setattr(result_service, "_manifest_result_h5_path", lambda *a: str(path))
    mappings = [dict(instance="A", fem_node_label=100+i, node_row=row, test_node_id=i)
                for i, row in enumerate([2, 0, 2, 8])]
    values, missing = wireframe._read_selected_displacements(str(tmp_path), mappings, "Modal", 0, None)
    np.testing.assert_array_equal(values, [[7, 8, 9], [1, 2, 3], [7, 8, 9], [0, 0, 0]])
    assert missing == [3]


def test_context_uses_global_l2_geometry(tmp_path, monkeypatch):
    path = tmp_path / "l2" / "geometry" / "A_surface.h5"
    path.parent.mkdir(parents=True)
    path.touch()
    idx = type("Idx", (), {"workspace": str(tmp_path)})()
    monkeypatch.setattr(wireframe.registry, "get", lambda *a: idx)
    manifest = type("Manifest", (), {"list_instances": lambda self: [{"instance_name": "A"}]})()
    monkeypatch.setattr(wireframe, "ManifestRepo", lambda *a: manifest)
    assert wireframe._context(1) == (idx, {"A": str(path)})


def test_request_validation_and_project_alias():
    from src.modal_service.routes import FemWireframeGeometryRequest
    from pydantic import ValidationError as RequestError
    assert FemWireframeGeometryRequest(project=574).project_id == 574
    with pytest.raises(RequestError):
        FemWireframeGeometryRequest(project_id=574, unmatched_policy="invalid")


def test_animation_preserves_scale_and_rotates_test_basis(monkeypatch):
    geometry = {"geometry_version": "v", "ids": [1], "mapping": [],
                "registration": {"translation": [10, 0, 0],
                                 "rotation": {"matrix": [[0, -1, 0], [1, 0, 0], [0, 0, 1]]}}}
    monkeypatch.setattr(wireframe, "get_geometry", lambda *a: geometry)
    monkeypatch.setattr(wireframe, "_context", lambda *a: (type("Idx", (), {"workspace": "w"})(), {}))
    sync = {"test": {"ids": [1], "originPos": [1, 0, 0], "scaleFactor": 5, "frames": []},
            "fem": {"available": True, "step": "Modal", "frame": 3, "result_group": "op2", "scale": 7},
            "sync": {"n_frames": 20}}
    def sync_animation(*args, **kwargs):
        assert kwargs["fem_frame"] == 3
        assert kwargs["include_frames"] is False
        return copy.deepcopy(sync)
    monkeypatch.setattr(wireframe.service, "get_sync_animation", sync_animation)
    monkeypatch.setattr(wireframe.service, "get_animation", lambda *a: {"real": [1, 0, 0], "imag": [0, 1, 0]})
    monkeypatch.setattr(wireframe, "_read_selected_displacements", lambda *a: (np.array([[2, 0, 0]]), []))
    result = wireframe.get_animation(1, 2, geometry_version="v", fem_frame=3)
    assert result["test"]["originPos"] == [10, 1, 0]
    assert result["test"]["real"] == [0, 1, 0]
    assert result["test"]["imag"] == [-1, 0, 0]
    assert result["fem"]["real"] == [2, 0, 0]
    assert result["fem"]["scale"] == 7
    assert result["test"]["scaleFactor"] == 5
