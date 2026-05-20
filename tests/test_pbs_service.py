from services.model_update.analysis import pbs_service


def test_run_pbs_solver_job_merges_project_pbs_settings(monkeypatch, tmp_path):
    input_file = tmp_path / "model.inp"
    input_file.write_text("*Heading\n", encoding="utf-8")

    captured = {}

    class _FakeClient:
        def __init__(self, config, *, timeout):
            captured["config"] = config
            captured["timeout"] = timeout

        def get_application_config(self, application):
            return captured["config"].applications[application]

        def run_job(self, **kwargs):
            captured["run_job_kwargs"] = kwargs
            return {"job_id": "pbs-001", "resolved_job_state": "C"}

    monkeypatch.setattr(
        pbs_service,
        "_load_project_pbs_settings",
        lambda project_id: {
            "env": "prod",
            "ApplicationId": "Abaqus",
            "ApplicationName": "Abaqus",
            "VERSION": "2022",
            "CORES": 48,
            "HOSTS": 1,
            "PLATFORM": "MultiCore-48c-394G|Free:528|Total:672",
            "PRECISION": "off",
        },
    )
    monkeypatch.setattr(
        pbs_service,
        "load_pbs_environment_config",
        lambda env: pbs_service.PBSEnvironmentConfig(
            name=str(env),
            base_url="https://pbs.example.com",
            api_prefix="/api",
            service_prefix="/Service6",
            storage_prefix="/storage",
            auth_path="/auth",
            server_name="a4mgt1",
            stage_path_template="/stage/${USER}",
            username="user",
            password="pass",
            verify_ssl=False,
            fallback_service_prefixes=[],
            applications={
                "Abaqus": {
                    "application_id": "Abaqus",
                    "application_name": "Abaqus",
                    "version": "2020",
                    "cores": 8,
                    "hosts": 1,
                    "precision": "both",
                    "platform": "",
                    "primary_file_exts": [".inp"],
                    "result_exts": [".odb"],
                }
            },
        ),
    )
    monkeypatch.setattr(pbs_service, "PBSClient", _FakeClient)

    result = pbs_service.run_pbs_solver_job(
        project_id=32,
        application="Abaqus",
        input_file=str(input_file),
        timeout_sec=30,
        wait=False,
    )

    app_config = captured["config"].applications["Abaqus"]
    assert captured["config"].name == "prod"
    assert app_config["platform"] == "MultiCore-48c-394G|Free:528|Total:672"
    assert app_config["version"] == "2022"
    assert app_config["cores"] == 48
    assert app_config["precision"] == "off"
    assert result["project_id"] == 32
    assert result["job_id"] == "pbs-001"
