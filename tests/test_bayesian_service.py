from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from services.model_update.analysis import bayesian_service


@pytest.fixture(autouse=True)
def _stub_project_status_update(monkeypatch):
    monkeypatch.setattr(
        bayesian_service,
        "update_work_condition_project_status",
        lambda *args, **kwargs: None,
    )


def test_update_parameter_section_values_rewrites_parameter_block(tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
P2=2.0, P3=3.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    result = bayesian_service.update_parameter_section_values(
        str(inp_path),
        {"P1": 1.5, "P3": 4.25},
        output_inp=str(tmp_path / "updated.inp"),
    )

    text = Path(result["output_inp"]).read_text(encoding="utf-8")
    assert "P1=1.5" in text
    assert "P2=2.0,P3=4.25" in text


def test_bayesian_update_normalized_uses_normalized_residual_and_gain_matrix():
    result = bayesian_service.bayesian_update_normalized(
        p_current=np.array([2.0, 4.0]),
        r_model=np.array([10.0, 20.0]),
        r_target=np.array([8.0, 10.0]),
        S_norm=np.array([[0.5, 0.25], [0.1, 0.3]]),
        p_scatter=np.array([0.25]),
        r_scatter=np.array([0.01]),
        damping=1e-12,
        p_ref=np.array([2.0, 4.0]),
    )

    expected_y = np.array([[0.2], [0.5]])
    expected_p_scatter = np.array([0.25, 0.25])
    expected_r_scatter = np.array([0.01, 0.01])
    expected_cp_n = 2.0 * np.diag(1.0 / expected_p_scatter**2)
    expected_cr_n = np.diag(1.0 / expected_r_scatter**2)
    expected_cp_n_eff = expected_cp_n + 1e-12 * np.eye(2)
    expected_cp_n_inv = np.linalg.inv(expected_cp_n_eff)
    expected_cr_n_inv = np.linalg.inv(expected_cr_n)
    expected_innovation_cov = expected_cr_n_inv + np.array([[0.5, 0.25], [0.1, 0.3]]) @ expected_cp_n_inv @ np.array([[0.5, 0.1], [0.25, 0.3]])
    expected_g_n = expected_cp_n_inv @ np.array([[0.5, 0.1], [0.25, 0.3]]) @ np.linalg.inv(expected_innovation_cov)

    assert np.allclose(result["y"], expected_y)
    assert np.allclose(result["normalized_parameter_scatter"], expected_p_scatter)
    assert np.allclose(result["normalized_response_scatter"], expected_r_scatter)
    assert np.allclose(result["Cp_n"], expected_cp_n)
    assert np.allclose(result["Cr_n"], expected_cr_n)
    assert np.allclose(result["Cp_n_eff"], expected_cp_n_eff)
    assert np.allclose(result["innovation_cov"], expected_innovation_cov)
    assert np.allclose(result["G_n"], expected_g_n)


def test_build_iteration_metrics_uses_ccabs_style_summary():
    result = bayesian_service._build_iteration_metrics(
        response_values=[10.0, 20.0],
        target_values=[8.0, 16.0],
        response_scatter=[0.01, 0.02],
        parameter_step=[0.1, -0.2],
    )

    assert np.isclose(result["ccabs"], 37.5)
    assert np.isclose(result["ccmean"], 37.5)
    assert np.isclose(result["ccdisp"], 0.25)
    assert np.isclose(result["ccdis"], 0.25)
    assert np.isclose(result["cctotal"], 37.75)
    assert np.isclose(result["cctot"], 37.75)
    assert np.isclose(result["ccdsf"], 0.0)
    assert np.isclose(result["rel_res"], 0.25)
    assert np.isclose(result["ra_norm"], np.linalg.norm([10.0, 20.0]))
    assert np.isclose(result["re_norm"], np.linalg.norm([8.0, 16.0]))
    assert np.isclose(result["dr_norm"], np.linalg.norm([-2.0, -4.0]))
    assert np.isclose(result["dx_norm"], np.linalg.norm([0.1, -0.2]))
    assert np.isclose(result["max_abs_dparam"], 0.2)
    assert np.isclose(result["mean_abs_response_diff"], 25.0)
    assert np.isclose(result["max_abs_response_diff"], 25.0)


def test_build_dsa_normalized_sensitivity_matrix_uses_normalized_component_values(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "fake.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    model = SimpleNamespace(
        parts={},
        assembly=None,
        parameters={
            "T1": SimpleNamespace(scalar_value=2.0),
            "T2": SimpleNamespace(scalar_value=5.0),
        },
        design_parameters=[
            SimpleNamespace(name="T1", order=1),
            SimpleNamespace(name="T2", order=2),
        ],
        design_responses=[
            SimpleNamespace(
                step_name="Step-1",
                frequency=1,
                requests=[
                    SimpleNamespace(region_type="NODE", set_name="NSET4", variables=["UY"]),
                ],
            )
        ],
    )

    monkeypatch.setattr(bayesian_service, "parse_inp", lambda path: model)
    monkeypatch.setattr(bayesian_service._sens, "_resolve_inp_path_from_project", lambda project_id: str(inp_path))
    monkeypatch.setattr(bayesian_service._sens, "_workspace_path", lambda path: str(Path(path).resolve()))
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_workspace_step_frame",
        lambda workspace, *, step, requested_frame: 0 if requested_frame is None else int(requested_frame),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_discover_sensitivity_fields_from_workspace",
        lambda workspace, **kwargs: {
            "workspace": workspace,
            "step": "Step-1",
            "instances": ["INST"],
            "per_instance": {
                "INST": [
                    {"field": "d_U_T1", "position": "NODAL", "components": []},
                    {"field": "d_U_T2", "position": "NODAL", "components": []},
                ]
            },
            "field_names": ["d_U_T1", "d_U_T2"],
        },
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {"parameter_name": "T1", "set_name": "E1", "set_type": "ELSET", "set_scope": "PART", "scalar_value": 2.0},
            {"parameter_name": "T2", "set_name": "E2", "set_type": "ELSET", "set_scope": "PART", "scalar_value": 5.0},
        ],
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_response_field_meta",
        lambda **kwargs: {"field": "U", "components": ["U1", "U2", "U3"], "positions": ["NODAL"]},
    )
    monkeypatch.setattr(bayesian_service._sens, "_pick_response_position", lambda field_meta, preferred: "NODAL")
    monkeypatch.setattr(
        bayesian_service._sens,
        "_design_response_target_labels",
        lambda model, spec: ("point", ["INST::10", "INST::20"]),
    )

    def fake_label_map(workspace, *, step, field, instance, position, frame, aggregation, component=None, component_index=None, result_group=None):
        if field == "d_U_T1" and component == "U2":
            return {"INST::10": 1.0, "INST::20": 2.0}
        if field == "d_U_T2" and component == "U2":
            return {"INST::10": 3.0, "INST::20": 4.0}
        if field == "U" and component == "U2":
            return {"INST::10": 10.0, "INST::20": 20.0}
        return {}

    monkeypatch.setattr(bayesian_service._sens, "_workspace_result_label_map", fake_label_map)

    result = bayesian_service.build_dsa_normalized_sensitivity_matrix(
        project_id=1,
        inp_path=str(inp_path),
        workspace=str(workspace),
        field_prefix="d_U_",
    )

    assert result["matrix"] == [[0.2, 1.5], [0.2, 1.0]]
    assert result["parameter_values"] == [2.0, 5.0]
    assert result["response_values"] == [10.0, 20.0]
    assert [item["parameter_name"] for item in result["parameter_columns"]] == ["T1", "T2"]
    assert [item["response_component"] for item in result["response_rows"]] == ["U2", "U2"]


def test_build_dsa_normalized_sensitivity_matrix_uses_explicit_response_component(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "fake.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    model = SimpleNamespace(
        parts={},
        assembly=None,
        parameters={
            "T1": SimpleNamespace(scalar_value=2.0),
        },
        design_parameters=[
            SimpleNamespace(name="T1", order=1),
        ],
        design_responses=[
            SimpleNamespace(
                step_name="Step-1",
                frequency=1,
                requests=[
                    SimpleNamespace(region_type="NODE", set_name="NSET4", variables=["U"]),
                ],
            )
        ],
    )

    monkeypatch.setattr(bayesian_service, "parse_inp", lambda path: model)
    monkeypatch.setattr(bayesian_service._sens, "_resolve_inp_path_from_project", lambda project_id: str(inp_path))
    monkeypatch.setattr(bayesian_service._sens, "_workspace_path", lambda path: str(Path(path).resolve()))
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_workspace_step_frame",
        lambda workspace, *, step, requested_frame: 0 if requested_frame is None else int(requested_frame),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_discover_sensitivity_fields_from_workspace",
        lambda workspace, **kwargs: {
            "workspace": workspace,
            "step": "Step-1",
            "instances": ["INST"],
            "per_instance": {
                "INST": [
                    {"field": "d_UR_T1", "position": "NODAL", "components": []},
                ]
            },
            "field_names": ["d_UR_T1"],
        },
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {"parameter_name": "T1", "set_name": "E1", "set_type": "ELSET", "set_scope": "PART", "scalar_value": 2.0},
        ],
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_response_field_meta",
        lambda **kwargs: {"field": "U", "components": ["U1", "U2", "U3"], "positions": ["NODAL"]},
    )
    monkeypatch.setattr(bayesian_service._sens, "_pick_response_position", lambda field_meta, preferred: "NODAL")
    monkeypatch.setattr(
        bayesian_service._sens,
        "_design_response_target_labels",
        lambda model, spec: ("point", ["INST::10"]),
    )

    def fake_label_map(workspace, *, step, field, instance, position, frame, aggregation, component=None, component_index=None, result_group=None):
        if field == "d_UR_T1":
            assert component == "U2"
            return {"INST::10": 1.0}
        if field == "U":
            assert component == "U2"
            return {"INST::10": 10.0}
        return {}

    monkeypatch.setattr(bayesian_service._sens, "_workspace_result_label_map", fake_label_map)

    result = bayesian_service.build_dsa_normalized_sensitivity_matrix(
        project_id=1,
        inp_path=str(inp_path),
        workspace=str(workspace),
        field_prefix="d_UR_",
        response_component="UY",
    )

    assert result["response_component"] == "U2"
    assert result["matrix"] == [[0.2]]
    assert [item["response_component"] for item in result["response_rows"]] == ["U2"]


def test_parse_design_response_variable_treats_mises_as_stress_invariant():
    parsed = bayesian_service._sens._parse_design_response_variable("MISES")

    assert parsed == {
        "variable": "MISES",
        "field_name": "S",
        "component": "MISES",
        "component_index": None,
    }


def test_build_dsa_normalized_sensitivity_matrix_uses_node_mises_from_element_nodal(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "fake.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    model = SimpleNamespace(
        parts={
            "P1": SimpleNamespace(
                elements={
                    10: SimpleNamespace(label=10, node_labels=[4, 5, 6, 7]),
                    20: SimpleNamespace(label=20, node_labels=[1, 4, 8, 9]),
                }
            )
        },
        assembly=SimpleNamespace(instances={"INST": SimpleNamespace(name="INST", part_name="P1")}),
        parameters={"T1": SimpleNamespace(scalar_value=2.0)},
        design_parameters=[SimpleNamespace(name="T1", order=1)],
        design_responses=[
            SimpleNamespace(
                step_name="Step-1",
                frequency=1,
                requests=[
                    SimpleNamespace(region_type="NODE", set_name="NSET4", variables=["MISES"]),
                ],
            )
        ],
    )

    monkeypatch.setattr(bayesian_service, "parse_inp", lambda path: model)
    monkeypatch.setattr(bayesian_service._sens, "_resolve_inp_path_from_project", lambda project_id: str(inp_path))
    monkeypatch.setattr(bayesian_service._sens, "_workspace_path", lambda path: str(Path(path).resolve()))
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_workspace_step_frame",
        lambda workspace, *, step, requested_frame: 0 if requested_frame is None else int(requested_frame),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_discover_sensitivity_fields_from_workspace",
        lambda workspace, **kwargs: {
            "workspace": workspace,
            "step": "Step-1",
            "instances": ["INST"],
            "per_instance": {
                "INST": [
                    {"field": "d_MISES_T1", "position": "ELEMENT_NODAL", "components": []},
                ]
            },
            "field_names": ["d_MISES_T1"],
        },
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {"parameter_name": "T1", "set_name": "E1", "set_type": "ELSET", "set_scope": "PART", "scalar_value": 2.0},
        ],
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_response_field_meta",
        lambda **kwargs: {"field": "S", "components": ["S11", "S22", "S33", "S12"], "positions": ["ELEMENT_NODAL"]},
    )
    monkeypatch.setattr(bayesian_service._sens, "_pick_response_position", lambda field_meta, preferred: "ELEMENT_NODAL")
    monkeypatch.setattr(
        bayesian_service._sens,
        "_design_response_target_labels",
        lambda model, spec: ("point", ["INST::4"]),
    )

    def fake_node_label_map(workspace, *, inp_model, step, field, instance, frame, aggregation, node_labels,
                            component=None, component_index=None, result_group=None):
        assert node_labels == [4]
        if field == "d_MISES_T1":
            assert component is None
            return {"INST::4": 3.0}
        if field == "S":
            assert component == "MISES"
            return {"INST::4": 12.0}
        raise AssertionError(f"unexpected field {field}")

    monkeypatch.setattr(
        bayesian_service._sens,
        "_workspace_element_nodal_node_label_map",
        fake_node_label_map,
    )

    result = bayesian_service.build_dsa_normalized_sensitivity_matrix(
        project_id=1,
        inp_path=str(inp_path),
        workspace=str(workspace),
        field_prefix="d_MISES_",
        aggregation="max",
    )

    assert result["matrix"] == [[0.5]]
    assert result["parameter_values"] == [2.0]
    assert result["response_values"] == [12.0]
    assert result["response_rows"][0]["response_component"] == "MISES"


def test_build_dsa_normalized_sensitivity_matrix_uses_in_memory_optimization_parameter_rows(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "fake.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    model = SimpleNamespace(
        parts={},
        assembly=None,
        parameters={},
        design_parameters=[],
        design_responses=[
            SimpleNamespace(
                step_name="Step-1",
                frequency=1,
                requests=[
                    SimpleNamespace(region_type="NODE", set_name="NSET4", variables=["UY"]),
                ],
            )
        ],
    )

    monkeypatch.setattr(bayesian_service, "parse_inp", lambda path: model)
    monkeypatch.setattr(bayesian_service._sens, "_resolve_inp_path_from_project", lambda project_id: str(inp_path))
    monkeypatch.setattr(bayesian_service._sens, "_workspace_path", lambda path: str(Path(path).resolve()))
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_workspace_step_frame",
        lambda workspace, *, step, requested_frame: 0 if requested_frame is None else int(requested_frame),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_discover_sensitivity_fields_from_workspace",
        lambda workspace, **kwargs: {
            "workspace": workspace,
            "step": "Step-1",
            "instances": ["INST"],
            "per_instance": {
                "INST": [
                    {"field": "d_U_1", "position": "NODAL", "components": []},
                ]
            },
            "field_names": ["d_U_1"],
        },
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_load_project_optimization_parameters",
        lambda project_id: (_ for _ in ()).throw(AssertionError("db optimization rows should not be loaded")),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_response_field_meta",
        lambda **kwargs: {"field": "U", "components": ["U1", "U2", "U3"], "positions": ["NODAL"]},
    )
    monkeypatch.setattr(bayesian_service._sens, "_pick_response_position", lambda field_meta, preferred: "NODAL")
    monkeypatch.setattr(
        bayesian_service._sens,
        "_design_response_target_labels",
        lambda model, spec: ("point", ["INST::10"]),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_design_response_target_labels",
        lambda model, spec: ("point", ["INST::10", "INST::20"]),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_resolve_dsa_parameter_scalar_value",
        lambda model, *, parameter_name, target_rows: float(target_rows[0]["scalar_value"]),
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "_design_response_target_labels",
        lambda model, spec: ("point", ["INST::10"]),
    )

    def fake_label_map(workspace, *, step, field, instance, position, frame, aggregation, component=None, component_index=None, result_group=None):
        if field == "d_U_1" and component == "U2":
            return {"INST::10": 1.0}
        if field == "U" and component == "U2":
            return {"INST::10": 10.0}
        return {}

    monkeypatch.setattr(bayesian_service._sens, "_workspace_result_label_map", fake_label_map)

    result = bayesian_service.build_dsa_normalized_sensitivity_matrix(
        project_id=1,
        inp_path=str(inp_path),
        workspace=str(workspace),
        field_prefix="d_U_",
        optimization_parameter_rows=[
            {
                "parameter_name": "T1",
                "set_name": "SET_A",
                "set_type": "ELSET",
                "set_scope": "PART",
                "scalar_value": 2.0,
                "scatter": 0.15,
            }
        ],
    )

    assert result["matrix"] == [[0.2]]
    assert result["parameter_values"] == [2.0]
    assert result["parameter_columns"][0]["parameter_name"] == "T1"
    assert result["parameter_columns"][0]["scatter"] == 0.15


def test_run_iteration_solver_deletes_abaqus_process_files_after_workspace_build(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model_iter0.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    odb_path = tmp_path / "demo.odb"
    odb_path.write_text("odb", encoding="utf-8")

    deleted_suffixes = [".com", ".prt", ".pmg", ".pes", ".par", ".msg", ".sta", ".dat"]
    for suffix in deleted_suffixes:
        (tmp_path / f"demo{suffix}").write_text("tmp", encoding="utf-8")

    monkeypatch.setattr(bayesian_service._solver, "_sanitize_job_name", lambda name: "demo")
    monkeypatch.setattr(
        bayesian_service._solver,
        "_build_abaqus_command",
        lambda **kwargs: ["abaqus", "job=demo", "input=model_iter0.inp"],
    )
    monkeypatch.setattr(
        bayesian_service._solver,
        "_run_local_solver",
        lambda **kwargs: {
            "ok": True,
            "artifacts": {"odb": str(odb_path)},
            "workdir": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        bayesian_service._sens,
        "build_workspace_from_odb",
        lambda **kwargs: {"workspace": str(tmp_path / "workspace_iter0")},
    )

    result = bayesian_service._run_iteration_solver(
        inp_path=inp_path,
        output_dir=tmp_path,
        iteration=0,
        abaqus="abaqus",
        job_name="demo",
        cpus=None,
        interactive=True,
        timeout_sec=None,
        extra_args=None,
        python3=None,
        keep_raw=False,
    )

    assert Path(result["solver"]["artifacts"]["odb"]).exists()
    for suffix in deleted_suffixes:
        assert not (tmp_path / f"demo{suffix}").exists()
    assert sorted(Path(path).suffix for path in result["solver"]["deleted_process_files"]) == sorted(deleted_suffixes)


def test_run_bayesian_update_workflow_rewrites_inp_across_iterations(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
P2=2.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    matrix_payloads = [
        {
            "workspace": str(tmp_path / "initial_ws"),
            "source_mode": "workspace",
            "workspace_built": False,
            "odb_id": None,
            "matrix": [[1.0, 0.0], [0.0, 1.0]],
            "response_values": [10.0, 20.0],
            "parameter_values": [1.0, 2.0],
            "parameter_columns": [
                {
                    "field": "d_UR_P1",
                    "parameter_name": "P1",
                    "parameter_token": "P1",
                    "parameter_value": 1.0,
                    "element_mapping": {
                        "field": "d_UR_P1",
                        "parameter_name": "P1",
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [101, 102]},
                    },
                },
                {
                    "field": "d_UR_P2",
                    "parameter_name": "P2",
                    "parameter_token": "P2",
                    "parameter_value": 2.0,
                    "element_mapping": {
                        "field": "d_UR_P2",
                        "parameter_name": "P2",
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [201]},
                    },
                },
            ],
            "response_rows": [
                {"row_key": "r1", "response_label": "INST::10"},
                {"row_key": "r2", "response_label": "INST::20"},
            ],
        },
        {
            "workspace": str(tmp_path / "rerun_ws"),
            "source_mode": "workspace",
            "workspace_built": False,
            "odb_id": None,
            "matrix": [[2.0, 0.0], [0.0, 2.0]],
            "response_values": [9.0, 18.0],
            "parameter_values": [1.1, 2.2],
            "parameter_columns": [
                {
                    "field": "d_UR_P1",
                    "parameter_name": "P1",
                    "parameter_token": "P1",
                    "parameter_value": 1.1,
                    "element_mapping": {
                        "field": "d_UR_P1",
                        "parameter_name": "P1",
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [101, 102]},
                    },
                },
                {
                    "field": "d_UR_P2",
                    "parameter_name": "P2",
                    "parameter_token": "P2",
                    "parameter_value": 2.2,
                    "element_mapping": {
                        "field": "d_UR_P2",
                        "parameter_name": "P2",
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [201]},
                    },
                },
            ],
            "response_rows": [
                {"row_key": "r1", "response_label": "INST::10"},
                {"row_key": "r2", "response_label": "INST::20"},
            ],
        },
    ]
    matrix_calls = []

    def fake_build_matrix(**kwargs):
        matrix_calls.append(kwargs)
        return matrix_payloads[len(matrix_calls) - 1]

    solver_calls = []

    def fake_run_iteration_solver(**kwargs):
        solver_calls.append(kwargs)
        return {
            "job_name": "demo",
            "command_preview": ["abaqus"],
            "solver": {"ok": True, "artifacts": {"odb": str(tmp_path / "job.odb")}},
            "workspace": {"workspace": str(tmp_path / "rerun_ws")},
        }

    updates = [
        {
            "delta_r": np.array([[1.0], [2.0]]),
            "y": np.array([[10.0], [10.0]]),
            "x": np.array([[0.1], [0.1]]),
            "dp": np.array([[0.1], [0.2]]),
            "p_new": np.array([1.1, 2.2]),
            "G_n": np.eye(2),
        },
        {
            "delta_r": np.array([[1.0], [2.0]]),
            "y": np.array([[11.0], [11.0]]),
            "x": np.array([[0.2], [0.3]]),
            "dp": np.array([[0.2], [0.4]]),
            "p_new": np.array([1.3, 2.6]),
            "G_n": np.eye(2),
        },
    ]
    update_calls = []

    def fake_bayesian_update(**kwargs):
        update_calls.append(kwargs)
        return updates[len(update_calls) - 1]

    persisted_calls = []

    def fake_persist(**kwargs):
        snapshot = dict(kwargs)
        snapshot["iteration_results"] = list(kwargs.get("iteration_results") or [])
        persisted_calls.append(snapshot)

    monkeypatch.setattr(bayesian_service, "build_dsa_normalized_sensitivity_matrix", fake_build_matrix)
    monkeypatch.setattr(bayesian_service, "_run_iteration_solver", fake_run_iteration_solver)
    monkeypatch.setattr(bayesian_service, "bayesian_update_normalized", fake_bayesian_update)
    monkeypatch.setattr(bayesian_service, "_persist_bayesian_tracking_results", fake_persist)

    result = bayesian_service.run_bayesian_update_workflow(
        project_id=1,
        batch_no=3,
        input_inp=str(inp_path),
        workspace=str(tmp_path / "initial_ws"),
        target_responses=[8.0, 16.0],
        parameter_scatter={"P1": 0.25, "P2": 0.3},
        response_scatter={"r1": 0.01, "r2": 0.02},
        output_dir=str(tmp_path / "out"),
        iterations=2,
        run_solver=True,
    )

    assert len(matrix_calls) == 2
    assert len(solver_calls) == 1
    assert len(update_calls) == 2
    assert result["batch_no"] == 3
    assert result["final_parameter_values"] == [1.3, 2.6]
    assert len(persisted_calls) == 2
    assert persisted_calls[0]["project_id"] == 1
    assert persisted_calls[0]["batch_no"] == 3
    assert len(persisted_calls[0]["iteration_results"]) == 1
    assert len(persisted_calls[1]["iteration_results"]) == 2

    final_text = Path(result["final_updated_inp"]).read_text(encoding="utf-8")
    assert "P1=1.3" in final_text
    assert "P2=2.6" in final_text
    assert Path(result["final_updated_inp"]).name == "model_iter2.inp"
    assert Path(result["iteration_results"][0]["updated_inp"]).name == "model_iter1.inp"
    assert Path(result["iteration_results"][1]["updated_inp"]).name == "model_iter2.inp"
    assert "next_iteration_solver" in result["iteration_results"][0]
    assert np.isclose(result["iteration_results"][0]["metrics"]["ccabs"], 37.5)
    assert np.isclose(result["iteration_results"][0]["metrics"]["rel_res"], 0.25)
    assert np.isclose(result["iteration_results"][1]["metrics"]["dx_norm"], np.linalg.norm([0.2, 0.4]))
    assert Path(result["iteration_results"][0]["saved_artifacts"]["files"]["summary_json"]).exists()
    assert Path(result["iteration_results"][1]["saved_artifacts"]["files"]["sensitivity_matrix_txt"]).exists()
    mapping_json = Path(result["iteration_results"][0]["saved_artifacts"]["files"]["parameter_element_mapping_json"])
    assert mapping_json.exists()
    assert "\"targets_by_scope\"" in mapping_json.read_text(encoding="utf-8")
    assert Path(result["saved_artifacts"]["files"]["parameter_history_csv"]).exists()
    assert Path(result["saved_artifacts"]["files"]["response_history_csv"]).exists()
    assert Path(result["saved_artifacts"]["files"]["overview_png"]).exists()
    overview_html = Path(result["saved_artifacts"]["files"]["overview_html"])
    assert overview_html.exists()
    assert "Bayesian Optimization History" in overview_html.read_text(encoding="utf-8")


def test_run_bayesian_update_workflow_uses_default_scatter_values(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    matrix_payload = {
        "workspace": str(tmp_path / "initial_ws"),
        "source_mode": "workspace",
        "workspace_built": False,
        "odb_id": None,
        "matrix": [[1.0]],
        "response_values": [10.0],
        "parameter_values": [1.0],
        "parameter_columns": [
            {"field": "d_UR_P1", "parameter_name": "P1", "parameter_token": "P1", "parameter_value": 1.0}
        ],
        "response_rows": [
            {"row_key": "r1", "response_label": "INST::10"}
        ],
    }
    captured = {"persist_calls": []}

    monkeypatch.setattr(bayesian_service, "build_dsa_normalized_sensitivity_matrix", lambda **kwargs: matrix_payload)

    def fake_bayesian_update(**kwargs):
        captured["p_scatter"] = kwargs["p_scatter"]
        captured["r_scatter"] = kwargs["r_scatter"]
        return {
            "delta_r": np.array([[1.0]]),
            "y": np.array([[10.0]]),
            "x": np.array([[0.1]]),
            "dp": np.array([[0.1]]),
            "p_new": np.array([1.1]),
            "G_n": np.eye(1),
        }

    monkeypatch.setattr(bayesian_service, "bayesian_update_normalized", fake_bayesian_update)
    monkeypatch.setattr(
        bayesian_service,
        "_persist_bayesian_tracking_results",
        lambda **kwargs: captured["persist_calls"].append(
            {
                **dict(kwargs),
                "iteration_results": list(kwargs.get("iteration_results") or []),
            }
        ),
    )

    result = bayesian_service.run_bayesian_update_workflow(
        project_id=1,
        input_inp=str(inp_path),
        workspace=str(tmp_path / "initial_ws"),
        target_responses=[8.0],
        output_dir=str(tmp_path / "out"),
        iterations=1,
        run_solver=False,
    )

    assert captured["p_scatter"].tolist() == [0.25]
    assert captured["r_scatter"].tolist() == [0.01]
    assert len(captured["persist_calls"]) == 1
    assert captured["persist_calls"][0]["batch_no"] == 1
    assert result["iteration_results"][0]["parameter_scatter"] == [0.25]
    assert result["iteration_results"][0]["response_scatter"] == [0.01]
    assert Path(result["saved_artifacts"]["files"]["iteration_summary_csv"]).exists()
    assert Path(result["saved_artifacts"]["files"]["response_diff_history_png"]).exists()


def test_run_bayesian_update_workflow_writes_cloud_result_metadata(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    matrix_payload = {
        "workspace": str(tmp_path / "initial_ws"),
        "source_mode": "workspace",
        "workspace_built": False,
        "odb_id": None,
        "matrix": [[1.0]],
        "response_values": [10.0],
        "parameter_values": [1.0],
        "parameter_columns": [
            {
                "field": "d_UR_P1",
                "parameter_name": "P1",
                "parameter_token": "P1",
                "parameter_value": 1.0,
                "element_mapping": {
                    "field": "d_UR_P1",
                    "parameter_name": "P1",
                    "target_kind": "cell",
                    "targets_by_scope": {"PART-1-1": [101]},
                },
            }
        ],
        "response_rows": [
            {"row_key": "r1", "response_label": "INST::10"}
        ],
    }
    cloud_calls = {}

    monkeypatch.setattr(bayesian_service, "build_dsa_normalized_sensitivity_matrix", lambda **kwargs: matrix_payload)
    monkeypatch.setattr(
        bayesian_service,
        "bayesian_update_normalized",
        lambda **kwargs: {
            "delta_r": np.array([[1.0]]),
            "y": np.array([[10.0]]),
            "x": np.array([[0.1]]),
            "dp": np.array([[0.1]]),
            "p_new": np.array([1.1]),
            "G_n": np.eye(1),
        },
    )
    monkeypatch.setattr(bayesian_service, "_persist_bayesian_tracking_results", lambda **kwargs: None)

    def fake_write_cloud(**kwargs):
        cloud_calls.update(kwargs)
        return {
            "result_group": "viz_rg",
            "step": "Bayesian Step",
            "field": "PARAMETER_CLOUD",
            "value_mode": "delta_value",
            "components": ["P1"],
            "frame_count": 1,
        }

    monkeypatch.setattr(bayesian_service, "_write_bayesian_cloud_result", fake_write_cloud)

    result = bayesian_service.run_bayesian_update_workflow(
        project_id=3,
        batch_no=2,
        input_inp=str(inp_path),
        odb_id="odb-demo",
        workspace=str(tmp_path / "initial_ws"),
        target_responses=[8.0],
        output_dir=str(tmp_path / "out"),
        iterations=1,
        run_solver=False,
        write_cloud_result=True,
        cloud_result_group="viz_rg",
        cloud_step_name="Bayesian Step",
        cloud_field_name="PARAMETER_CLOUD",
        cloud_value_mode="delta_value",
    )

    assert cloud_calls["odb_id"] == "odb-demo"
    assert cloud_calls["base_url"] is None
    assert cloud_calls["batch_no"] == 2
    assert cloud_calls["result_group"] == "viz_rg"
    assert cloud_calls["step_name"] == "Bayesian Step"
    assert cloud_calls["field_name"] == "PARAMETER_CLOUD"
    assert cloud_calls["value_mode"] == "delta_value"
    assert result["cloud_result"]["result_group"] == "viz_rg"
    assert result["cloud_result"]["components"] == ["P1"]


def test_write_bayesian_cloud_result_posts_external_field_payload(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, base_url: str, timeout: int):
            captured["base_url"] = base_url
            captured["timeout"] = timeout

        def post_external_field(self, odb_id: str, body: dict):
            captured["odb_id"] = odb_id
            captured["body"] = body
            return {
                "field_name": body["field_name"],
                "step_name": body["step_name"],
                "instances_written": len(body["instances"]),
                "frames_written": len(body["instances"][0]["frames"]),
                "source": "external",
            }

    monkeypatch.setattr(bayesian_service._sens, "ODBClient", FakeClient)

    result = bayesian_service._write_bayesian_cloud_result(
        odb_id="odb-demo",
        base_url="http://127.0.0.1:18765",
        batch_no=5,
        iteration_results=[
            {
                "parameter_columns": [
                    {"parameter_name": "P1", "parameter_value": 1.0},
                    {"parameter_name": "P2", "parameter_value": 2.0},
                ],
                "parameter_element_mapping": [
                    {
                        "parameter_name": "P1",
                        "parameter_value": 1.0,
                        "updated_parameter_value": 1.1,
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [101, 102]},
                    },
                    {
                        "parameter_name": "P2",
                        "parameter_value": 2.0,
                        "updated_parameter_value": 2.4,
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [102]},
                    },
                ],
            }
        ],
        result_group="viz_rg",
        step_name="Bayesian Step",
        field_name="PARAMETER_CLOUD",
        value_mode="delta_value",
        timeout=33,
    )

    assert captured["odb_id"] == "odb-demo"
    assert captured["base_url"] == "http://127.0.0.1:18765"
    assert captured["timeout"] == 33
    assert captured["body"]["type"] == "element"
    assert captured["body"]["components"] == ["P1", "P2"]
    assert captured["body"]["result_group"] == "viz_rg"
    assert len(captured["body"]["instances"]) == 1
    assert captured["body"]["instances"][0]["instance"] == "PART-1-1"
    assert captured["body"]["instances"][0]["frames"][0]["frame_idx"] == 0
    assert captured["body"]["instances"][0]["frames"][0]["frame_value"] == 1.0
    first_entry, second_entry = captured["body"]["instances"][0]["frames"][0]["data"]
    assert first_entry["label"] == 101
    assert np.isclose(first_entry["values"][0], 0.1)
    assert np.isnan(first_entry["values"][1])
    assert second_entry["label"] == 102
    assert np.isclose(second_entry["values"][0], 0.1)
    assert np.isclose(second_entry["values"][1], 0.4)
    assert result["odb_id"] == "odb-demo"
    assert result["query_hint"]["odb_id"] == "odb-demo"
    assert result["write_response"]["instances_written"] == 1


def test_resolve_cloud_scalar_value_supports_relative_change():
    assert np.isclose(
        bayesian_service._resolve_cloud_scalar_value(
            mode="relative_change",
            updated_value=1.2,
            baseline_value=1.0,
        ),
        0.2,
    )


def test_run_bayesian_update_workflow_stops_early_when_response_diff_within_threshold(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    matrix_payload = {
        "workspace": str(tmp_path / "initial_ws"),
        "source_mode": "workspace",
        "workspace_built": False,
        "odb_id": None,
        "matrix": [[1.0]],
        "response_values": [10.5],
        "parameter_values": [1.0],
        "parameter_columns": [
            {"field": "d_UR_P1", "parameter_name": "P1", "parameter_token": "P1", "parameter_value": 1.0}
        ],
        "response_rows": [
            {"row_key": "r1", "response_label": "INST::10"}
        ],
    }
    update_calls = []
    solver_calls = []

    monkeypatch.setattr(bayesian_service, "build_dsa_normalized_sensitivity_matrix", lambda **kwargs: matrix_payload)

    def fake_bayesian_update(**kwargs):
        update_calls.append(kwargs)
        return {
            "delta_r": np.array([[0.5]]),
            "y": np.array([[0.047619]]),
            "x": np.array([[0.0]]),
            "dp": np.array([[0.0]]),
            "p_new": np.array([1.0]),
            "G_n": np.eye(1),
        }

    monkeypatch.setattr(bayesian_service, "bayesian_update_normalized", fake_bayesian_update)
    monkeypatch.setattr(
        bayesian_service,
        "_run_iteration_solver",
        lambda **kwargs: solver_calls.append(kwargs) or {"workspace": {"workspace": str(tmp_path / "rerun_ws")}},
    )
    monkeypatch.setattr(bayesian_service, "_persist_bayesian_tracking_results", lambda **kwargs: None)

    result = bayesian_service.run_bayesian_update_workflow(
        project_id=9,
        input_inp=str(inp_path),
        workspace=str(tmp_path / "initial_ws"),
        target_responses=[10.0],
        output_dir=str(tmp_path / "out"),
        iterations=3,
        exit_diff_percent=6.0,
        run_solver=True,
    )

    assert len(update_calls) == 1
    assert update_calls[0]["step_scale"] == 0.0
    assert solver_calls == []
    assert result["iterations"] == 1
    assert result["requested_iterations"] == 3
    assert result["stopped_early"] is True
    assert result["final_parameter_values"] == [1.0]
    assert result["iteration_results"][0]["exit_check"]["converged"] is True
    assert result["iteration_results"][0]["exit_check"]["threshold"] == 6.0
    assert "next_iteration_solver" not in result["iteration_results"][0]


def test_run_bayesian_update_workflow_skips_file_outputs_when_save_results_false(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    matrix_payload = {
        "workspace": str(tmp_path / "initial_ws"),
        "source_mode": "workspace",
        "workspace_built": False,
        "odb_id": None,
        "matrix": [[1.0]],
        "response_values": [10.0],
        "parameter_values": [1.0],
        "parameter_columns": [
            {"field": "d_UR_P1", "parameter_name": "P1", "parameter_token": "P1", "parameter_value": 1.0}
        ],
        "response_rows": [
            {"row_key": "r1", "response_label": "INST::10"}
        ],
    }

    monkeypatch.setattr(bayesian_service, "build_dsa_normalized_sensitivity_matrix", lambda **kwargs: matrix_payload)
    monkeypatch.setattr(
        bayesian_service,
        "bayesian_update_normalized",
        lambda **kwargs: {
            "delta_r": np.array([[1.0]]),
            "y": np.array([[10.0]]),
            "x": np.array([[0.1]]),
            "dp": np.array([[0.1]]),
            "p_new": np.array([1.1]),
            "G_n": np.eye(1),
        },
    )
    monkeypatch.setattr(bayesian_service, "_persist_bayesian_tracking_results", lambda **kwargs: None)

    output_dir = tmp_path / "out"
    result = bayesian_service.run_bayesian_update_workflow(
        project_id=7,
        input_inp=str(inp_path),
        workspace=str(tmp_path / "initial_ws"),
        target_responses=[8.0],
        output_dir=str(output_dir),
        save_results=False,
        iterations=1,
        run_solver=False,
    )

    assert result["save_results"] is False
    assert result["output_dir"] is None
    assert result["final_updated_inp"] is None
    assert result["saved_artifacts"] == {}
    assert output_dir.exists() is False
    assert "saved_artifacts" not in result["iteration_results"][0]
    assert result["iteration_results"][0]["input_inp"] is None
    assert result["iteration_results"][0]["updated_inp"] is None
    assert result["iteration_results"][0]["source"]["workspace"] is None
    assert result["iteration_results"][0]["solver"] is None
    assert result["final_parameter_values"] == [1.1]


def test_run_bayesian_update_workflow_marks_project_fix_status_done(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    status_calls = []
    monkeypatch.setattr(
        bayesian_service,
        "update_work_condition_project_status",
        lambda project_id, **fields: status_calls.append((project_id, fields)),
    )
    monkeypatch.setattr(
        bayesian_service,
        "build_dsa_normalized_sensitivity_matrix",
        lambda **kwargs: {
            "workspace": str(tmp_path / "initial_ws"),
            "source_mode": "workspace",
            "workspace_built": False,
            "odb_id": None,
            "matrix": [[1.0]],
            "response_values": [10.0],
            "parameter_values": [1.0],
            "parameter_columns": [
                {"field": "d_UR_P1", "parameter_name": "P1", "parameter_token": "P1", "parameter_value": 1.0}
            ],
            "response_rows": [
                {"row_key": "r1", "response_label": "INST::10"}
            ],
        },
    )
    monkeypatch.setattr(
        bayesian_service,
        "bayesian_update_normalized",
        lambda **kwargs: {
            "delta_r": np.array([[1.0]]),
            "y": np.array([[10.0]]),
            "x": np.array([[0.1]]),
            "dp": np.array([[0.1]]),
            "p_new": np.array([1.1]),
            "G_n": np.eye(1),
        },
    )
    monkeypatch.setattr(bayesian_service, "_persist_bayesian_tracking_results", lambda **kwargs: None)

    bayesian_service.run_bayesian_update_workflow(
        project_id=7,
        input_inp=str(inp_path),
        workspace=str(tmp_path / "initial_ws"),
        target_responses=[8.0],
        output_dir=str(tmp_path / "out"),
        iterations=1,
        run_solver=False,
    )

    assert status_calls == [
        (
            7,
            {
                "fixes_cal_status": 0,
                "fixes_result_status": 0,
            },
        ),
        (
            7,
            {
                "fixes_cal_status": 1,
                "fixes_result_status": 1,
            },
        )
    ]


def test_run_bayesian_update_workflow_marks_project_fix_status_failed(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    status_calls = []
    console_events = []
    monkeypatch.setattr(
        bayesian_service,
        "update_work_condition_project_status",
        lambda project_id, **fields: status_calls.append((project_id, fields)),
    )
    monkeypatch.setattr(
        bayesian_service,
        "safe_write_console_event",
        lambda project_id, title, lines=None: console_events.append((project_id, title, list(lines or []))),
    )
    monkeypatch.setattr(
        bayesian_service,
        "build_dsa_normalized_sensitivity_matrix",
        lambda **kwargs: (_ for _ in ()).throw(bayesian_service.ValidationError("boom", {})),
    )

    try:
        bayesian_service.run_bayesian_update_workflow(
            project_id=7,
            input_inp=str(inp_path),
            workspace=str(tmp_path / "initial_ws"),
            target_responses=[8.0],
            output_dir=str(tmp_path / "out"),
            iterations=1,
            run_solver=False,
        )
    except bayesian_service.ValidationError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected run_bayesian_update_workflow to fail")

    assert status_calls[0] == (
        7,
        {
            "fixes_cal_status": 0,
            "fixes_result_status": 0,
        },
    )
    assert status_calls[-1] == (
        7,
        {
            "fixes_cal_status": 2,
            "fixes_result_status": 2,
        },
    )
    assert console_events


def test_run_bayesian_update_from_text_reads_external_matrix_and_responses(tmp_path: Path):
    matrix_file = tmp_path / "sens.txt"
    matrix_file.write_text(
        "\n".join(
            [
                "0.1 0.2",
                "0.3 0.4",
                "9.0 12.0",
                "8.0 10.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
P2=2.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    result = bayesian_service.run_bayesian_update_from_text(
        sensitivity_matrix_file=str(matrix_file),
        sensitivity_row_start=1,
        sensitivity_row_count=2,
        sensitivity_col_start=1,
        model_response_file=str(matrix_file),
        model_response_row=3,
        model_response_col_start=1,
        target_response_file=str(matrix_file),
        target_response_row=4,
        target_response_col_start=1,
        parameter_names=["P1", "P2"],
        parameter_scatter={"P1": 0.25, "P2": 0.5},
        response_scatter=[0.01, 0.02],
        input_inp=str(inp_path),
        output_dir=str(tmp_path / "check_out"),
        case_name="manual_check",
    )

    expected = bayesian_service.bayesian_update_normalized(
        p_current=np.array([1.0, 2.0]),
        r_model=np.array([9.0, 12.0]),
        r_target=np.array([8.0, 10.0]),
        S_norm=np.array([[0.1, 0.2], [0.3, 0.4]]),
        p_scatter=np.array([0.25, 0.5]),
        r_scatter=np.array([0.01, 0.02]),
        p_ref=np.array([1.0, 2.0]),
    )

    assert result["parameter_values"] == [1.0, 2.0]
    assert result["sensitivity_matrix"] == [[0.1, 0.2], [0.3, 0.4]]
    assert np.allclose(result["bayesian"]["p_new"], expected["p_new"].tolist())
    assert Path(result["saved_artifacts"]["files"]["summary_json"]).exists()
    assert Path(result["saved_artifacts"]["files"]["updated_parameter_values_txt"]).exists()


def test_run_bayesian_update_from_text_uses_default_scatter_values(tmp_path: Path):
    matrix_file = tmp_path / "sens.txt"
    matrix_file.write_text(
        "\n".join(
            [
                "0.1",
                "9.0",
                "8.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    inp_path = tmp_path / "model.inp"
    inp_path.write_text(
        """*Heading
*PARAMETER
P1=1.0
*Step
*Static
*End Step
""",
        encoding="utf-8",
    )

    result = bayesian_service.run_bayesian_update_from_text(
        sensitivity_matrix_file=str(matrix_file),
        sensitivity_row_start=1,
        sensitivity_row_count=1,
        model_response_file=str(matrix_file),
        model_response_row=2,
        target_response_file=str(matrix_file),
        target_response_row=3,
        parameter_names=["P1"],
        input_inp=str(inp_path),
        case_name="default_scatter_check",
    )

    assert result["parameter_scatter"] == [0.25]
    assert result["response_scatter"] == [0.01]


def test_persist_bayesian_tracking_results_overwrites_same_batch_and_writes_expected_rows(monkeypatch):
    executed = []

    class FakeCursor:
        def execute(self, sql, params=None):
            executed.append((" ".join(str(sql).split()), params))

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.committed = False
            self.rolled_back = False

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    fake_conn = FakeConnection()
    monkeypatch.setattr(bayesian_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(bayesian_service, "get_connection", lambda: fake_conn)

    bayesian_service._persist_bayesian_tracking_results(
        project_id=12,
        batch_no=1,
        iteration_results=[
            {
                "iteration": 1,
                "response_values": [5.0],
                "target_responses": [4.0],
                "response_rows": [{"row_key": "row-1", "response_label": "R1"}],
                "parameter_values": [1.0],
                "response_scatter": [0.01],
                "parameter_columns": [
                    {
                        "parameter_name": "P1",
                        "element_mapping": {"target_rows": [{"set_name": "SET1"}]},
                    }
                ],
                "bayesian": {"p_new": [1.5], "dp": [0.5]},
            }
        ],
    )

    delete_params = [params for sql, params in executed if sql.startswith("DELETE FROM")]
    assert delete_params == [(12, 1), (12, 1), (12,), (12, 1), (12, 1), (12, 1)]

    inserts = [(sql, params) for sql, params in executed if sql.startswith("INSERT INTO")]
    assert inserts[0][1] == (12, 1, 1)
    metric_insert = inserts[1][1]
    assert metric_insert[:4] == (12, 1, 1, 25.0)
    assert np.isclose(metric_insert[4], 0.25)
    assert metric_insert[5:] == (5.0, 4.0, 1.0, 0.5, 0.5, 25.0, 25.0)
    relevance_inserts = [params for sql, params in inserts if "t_mt_py_fem_relevance_tracking" in sql]
    assert relevance_inserts == [
        (12, 1, "CCABS", 25.0),
        (12, 1, "CCMEAN", 25.0),
        (12, 1, "CCDISP", 0.25),
        (12, 1, "CCTOTAL", 25.25),
        (12, 1, "CCDSF", 0.0),
    ]
    assert inserts[7][1] == (12, 1, "response_1", 1, 5.0, 4.0, 25.0)
    assert inserts[8][1] == (12, 1, "Response", "response_1", 1, 5.0)
    assert inserts[9][1] == (12, 1, "Parameter", "P1", 1, 1.5)
    assert inserts[10][1] == (12, 1, "P1", "default", "default", "SET1", 1.0, 1.5, 0.5)
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False


def test_sol200_modal_bayesian_prefers_metadata_source_bdf_for_initial_sol103(monkeypatch, tmp_path: Path):
    from services.model_update.analysis import nastran_sol200_service, project_path_service
    from services.model_update.importers import op2_service

    source_bdf = tmp_path / "source_model.bdf"
    source_bdf.write_text("CEND\nBEGIN BULK\nENDDATA\n", encoding="utf-8")
    localized_bdf = tmp_path / "source_model.localized_source.bdf"
    localized_bdf.write_text("CEND\nBEGIN BULK\nENDDATA\n", encoding="utf-8")
    generated_sol200_bdf = tmp_path / "source_model_sol200.bdf"
    generated_sol200_bdf.write_text("INCLUDE 'design_model.bdf'\n", encoding="utf-8")
    metadata_path = tmp_path / "source_model_sol200.bdf.sol200.json"
    metadata_path.write_text(
        bayesian_service.json.dumps(
            {
                "source_input_bdf": str(source_bdf),
                "localized_input_bdf": str(localized_bdf),
            }
        ),
        encoding="utf-8",
    )

    stored_runs = [
        {
            "parameter_columns": [
                {"parameter_name": "E1", "parameter_value": 100.0},
            ],
            "response_rows": [
                {"response_label": "mode_1", "mode_number": 1},
            ],
            "matrix": [[0.1]],
            "source": {
                "bdf_path": str(generated_sol200_bdf),
                "metadata_path": str(metadata_path),
            },
        },
        {
            "parameter_columns": [
                {"parameter_name": "E1", "parameter_value": 101.0},
            ],
            "response_rows": [
                {"response_label": "mode_1", "mode_number": 1},
            ],
            "matrix": [[0.1]],
            "analysis_run_id": 99,
        },
    ]

    sol103_inputs = []

    monkeypatch.setattr(bayesian_service, "_clear_bayesian_run_outputs", lambda **kwargs: {})
    monkeypatch.setattr(bayesian_service, "_set_project_fix_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(bayesian_service, "safe_write_console_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bayesian_service._sens,
        "_load_stored_sensitivity_run",
        lambda **kwargs: stored_runs.pop(0),
    )
    monkeypatch.setattr(
        bayesian_service,
        "_build_modal_response_payload",
        lambda **kwargs: {
            "response_rows": [{"response_label": "mode_1", "mode_number": 1}],
            "target_values": [9.0],
            "matched_payload": {},
        },
    )
    monkeypatch.setattr(
        bayesian_service,
        "_run_sol103_modal_response_values",
        lambda **kwargs: sol103_inputs.append(kwargs["input_bdf"]) or {
            "response_values": [10.0],
            "solver_payload": {},
        },
    )
    monkeypatch.setattr(
        bayesian_service,
        "_normalize_absolute_modal_sensitivity_matrix",
        lambda **kwargs: np.asarray([[0.1]], dtype=np.float64),
    )
    monkeypatch.setattr(
        bayesian_service,
        "bayesian_update_normalized",
        lambda **kwargs: {
            "p_new": [101.0],
            "dp": [1.0],
        },
    )
    monkeypatch.setattr(
        bayesian_service,
        "_update_bdf_parameter_values",
        lambda **kwargs: {"output_bdf": kwargs["output_bdf"]},
    )
    monkeypatch.setattr(
        bayesian_service,
        "_build_sol200_parameter_rows",
        lambda parameter_columns, values: [{"parameter_name": "E1", "parameter_value": float(values[0])}],
    )
    monkeypatch.setattr(
        bayesian_service,
        "_build_sol200_response_rows",
        lambda response_rows: list(response_rows),
    )
    monkeypatch.setattr(
        bayesian_service,
        "_select_modal_frequency_matrix_rows",
        lambda **kwargs: [[0.1]],
    )
    monkeypatch.setattr(
        bayesian_service,
        "_build_iteration_metrics",
        lambda **kwargs: {"ccabs": 0.0, "ccmean": 0.0, "ccdisp": 0.0, "cctotal": 0.0, "ccdsf": 0.0},
    )
    monkeypatch.setattr(
        bayesian_service,
        "_evaluate_exit_condition",
        lambda *args, **kwargs: {"converged": False},
    )
    monkeypatch.setattr(bayesian_service, "_write_bayesian_iteration_console_log", lambda **kwargs: None)
    monkeypatch.setattr(bayesian_service, "_persist_bayesian_tracking_results", lambda **kwargs: None)
    monkeypatch.setattr(
        bayesian_service,
        "_persist_final_iteration_modal_outputs",
        lambda **kwargs: {"batch_no": kwargs["batch_no"]},
    )
    monkeypatch.setattr(bayesian_service, "_clone_jsonable", lambda value: value)
    monkeypatch.setattr(
        nastran_sol200_service,
        "run_sol200_and_store_workflow",
        lambda **kwargs: {"generated_files": {}},
    )
    monkeypatch.setattr(
        bayesian_service,
        "_save_bayesian_history_artifacts",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        op2_service,
        "_build_op2_parameter_columns_with_mappings",
        lambda **kwargs: [{"parameter_name": "E1", "target_rows": []}],
    )
    monkeypatch.setattr(project_path_service, "resolve_project_workspace", lambda project_id: str(tmp_path))

    result = bayesian_service.run_sol200_modal_frequency_bayesian_update_workflow(
        project_id=1,
        batch_no=1,
        iterations=1,
        save_results=False,
        write_cloud_result=False,
    )

    assert sol103_inputs
    assert Path(sol103_inputs[0]).resolve() == source_bdf.resolve()
    assert result["input_bdf"] == str(source_bdf.resolve())
