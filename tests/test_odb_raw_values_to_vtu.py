from pathlib import Path

import meshio
import numpy as np

from src.inp.model import Assembly, Element, InpModel, Instance, Node, Part
from tools.inp_to_vtu import _build_odb_results, write_vtu
from tools.odb_client import ODBClient, _safe_section_name


def test_safe_section_name_matches_backend_truncation():
    etype = "C3D8R-IP/very long etype name for truncation"
    name = _safe_section_name("v_", etype)

    assert name == "v_C3D8R_IP_very_long_etype_name_"
    assert len(name) == 32


def test_raw_values_to_label_map_supports_component_and_aggregation():
    client = ODBClient("http://example.invalid")

    nodal_raw = {
        "position": "NODAL",
        "instance": "PART-1-1",
        "components": ["U1", "U2", "U3"],
        "node_labels": np.array([1, 2], dtype=np.int32),
        "values": np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32),
    }
    nodal_map = client.raw_values_to_label_map(nodal_raw, component="U2", scoped=True)
    assert nodal_map == {
        "PART-1-1::1": 2.0,
        "PART-1-1::2": 5.0,
    }

    element_raw = {
        "position": "INTEGRATION_POINT",
        "instance": "PART-1-1",
        "components": ["S1", "S2"],
        "etype_groups": ["C3D8R"],
        "groups": {
            "C3D8R": {
                "elem_labels": np.array([10, 20], dtype=np.int32),
                "values": np.array(
                    [
                        [[1.0, -2.0], [3.0, -4.0]],
                        [[5.0, -6.0], [7.0, -8.0]],
                    ],
                    dtype=np.float32,
                ),
            }
        },
    }
    element_map = client.raw_values_to_label_map(
        element_raw,
        component="S2",
        aggregation="max_abs",
        scoped=True,
    )
    assert element_map == {
        "PART-1-1::10": 4.0,
        "PART-1-1::20": 8.0,
    }


def test_write_vtu_supports_scoped_node_and_cell_results(tmp_path: Path):
    part = Part(
        name="P1",
        nodes={
            1: Node(1, 0.0, 0.0, 0.0),
            2: Node(2, 1.0, 0.0, 0.0),
            3: Node(3, 0.0, 1.0, 0.0),
        },
        elements={
            1: Element(1, "CPS3", "TRI3", [1, 2, 3]),
        },
    )

    inst_a = Instance(name="INST_A", part_name="P1")
    inst_b = Instance(name="INST_B", part_name="P1")
    inst_a.transform_matrix = np.eye(4, dtype=np.float64)
    inst_b.transform_matrix = np.eye(4, dtype=np.float64)

    model = InpModel(
        parts={"P1": part},
        assembly=Assembly(
            instances={
                "INST_A": inst_a,
                "INST_B": inst_b,
            }
        ),
    )

    out_path = tmp_path / "scoped.vtu"
    write_vtu(
        model,
        str(out_path),
        node_results={"U1": {"INST_A::1": 1.0, "INST_B::1": 2.0}},
        cell_results={"SENS": {"INST_A::1": 10.0, "INST_B::1": 20.0}},
    )

    mesh = meshio.read(out_path)
    assert np.allclose(mesh.point_data["U1"][[0, 3]], [1.0, 2.0], equal_nan=True)
    assert np.isnan(mesh.point_data["U1"][1])
    assert np.isnan(mesh.point_data["U1"][4])

    triangle_block = mesh.cell_data["SENS"][0]
    assert np.allclose(triangle_block, [10.0, 20.0])


def test_write_vtu_treats_singleton_cell_values_as_scalar(tmp_path: Path):
    part = Part(
        name="P1",
        nodes={
            1: Node(1, 0.0, 0.0, 0.0),
            2: Node(2, 1.0, 0.0, 0.0),
            3: Node(3, 0.0, 1.0, 0.0),
        },
        elements={
            1: Element(1, "CPS3", "TRI3", [1, 2, 3]),
        },
    )

    inst_a = Instance(name="INST_A", part_name="P1")
    inst_b = Instance(name="INST_B", part_name="P1")
    inst_a.transform_matrix = np.eye(4, dtype=np.float64)
    inst_b.transform_matrix = np.eye(4, dtype=np.float64)

    model = InpModel(
        parts={"P1": part},
        assembly=Assembly(
            instances={
                "INST_A": inst_a,
                "INST_B": inst_b,
            }
        ),
    )

    out_path = tmp_path / "singleton_scalar.vtu"
    write_vtu(
        model,
        str(out_path),
        cell_results={"SENS": {"INST_A::1": [10.0], "INST_B::1": [20.0]}},
    )

    mesh = meshio.read(out_path)
    triangle_block = mesh.cell_data["SENS"][0]
    assert triangle_block.ndim == 1
    assert np.allclose(triangle_block, [10.0, 20.0])


def test_write_vtu_maps_root_only_inp_results_from_part_1_1_scope(tmp_path: Path):
    model = InpModel(
        parts={
            "__root__": Part(
                name="__root__",
                nodes={
                    1: Node(1, 0.0, 0.0, 0.0),
                    2: Node(2, 1.0, 0.0, 0.0),
                    3: Node(3, 0.0, 1.0, 0.0),
                },
                elements={
                    100: Element(100, "CPS3", "TRI3", [1, 2, 3]),
                },
            )
        },
        assembly=None,
    )

    out_path = tmp_path / "root_scope_alias.vtu"
    write_vtu(
        model,
        str(out_path),
        cell_results={"SENS": {"PART-1-1::100": 12.5}},
    )

    mesh = meshio.read(out_path)
    triangle_block = mesh.cell_data["SENS"][0]
    assert np.allclose(triangle_block, [12.5])


def test_build_odb_results_reads_point_and_cell_specs(monkeypatch, tmp_path: Path):
    spec_path = tmp_path / "odb_results.json"
    spec_path.write_text(
        """
{
  "base_url": "http://127.0.0.1:18765",
  "odb_id": "odb-1",
  "instance": "PART-1-1",
  "step": "Step-1",
  "results": [
    {
      "name": "U1",
      "target": "point",
      "field": "U",
      "position": "NODAL",
      "component": "U1"
    },
    {
      "name": "SENS",
      "target": "cell",
      "field": "SENS",
      "position": "INTEGRATION_POINT",
      "aggregation": "max_abs"
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )

    def fake_get_result_label_map(self, **kwargs):
        if kwargs["field"] == "U":
            return {"PART-1-1::1": 1.23}
        if kwargs["field"] == "SENS":
            assert kwargs["aggregation"] == "max_abs"
            return {"PART-1-1::10": 9.87}
        raise AssertionError(f"unexpected field: {kwargs['field']}")

    monkeypatch.setattr(ODBClient, "get_result_label_map", fake_get_result_label_map)

    node_results, cell_results = _build_odb_results(str(spec_path))

    assert node_results == {"U1": {"PART-1-1::1": 1.23}}
    assert cell_results == {"SENS": {"PART-1-1::10": 9.87}}
