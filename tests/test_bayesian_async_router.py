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
    monkeypatch.setattr(optimization, "resolve_bayesian_output_dir", lambda value: "D:/temp/bayesian")
    monkeypatch.setattr(optimization, "resolve_abaqus_command", lambda value: "C:/SIMULIA/Commands/abaqus.bat")
    monkeypatch.setattr(optimization, "resolve_python3_command", lambda value: "C:/Python/python.exe")

    kwargs = optimization._bayesian_run_kwargs(body)

    assert kwargs["output_dir"] == "D:/temp/bayesian"
    assert kwargs["abaqus"] == "C:/SIMULIA/Commands/abaqus.bat"
    assert kwargs["python3"] == "C:/Python/python.exe"
