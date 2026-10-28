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


def test_bdf_match_alias_uses_actual_geometry_and_result_instance(geometry_file, tmp_path, monkeypatch):
    nodes, edges, matches = _inputs()
    nodes = [node for node in nodes if node["nid"] != 3]
    edges = [edges[0]]
    for match in matches:
        match["instance_name"] = "BDF_MODEL"
    saved_matches = copy.deepcopy(matches)
    instance = "PROJECT_BDF_579DE83F"
    result = wireframe._build_geometry(nodes, edges, matches, {instance: geometry_file}, "disconnect")
    assert matches == saved_matches
    assert result["ids"] == [1, 2, 4]
    assert result["elementsIndex"] == [0, 1]
    assert result["diagnostics"]["unmatched_node_ids"] == []
    assert result["diagnostics"]["nearest_count"] == 0
    assert all(m["instance"] == instance and m["source"] == "matched" for m in result["mapping"])
    path = tmp_path / "results.h5"
    with h5py.File(path, "w") as f:
        f[f"NODAL/{instance}/labels"] = [200, 100]
        f[f"NODAL/{instance}/data"] = [[[2., 0., 0.], [5., 0., 0.]]]
    from src.l3.services import result_service
    monkeypatch.setattr(result_service, "_manifest_result_h5_path", lambda *a: str(path))
    values, missing = wireframe._read_selected_displacements(
        str(tmp_path), result["mapping"], "Modal", 0, None)
    np.testing.assert_array_equal(values, [[5, 0, 0], [2, 0, 0], [5, 0, 0]])
    assert missing == []


def test_instance_alias_never_guesses_between_assembly_instances(geometry_file):
    assert wireframe._geometry_instance("BDF_MODEL", {"BDF_MODEL": "exact", "OTHER": "other"}) == "BDF_MODEL"
    assert wireframe._geometry_instance("BDF_MODEL", {"A": geometry_file, "B": geometry_file}) is None
    assert wireframe._geometry_instance("UNKNOWN", {"A": geometry_file}) is None
    nodes, edges, matches = _inputs()
    for match in matches:
        match["instance_name"] = "BDF_MODEL"
    result = wireframe._build_geometry(nodes, edges, matches, {"A": geometry_file, "B": geometry_file}, "disconnect")
    assert result["ids"] == []
    assert result["diagnostics"]["unmatched_node_ids"] == [1, 2, 3, 4]


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
    from src.modal_service.routes import FemWireframeGeometryRequest, FemWireframeAnimationRequest
    from pydantic import ValidationError as RequestError
    assert FemWireframeGeometryRequest(project=574).project_id == 574
    for request in (FemWireframeGeometryRequest, FemWireframeAnimationRequest):
        assert request(project_id=574, index=2).order == 2
        assert request(project_id=574, order=3, index=2).order == 3
        with pytest.raises(RequestError):
            request(project_id=574, index=-1)
    with pytest.raises(RequestError):
        FemWireframeGeometryRequest(project_id=574, unmatched_policy="invalid")


def test_animation_preserves_scale_and_rotates_test_basis(monkeypatch):
    geometry = {"geometry_version": "v", "ids": [1], "mapping": [],
                "originPos": [2, 0, 0], "elementsIndex": [],
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
    assert result["fem"]["scaleFactor"] == 7
    assert result["fem"]["originPos"] == [2, 0, 0]
    assert result["fem"]["newPos"] == [16, 0, 0]
    assert result["test"]["scaleFactor"] == 5


def test_geometry_mode_returns_test_mesh_fields_and_selected_fem_deformation(geometry_file, monkeypatch):
    idx = type("Idx", (), {"workspace": "workspace"})()
    monkeypatch.setattr(wireframe, "_context", lambda *a: (idx, {"A": geometry_file}))
    monkeypatch.setattr(wireframe, "_sources", lambda *a: _inputs())
    def stats(project, order, step, frame, group):
        assert (project, order, step, frame, group) == (574, 2, "Modal", 3, "op2")
        return dict(available=True, reason=None, step=step, frame=frame,
                    result_group=group, frequency=42.)
    monkeypatch.setattr(wireframe.service, "_fem_modal_stats", stats)
    def displacements(workspace, mappings, step, frame, group):
        assert workspace == "workspace"
        assert [m["fem_node_label"] for m in mappings] == [100, 200, 100]
        assert (step, frame, group) == ("Modal", 3, "op2")
        return np.array([[1., 0., 0.], [0., 2., 0.], [1., 0., 0.]]), []
    monkeypatch.setattr(wireframe, "_read_selected_displacements", displacements)
    undeformed = wireframe.get_geometry(574)
    result = wireframe.get_geometry(574, order=2, step="Modal", fem_frame=3,
                                    result_group="op2", animation=True, coefficient=2., component="uy")
    scale = result["scaleFactor"]
    assert scale == pytest.approx(wireframe.service.get_box_max_scalar_size(result["originPos"]) / 4.)
    assert result["elementsIndex"] == [0, 1]
    assert result["componentData"] == [0., 2., 0.]
    assert result["minValue"] == 0.
    assert result["maxValue"] == 2.
    assert result["real"] == [1., 0., 0., 0., 2., 0., 1., 0., 0.]
    assert result["imag"] == [0.] * 9
    np.testing.assert_allclose(result["newPos"], [scale, 0., 0., 2., 2 * scale, 0., scale, 0., 0.])
    assert result["geometry_version"] == undeformed["geometry_version"]
    assert undeformed["newPos"] == []
    assert undeformed["componentData"] == [0., 0., 0.]
    assert undeformed["real"] == []
    without_basis = wireframe.get_geometry(574, order=2, step="Modal", fem_frame=3, result_group="op2")
    assert without_basis["real"] == without_basis["imag"] == []
    assert len(without_basis["newPos"]) == 9


def test_geometry_unavailable_results_and_empty_nodes(geometry_file, monkeypatch):
    monkeypatch.setattr(wireframe, "_context", lambda *a: (None, {"A": geometry_file}))
    monkeypatch.setattr(wireframe, "_sources", lambda *a: _inputs())
    monkeypatch.setattr(wireframe.service, "_fem_modal_stats", lambda *a: dict(
        available=False, reason="No results", step="Modal", frame=0, result_group=None, frequency=None))
    result = wireframe.get_geometry(574, order=1, animation=True)
    assert result["available"] is False
    assert result["newPos"] == []
    assert result["real"] == [0.] * 9
    monkeypatch.setattr(wireframe, "_sources", lambda *a: ([], [], []))
    result = wireframe.get_geometry(574, order=1)
    assert result["ids"] == result["elementsIndex"] == result["componentData"] == []


def test_geometry_route_forwards_modal_selection(monkeypatch):
    from src.modal_service.routes import FemWireframeGeometryRequest, fem_wireframe_geometry
    def geometry(project_id, policy, **kwargs):
        assert (project_id, policy) == (574, "disconnect")
        assert kwargs == dict(order=2, step="Modal", fem_frame=3, result_group="op2",
                              coefficient=2., component="ux", animation=True)
        return {"newPos": [1., 2., 3.]}
    monkeypatch.setattr(wireframe, "get_geometry", geometry)
    response = fem_wireframe_geometry(FemWireframeGeometryRequest(
        project_id=574, index=2, step="Modal", fem_frame=3, result_group="op2",
        coefficient=2., component="ux", animation=True))
    assert response["data"]["newPos"] == [1., 2., 3.]
