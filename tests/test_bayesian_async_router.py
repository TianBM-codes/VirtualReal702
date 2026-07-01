from webapi.models import BayesianModelUpdateRequest
from webapi.routers import optimization


def test_bayesian_run_kwargs_excludes_async_submit():
    body = BayesianModelUpdateRequest(
        project_id=1001,
        input_inp="D:/demo/model.inp",
        target_responses={"R1": 1.0},
        async_submit=True,
    )

    kwargs = optimization._bayesian_run_kwargs(body)

    assert kwargs["project_id"] == 1001
    assert kwargs["input_inp"] == "D:/demo/model.inp"
    assert kwargs["target_responses"] == {"R1": 1.0}
    assert "async_submit" not in kwargs


def test_bayesian_run_kwargs_uses_service_config_defaults(monkeypatch):
    body = BayesianModelUpdateRequest(
        project_id=1001,
        input_inp="D:/demo/model.inp",
        target_responses={"R1": 1.0},
    )
    monkeypatch.setattr(optimization, "resolve_project_cal_subdir", lambda project_id, name: "D:/temp/bayesian")
    monkeypatch.setattr(optimization, "resolve_abaqus_command", lambda value: "C:/SIMULIA/Commands/abaqus.bat")
    monkeypatch.setattr(optimization, "resolve_project_input_file", lambda project_id, explicit_path, file_name, field_name: "D:/demo/model.inp")

    kwargs = optimization._bayesian_run_kwargs(body)

    assert kwargs["output_dir"] == "D:/temp/bayesian"
    assert kwargs["abaqus"] == "C:/SIMULIA/Commands/abaqus.bat"
    assert kwargs["write_cloud_result"] is True
    assert kwargs["cloud_step_name"] == "BayesianUpdate"
    assert "python3" not in kwargs


def test_run_bayesian_update_workflow_compact_wraps_sensitivity_status(monkeypatch):
    captured = {}

    def fake_wrapper(project_id, fn):
        captured["project_id"] = project_id
        return fn()

    monkeypatch.setattr(optimization._sens, "_run_with_project_sensitivity_status", fake_wrapper)
    monkeypatch.setattr(
        optimization,
        "run_bayesian_update_workflow",
        lambda **kwargs: {
            "project_id": kwargs["project_id"],
            "batch_no": kwargs.get("batch_no"),
            "input_inp": kwargs["input_inp"],
            "output_dir": "D:/temp/bayesian",
            "save_results": True,
            "iterations": 1,
            "requested_iterations": 1,
            "stopped_early": False,
            "exit_diff_percent": None,
            "final_updated_inp": "D:/temp/bayesian/iter1.inp",
            "saved_artifacts": {"history_dir": "D:/temp/bayesian/history", "files": {}},
            "cloud_result": None,
            "final_static_output": None,
            "iteration_results": [],
        },
    )

    result = optimization._run_bayesian_update_workflow_compact(
        project_id=12,
        input_inp="D:/demo/model.inp",
        target_responses={"R1": 1.0},
    )

    assert captured["project_id"] == 12
    assert result["project_id"] == 12
