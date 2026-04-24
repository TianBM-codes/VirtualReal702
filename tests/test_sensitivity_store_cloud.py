from pathlib import Path

import numpy as np

from services.model_update.analysis import sensitivity_service


def test_write_sensitivity_cloud_result_posts_external_field_payload(monkeypatch):
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

    monkeypatch.setattr(sensitivity_service, "ODBClient", FakeClient)

    result = sensitivity_service._write_sensitivity_cloud_result(
        odb_id="odb-demo",
        base_url="http://127.0.0.1:18765",
        batch_no="5",
        matrix_payload={
            "matrix": [[0.1, 0.2], [0.3, 0.4]],
            "response_rows": [
                {"row_key": "R1", "response_label": "PART-1-1::10"},
                {"row_key": "R2", "response_label": "PART-1-1::20"},
            ],
            "parameter_columns": [
                {
                    "parameter_name": "P1",
                    "field": "d_U_P1",
                    "element_mapping": {
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [101, 102]},
                    },
                },
                {
                    "parameter_name": "P2",
                    "field": "d_U_P2",
                    "element_mapping": {
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [102]},
                    },
                },
            ],
        },
        result_group="viz_rg",
        step_name="Sensitivity Step",
        field_name="SENSITIVITY_CLOUD",
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
    assert len(captured["body"]["instances"][0]["frames"]) == 2
    frame0 = captured["body"]["instances"][0]["frames"][0]
    frame1 = captured["body"]["instances"][0]["frames"][1]
    assert frame0["frame_idx"] == 0
    assert frame0["frame_value"] == 1.0
    assert frame1["frame_idx"] == 1
    assert frame1["frame_value"] == 2.0
    first_entry, second_entry = frame0["data"]
    assert first_entry["label"] == 101
    assert np.isclose(first_entry["values"][0], 0.1)
    assert np.isnan(first_entry["values"][1])
    assert second_entry["label"] == 102
    assert np.isclose(second_entry["values"][0], 0.1)
    assert np.isclose(second_entry["values"][1], 0.2)
    assert frame1["data"][1]["label"] == 102
    assert np.isclose(frame1["data"][1]["values"][0], 0.3)
    assert np.isclose(frame1["data"][1]["values"][1], 0.4)
    assert result["odb_id"] == "odb-demo"
    assert result["frame_count"] == 2
    assert result["components"] == ["P1", "P2"]
    assert result["frames"][0]["description"] == "R1"
    assert result["query_hint"]["endpoint"] == "/api/odb/{odb_id}/results/frame-scalars"
    assert result["write_response"]["instances_written"] == 1


def test_store_dsa_sensitivity_results_writes_cloud_result_metadata(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    matrix_payload = {
        "workspace": str(workspace),
        "base_url": None,
        "step": "Step-1",
        "instances": ["PART-1-1"],
        "aggregation": "max_abs",
        "frame": 0,
        "field_prefix": "d_U_",
        "matrix": [[0.1]],
        "response_rows": [{"row_key": "R1", "response_label": "PART-1-1::10"}],
        "parameter_columns": [
            {
                "field": "d_U_P1",
                "parameter_name": "P1",
                "element_mapping": {
                    "target_kind": "cell",
                    "targets_by_scope": {"PART-1-1": [101]},
                },
            }
        ],
    }
    cloud_calls = {}

    monkeypatch.setattr(
        sensitivity_service,
        "_load_dsa_normalized_sensitivity_matrix",
        lambda **kwargs: matrix_payload,
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_persist_sensitivity_matrix",
        lambda **kwargs: {"analysis_run_id": 12, "project_id": 3, "batch_no": "2"},
    )

    def fake_write_cloud(**kwargs):
        cloud_calls.update(kwargs)
        return {
            "result_group": "viz_rg",
            "step": "Sensitivity Step",
            "field": "SENSITIVITY_CLOUD",
            "components": ["P1"],
            "frame_count": 1,
        }

    monkeypatch.setattr(sensitivity_service, "_write_sensitivity_cloud_result", fake_write_cloud)

    result = sensitivity_service.store_dsa_sensitivity_results(
        project_id=3,
        batch_no="2",
        input_inp=str(inp_path),
        odb_id="odb-demo",
        base_url="http://127.0.0.1:18765",
        workspace=str(workspace),
        run_solver=False,
        write_cloud_result=True,
        cloud_result_group="viz_rg",
        cloud_step_name="Sensitivity Step",
        cloud_field_name="SENSITIVITY_CLOUD",
    )

    assert cloud_calls["odb_id"] == "odb-demo"
    assert cloud_calls["base_url"] == "http://127.0.0.1:18765"
    assert cloud_calls["batch_no"] == "2"
    assert cloud_calls["result_group"] == "viz_rg"
    assert cloud_calls["step_name"] == "Sensitivity Step"
    assert cloud_calls["field_name"] == "SENSITIVITY_CLOUD"
    assert cloud_calls["matrix_payload"] is matrix_payload
    assert result["cloud_result"]["result_group"] == "viz_rg"
    assert result["cloud_result"]["components"] == ["P1"]
    assert result["analysis_run_id"] == 12


def test_run_sensitivity_inp_and_store_runs_solver_builds_workspace_and_cleans_files(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "model_sensitivity.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    workspace_calls = {}
    matrix_payload = {
        "workspace": str(output_dir / "job_a_workspace"),
        "step": "Step-1",
        "instances": ["PART-1-1"],
        "aggregation": "max_abs",
        "frame": 0,
        "field_prefix": "d_U_",
        "matrix": [[0.2]],
        "response_rows": [{"row_key": "R1", "response_label": "PART-1-1::10"}],
        "parameter_columns": [{"field": "d_U_T1", "parameter_name": "T1"}],
    }

    monkeypatch.setattr(
        sensitivity_service,
        "run_abaqus_job",
        lambda **kwargs: {
            "job_name": "job_a",
            "generated_files": {"analysis_inp": str(output_dir / inp_path.name)},
            "solver": {
                "ok": True,
                "artifacts": {"odb": str(output_dir / "job_a.odb")},
            },
        },
    )

    def fake_build_workspace(**kwargs):
        workspace_calls.update(kwargs)
        return {"workspace": kwargs["workspace"]}

    monkeypatch.setattr(sensitivity_service, "build_workspace_from_odb", fake_build_workspace)
    monkeypatch.setattr(
        sensitivity_service,
        "delete_abaqus_process_files",
        lambda workdir, job_name: [str(Path(workdir) / f"{job_name}.com")],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_dsa_normalized_sensitivity_matrix",
        lambda **kwargs: matrix_payload,
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_persist_sensitivity_matrix",
        lambda **kwargs: {"analysis_run_id": 20, "project_id": 7, "batch_no": "3"},
    )

    result = sensitivity_service.run_sensitivity_inp_and_store(
        project_id=7,
        batch_no="3",
        input_inp=str(inp_path),
        output_dir=str(output_dir),
        step="Step-1",
        instances=["PART-1-1"],
        field_prefix="d_U_",
        response_component="U1",
        position="NODAL",
    )

    assert workspace_calls["odb_path"] == str(output_dir / "job_a.odb")
    assert workspace_calls["workspace"] == str(output_dir / "job_a_workspace")
    assert result["analysis_run_id"] == 20
    assert result["analysis_inp"] == str((output_dir / inp_path.name).resolve())
    assert result["odb_path"] == str((output_dir / "job_a.odb").resolve())
    assert result["workspace"] == str(output_dir / "job_a_workspace")
    assert result["deleted_process_files"] == [str(output_dir / "job_a.com")]


def test_generate_sensitivity_inp_and_store_loads_project_metadata_and_reuses_run_path(monkeypatch, tmp_path: Path):
    original_inp = tmp_path / "model.inp"
    original_inp.write_text("*Heading\n", encoding="utf-8")
    generated_inp = tmp_path / "out" / "generated_sensitivity.inp"
    generated_inp.parent.mkdir()
    generated_inp.write_text("*Heading\n", encoding="utf-8")

    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [{"parameter_name": "T1"}],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_design_responses",
        lambda project_id: [{"response_no": 1, "set_name": "SET-1"}],
    )

    generation_calls = {}

    def fake_generate(**kwargs):
        generation_calls.update(kwargs)
        return {
            "analysis_inp": str(generated_inp),
            "generated_files": {"design_parameter_inp": str(tmp_path / "out" / "designParameter.inp")},
        }

    monkeypatch.setattr(sensitivity_service, "_generate_sensitivity_inp_from_project_db", fake_generate)
    monkeypatch.setattr(
        sensitivity_service,
        "run_sensitivity_inp_and_store",
        lambda **kwargs: {
            "analysis_run_id": 31,
            "project_id": kwargs["project_id"],
            "batch_no": kwargs["batch_no"],
            "input_inp": kwargs["input_inp"],
            "analysis_inp": kwargs["input_inp"],
            "generated_files": {"analysis_inp": kwargs["input_inp"]},
            "deleted_process_files": [],
        },
    )

    result = sensitivity_service.generate_sensitivity_inp_and_store(
        project_id=9,
        batch_no="4",
        input_inp=str(original_inp),
        output_dir=str(tmp_path / "out"),
        step="Step-1",
        instances=["PART-1-1"],
        field_prefix="d_U_",
        response_component="U1",
        position="NODAL",
    )

    assert generation_calls["project_id"] == 9
    assert generation_calls["input_inp"] == str(original_inp.resolve())
    assert len(generation_calls["parameter_rows"]) == 1
    assert len(generation_calls["design_response_rows"]) == 1
    assert result["input_inp"] == str(original_inp.resolve())
    assert result["analysis_inp"] == str(generated_inp)
    assert result["generation_source"] == "project_db"
    assert result["loaded_parameter_count"] == 1
    assert result["loaded_design_response_count"] == 1
    assert result["generated_files"]["design_parameter_inp"].endswith("designParameter.inp")
