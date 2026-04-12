from pathlib import Path

from services.model_update.analysis import sensitivity_service
from src.inp.model import Assembly, Elset, InpModel, Instance, Part


class _FakeODBClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_overview(self, odb_id):
        assert odb_id == "odb-1"
        return {
            "default_step": "Step-1",
            "instances": ["INST_A", "INST_B"],
        }

    def get_fields(self, odb_id, instance, step):
        assert odb_id == "odb-1"
        assert step == "Step-1"
        return [
            {
                "field": "d_UR_T10",
                "positions": ["INTEGRATION_POINT"],
                "components": ["C1"],
            },
            {
                "field": "S",
                "positions": ["INTEGRATION_POINT"],
                "components": ["S11"],
            },
            {
                "field": "d_UR_T20",
                "positions": ["ELEMENT_NODAL"],
                "components": ["C1"],
            },
        ]

    def get_result_label_map(self, **kwargs):
        if kwargs["field"] == "d_UR_T10":
            return {f"{kwargs['instance']}::1": 10.0}
        if kwargs["field"] == "d_UR_T20":
            return {f"{kwargs['instance']}::2": 20.0}
        if kwargs["field"] == "S":
            return {f"{kwargs['instance']}::3": 30.0}
        raise AssertionError(f"unexpected field {kwargs['field']}")


def test_export_odb_sensitivity_vtu_uses_project_inp_and_prefix(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    captured = {}
    model = InpModel(
        parts={
            "P1": Part(
                name="P1",
                elsets={
                    "SET_A": Elset(name="SET_A", elem_labels=[1]),
                    "SET_B": Elset(name="SET_B", elem_labels=[2]),
                },
            )
        },
        assembly=Assembly(instances={"INST_A": Instance(name="INST_A", part_name="P1")}),
    )

    monkeypatch.setattr(sensitivity_service, "ODBClient", _FakeODBClient)
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "parameter_name": "PARAM_A",
                "set_name": "SET_A",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
            },
            {
                "parameter_name": "PARAM_B",
                "set_name": "SET_B",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
            },
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_build_dsa_parameter_row_map",
        lambda rows, field_prefix, field_names: {
            "d_UR_T10": rows[0],
            "d_UR_T20": rows[1],
        },
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )
    monkeypatch.setattr(sensitivity_service, "parse_inp", lambda path: model)

    import tools.inp_to_vtu as inp_to_vtu

    def fake_write_vtu(inp, output, *, node_results=None, cell_results=None, apply_transforms=True):
        captured["inp"] = inp
        captured["output"] = output
        captured["node_results"] = node_results
        captured["cell_results"] = cell_results
        captured["apply_transforms"] = apply_transforms

    monkeypatch.setattr(inp_to_vtu, "write_vtu", fake_write_vtu)

    out_path = tmp_path / "sens.vtu"
    result = sensitivity_service.export_odb_sensitivity_vtu(
        project_id=1001,
        odb_id="odb-1",
        output_vtu=str(out_path),
        base_url="http://127.0.0.1:18765",
    )

    assert result["inp_path"] == str(inp_path.resolve())
    assert result["output_vtu"] == str(out_path.resolve())
    assert result["exported_fields"] == ["d_UR_T10", "d_UR_T20"]
    assert captured["node_results"] is None
    assert captured["cell_results"] == {
        "d_UR_": {"INST_A::1": 10.0, "INST_A::2": 20.0},
    }


def test_export_odb_sensitivity_vtu_supports_local_workspace_without_base_url(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    captured = {}
    model = InpModel(
        parts={
            "P1": Part(
                name="P1",
                elsets={"SET_A": Elset(name="SET_A", elem_labels=[100])},
            )
        },
        assembly=Assembly(instances={"INST_A": Instance(name="INST_A", part_name="P1")}),
    )

    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "parameter_name": "THICKNESS_PARAM",
                "set_name": "SET_A",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
            }
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_build_dsa_parameter_row_map",
        lambda rows, field_prefix, field_names: {"d_UR_T10": rows[0]},
    )
    monkeypatch.setattr(sensitivity_service, "parse_inp", lambda path: model)
    monkeypatch.setattr(
        sensitivity_service,
        "_workspace_path",
        lambda path: str(Path(path).resolve()),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_discover_sensitivity_fields_from_workspace",
        lambda workspace, **kwargs: {
            "workspace": workspace,
            "step": "Step-1",
            "instances": ["INST_A"],
            "per_instance": {
                "INST_A": [
                    {"field": "d_UR_T10", "position": "INTEGRATION_POINT", "components": []}
                ]
            },
            "field_names": ["d_UR_T10"],
        },
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_workspace_result_label_map",
        lambda workspace, **kwargs: {"INST_A::100": 3.14},
    )

    import tools.inp_to_vtu as inp_to_vtu

    def fake_write_vtu(inp, output, *, node_results=None, cell_results=None, apply_transforms=True):
        captured["inp"] = inp
        captured["output"] = output
        captured["node_results"] = node_results
        captured["cell_results"] = cell_results

    monkeypatch.setattr(inp_to_vtu, "write_vtu", fake_write_vtu)

    out_path = tmp_path / "local.vtu"
    result = sensitivity_service.export_odb_sensitivity_vtu(
        project_id=1001,
        odb_id=None,
        output_vtu=str(out_path),
        workspace=str(workspace),
        base_url=None,
    )

    assert result["source_mode"] == "workspace"
    assert result["base_url"] is None
    assert result["workspace"] == str(workspace.resolve())
    assert captured["node_results"] is None
    assert captured["cell_results"] == {"d_UR_": {"INST_A::100": 3.14}}


def test_export_odb_sensitivity_vtu_supports_registry_mode_with_only_odb_id(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    captured = {}
    model = InpModel(
        parts={
            "P1": Part(
                name="P1",
                elsets={"SET_A": Elset(name="SET_A", elem_labels=[100])},
            )
        },
        assembly=Assembly(instances={"INST_A": Instance(name="INST_A", part_name="P1")}),
    )

    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "parameter_name": "THICKNESS_PARAM",
                "set_name": "SET_A",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
            }
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_build_dsa_parameter_row_map",
        lambda rows, field_prefix, field_names: {"d_UR_T10": rows[0]},
    )
    monkeypatch.setattr(sensitivity_service, "parse_inp", lambda path: model)
    monkeypatch.setattr(
        sensitivity_service,
        "_discover_sensitivity_fields_from_registry",
        lambda odb_id, **kwargs: {
            "workspace": str(workspace.resolve()),
            "step": "Step-1",
            "instances": ["INST_A"],
            "per_instance": {
                "INST_A": [
                    {"field": "d_UR_T10", "position": "INTEGRATION_POINT", "components": []}
                ]
            },
            "field_names": ["d_UR_T10"],
        },
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_workspace_result_label_map",
        lambda workspace, **kwargs: {"INST_A::100": 6.28},
    )

    import tools.inp_to_vtu as inp_to_vtu

    def fake_write_vtu(inp, output, *, node_results=None, cell_results=None, apply_transforms=True):
        captured["cell_results"] = cell_results

    monkeypatch.setattr(inp_to_vtu, "write_vtu", fake_write_vtu)

    out_path = tmp_path / "registry.vtu"
    result = sensitivity_service.export_odb_sensitivity_vtu(
        project_id=1001,
        odb_id="odb-1",
        output_vtu=str(out_path),
        base_url=None,
    )

    assert result["source_mode"] == "registry"
    assert result["workspace"] == str(workspace.resolve())
    assert result["base_url"] is None
    assert captured["cell_results"] == {"d_UR_": {"INST_A::100": 6.28}}


def test_export_adjoint_sensitivity_vtu_uses_exact_field_name(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    captured = {}

    monkeypatch.setattr(sensitivity_service, "ODBClient", _FakeODBClient)
    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )

    import tools.inp_to_vtu as inp_to_vtu

    def fake_write_vtu(inp, output, *, node_results=None, cell_results=None, apply_transforms=True):
        captured["node_results"] = node_results
        captured["cell_results"] = cell_results

    monkeypatch.setattr(inp_to_vtu, "write_vtu", fake_write_vtu)

    out_path = tmp_path / "adjoint.vtu"
    result = sensitivity_service.export_adjoint_sensitivity_vtu(
        project_id=1001,
        odb_id="odb-1",
        output_vtu=str(out_path),
        base_url="http://127.0.0.1:18765",
        field_name="S",
    )

    assert result["field_name"] == "S"
    assert result["exported_fields"] == ["S"]
    assert captured["node_results"] is None
    assert captured["cell_results"] == {"S": {"INST_A::3": 30.0, "INST_B::3": 30.0}}


def test_build_dsa_parameter_row_map_uses_field_index_mapping():
    rows = [
        {"parameter_name": "PARAM_A"},
        {"parameter_name": "PARAM_B"},
        {"parameter_name": "PARAM_C"},
    ]

    mapping = sensitivity_service._build_dsa_parameter_row_map(rows, "d_UR_", ["d_UR_T2"])
    assert mapping["d_UR_T2"]["parameter_name"] == "PARAM_B"


def test_build_dsa_parameter_row_map_falls_back_to_field_order_when_indices_exceed_count():
    rows = [
        {"parameter_name": "PARAM_A"},
        {"parameter_name": "PARAM_B"},
    ]

    mapping = sensitivity_service._build_dsa_parameter_row_map(rows, "d_UR_", ["d_UR_T10", "d_UR_T20"])
    assert mapping["d_UR_T10"]["parameter_name"] == "PARAM_A"
    assert mapping["d_UR_T20"]["parameter_name"] == "PARAM_B"


def test_export_odb_sensitivity_vtu_rejects_overlapping_parameter_sets(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    model = InpModel(
        parts={
            "P1": Part(
                name="P1",
                elsets={
                    "SET_A": Elset(name="SET_A", elem_labels=[1]),
                    "SET_B": Elset(name="SET_B", elem_labels=[1]),
                },
            )
        },
        assembly=Assembly(instances={"INST_A": Instance(name="INST_A", part_name="P1")}),
    )

    monkeypatch.setattr(sensitivity_service, "ODBClient", _FakeODBClient)
    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "parameter_name": "PARAM_A",
                "set_name": "SET_A",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
            },
            {
                "parameter_name": "PARAM_B",
                "set_name": "SET_B",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
            },
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_build_dsa_parameter_row_map",
        lambda rows, field_prefix, field_names: {
            "d_UR_T10": rows[0],
            "d_UR_T20": rows[1],
        },
    )
    monkeypatch.setattr(sensitivity_service, "parse_inp", lambda path: model)

    try:
        sensitivity_service.export_odb_sensitivity_vtu(
            project_id=1001,
            odb_id="odb-1",
            output_vtu=str(tmp_path / "conflict.vtu"),
            base_url="http://127.0.0.1:18765",
        )
        raise AssertionError("expected overlapping parameter sets to raise ValidationError")
    except sensitivity_service.ValidationError as exc:
        assert exc.details["export_field"] == "d_UR_"
        assert exc.details["label"] == "INST_A::1"


def test_export_adjoint_sensitivity_vtu_supports_whole_element_position(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    captured = {}

    class _WholeElementClient(_FakeODBClient):
        def get_fields(self, odb_id, instance, step):
            return [
                {
                    "field": "ADJOINT_FIELD",
                    "positions": ["WHOLE_ELEMENT"],
                    "components": ["C1"],
                }
            ]

        def get_result_label_map(self, **kwargs):
            assert kwargs["position"] == "WHOLE_ELEMENT"
            assert kwargs["field"] == "ADJOINT_FIELD"
            return {f"{kwargs['instance']}::10": 42.0}

    monkeypatch.setattr(sensitivity_service, "ODBClient", _WholeElementClient)
    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )

    import tools.inp_to_vtu as inp_to_vtu

    def fake_write_vtu(inp, output, *, node_results=None, cell_results=None, apply_transforms=True):
        captured["node_results"] = node_results
        captured["cell_results"] = cell_results

    monkeypatch.setattr(inp_to_vtu, "write_vtu", fake_write_vtu)

    out_path = tmp_path / "adjoint_whole_element.vtu"
    result = sensitivity_service.export_adjoint_sensitivity_vtu(
        project_id=1001,
        odb_id="odb-1",
        output_vtu=str(out_path),
        base_url="http://127.0.0.1:18765",
        field_name="ADJOINT_FIELD",
        position="WHOLE_ELEMENT",
    )

    assert result["field_name"] == "ADJOINT_FIELD"
    assert captured["node_results"] is None
    assert captured["cell_results"] == {
        "ADJOINT_FIELD": {"INST_A::10": 42.0, "INST_B::10": 42.0}
    }


def test_export_adjoint_sensitivity_vtu_collapses_singleton_cell_values(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    captured = {}

    class _SingletonValueClient(_FakeODBClient):
        def get_fields(self, odb_id, instance, step):
            return [
                {
                    "field": "d_U2_DISTRIBUTION_THICK",
                    "positions": ["WHOLE_ELEMENT"],
                    "components": [],
                }
            ]

        def get_result_label_map(self, **kwargs):
            return {f"{kwargs['instance']}::100": [3.14]}

    monkeypatch.setattr(sensitivity_service, "ODBClient", _SingletonValueClient)
    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(inp_path),
    )

    import tools.inp_to_vtu as inp_to_vtu

    def fake_write_vtu(inp, output, *, node_results=None, cell_results=None, apply_transforms=True):
        captured["cell_results"] = cell_results

    monkeypatch.setattr(inp_to_vtu, "write_vtu", fake_write_vtu)

    out_path = tmp_path / "adjoint_singleton.vtu"
    sensitivity_service.export_adjoint_sensitivity_vtu(
        project_id=1001,
        odb_id="odb-1",
        output_vtu=str(out_path),
        base_url="http://127.0.0.1:18765",
        field_name="d_U2_DISTRIBUTION_THICK",
        position="WHOLE_ELEMENT",
    )

    assert captured["cell_results"] == {
        "d_U2_DISTRIBUTION_THICK": {"INST_A::100": 3.14, "INST_B::100": 3.14}
    }
