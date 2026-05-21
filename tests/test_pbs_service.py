import pytest

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
            server_name="a4mgt1",
            stage_path_template="/stage/${USER}",
            username="user",
            password="pass",
            verify_ssl=False,
            api_paths={
                "login": "/api/login",
                "expand_vars": "/api/expandvars",
                "create_dir": "/api/dir/create",
                "upload_file": "/api/files/upload",
                "file_exists": "/api/files/exists",
                "submit_job": "/api/jpbs",
                "job_status": "/api/jobs/{job_id}",
                "list_files": "/api/files/list",
                "download_file": "/api/files/download",
            },
            applications={
                "Abaqus": {
                    "cores": 8,
                    "hosts": 1,
                    "precision": "both",
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
    assert app_config["application_id"] == "Abaqus"
    assert app_config["application_name"] == "Abaqus"
    assert app_config["platform"] == "MultiCore-48c-394G|Free:528|Total:672"
    assert app_config["version"] == "2022"
    assert app_config["cores"] == 48
    assert app_config["precision"] == "off"
    assert result["project_id"] == 32
    assert result["job_id"] == "pbs-001"


def test_run_pbs_solver_job_requires_project_pbs_config(monkeypatch, tmp_path):
    input_file = tmp_path / "model.inp"
    input_file.write_text("*Heading\n", encoding="utf-8")

    monkeypatch.setattr(pbs_service, "_load_project_pbs_settings", lambda project_id: {})

    with pytest.raises(Exception) as exc_info:
        pbs_service.run_pbs_solver_job(
            project_id=32,
            application="Abaqus",
            input_file=str(input_file),
            wait=False,
        )

    assert "请先配置高性能集群计算配置" in str(exc_info.value)


def test_run_pbs_solver_job_requires_project_application_fields(monkeypatch, tmp_path):
    input_file = tmp_path / "model.inp"
    input_file.write_text("*Heading\n", encoding="utf-8")

    monkeypatch.setattr(
        pbs_service,
        "_load_project_pbs_settings",
        lambda project_id: {
            "env": "prod",
            "ApplicationId": "Abaqus",
        },
    )
    monkeypatch.setattr(
        pbs_service,
        "load_pbs_environment_config",
        lambda env: pbs_service.PBSEnvironmentConfig(
            name=str(env),
            base_url="https://pbs.example.com",
            server_name="a4mgt1",
            stage_path_template="/stage/${USER}",
            username="user",
            password="pass",
            verify_ssl=False,
            api_paths={
                "login": "/api/login",
                "expand_vars": "/api/expandvars",
                "create_dir": "/api/dir/create",
                "upload_file": "/api/files/upload",
                "file_exists": "/api/files/exists",
                "submit_job": "/api/jpbs",
                "job_status": "/api/jobs/{job_id}",
                "list_files": "/api/files/list",
                "download_file": "/api/files/download",
            },
            applications={
                "Abaqus": {
                    "cores": 8,
                    "hosts": 1,
                    "precision": "off",
                    "primary_file_exts": [".inp"],
                    "result_exts": [".odb"],
                }
            },
        ),
    )

    with pytest.raises(Exception) as exc_info:
        pbs_service.run_pbs_solver_job(
            project_id=32,
            application="Abaqus",
            input_file=str(input_file),
            wait=False,
        )

    assert "PBS 应用配置不完整" in str(exc_info.value)
    assert "project_config.pbs" in str(exc_info.value)


def test_build_submit_payload_for_nastran_does_not_include_memory():
    client = pbs_service.PBSClient(
        pbs_service.PBSEnvironmentConfig(
            name="dev",
            base_url="https://pbs.example.com",
            server_name="hpccluster",
            stage_path_template="/stage/${USER}",
            username="user",
            password="pass",
            verify_ssl=False,
            api_paths={
                "login": "/api/login",
                "expand_vars": "/api/expandvars",
                "create_dir": "/api/dir/create",
                "upload_file": "/api/files/upload",
                "file_exists": "/api/files/exists",
                "submit_job": "/api/jpbs",
                "job_status": "/api/jobs/{job_id}",
                "list_files": "/api/files/list",
                "download_file": "/api/files/download",
            },
            applications={
                "Nastran": {
                    "application_id": "Nastran",
                    "application_name": "Nastran",
                    "version": "2019",
                    "platform": "queue-a",
                    "cores": 8,
                    "primary_file_exts": [".bdf"],
                    "result_exts": [".op2"],
                }
            },
        )
    )

    payload = client.build_submit_payload(
        application="Nastran",
        job_name="demo",
        remote_primary_file="/stage/demo.bdf",
        remote_job_dir="/stage/job/demo",
    )

    assert payload["CORES"] == 8
    assert "MEMORY" not in payload
