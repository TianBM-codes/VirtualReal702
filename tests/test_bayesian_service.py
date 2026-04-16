from pathlib import Path
from types import SimpleNamespace

import numpy as np

from services.model_update.analysis import bayesian_service


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

    def fake_label_map(workspace, *, step, field, instance, position, frame, aggregation, component=None, component_index=None):
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

    def fake_label_map(workspace, *, step, field, instance, position, frame, aggregation, component=None, component_index=None):
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

    monkeypatch.setattr(bayesian_service, "build_dsa_normalized_sensitivity_matrix", fake_build_matrix)
    monkeypatch.setattr(bayesian_service, "_run_iteration_solver", fake_run_iteration_solver)
    monkeypatch.setattr(bayesian_service, "bayesian_update_normalized", fake_bayesian_update)

    result = bayesian_service.run_bayesian_update_workflow(
        project_id=1,
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
    assert result["final_parameter_values"] == [1.3, 2.6]

    final_text = Path(result["final_updated_inp"]).read_text(encoding="utf-8")
    assert "P1=1.3" in final_text
    assert "P2=2.6" in final_text
    assert Path(result["final_updated_inp"]).name == "model_iter2.inp"
    assert Path(result["iteration_results"][0]["updated_inp"]).name == "model_iter1.inp"
    assert Path(result["iteration_results"][1]["updated_inp"]).name == "model_iter2.inp"
    assert "next_iteration_solver" in result["iteration_results"][0]
    assert Path(result["iteration_results"][0]["saved_artifacts"]["files"]["summary_json"]).exists()
    assert Path(result["iteration_results"][1]["saved_artifacts"]["files"]["sensitivity_matrix_txt"]).exists()
    mapping_json = Path(result["iteration_results"][0]["saved_artifacts"]["files"]["parameter_element_mapping_json"])
    assert mapping_json.exists()
    assert "\"targets_by_scope\"" in mapping_json.read_text(encoding="utf-8")


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
    captured = {}

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
    assert result["iteration_results"][0]["parameter_scatter"] == [0.25]
    assert result["iteration_results"][0]["response_scatter"] == [0.01]


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
