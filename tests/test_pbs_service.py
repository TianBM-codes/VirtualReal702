import pytest

from services.model_update.analysis import pbs_service


def test_submit_local_project_result_group_and_wait_uses_existing_runner_pipeline(monkeypatch, tmp_path):
    from src import job_runner as local_job_runner

    odb_path = tmp_path / "case_a.odb"
    odb_path.write_text("odb", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    state = {
        "project": {
            "project_id": "1001",
            "workspace": "1001",
            "geom_status": "ready",
        },
        "group": None,
    }
    captured = {}

    class _FakeRepo:
        def __init__(self, registry_db_path):
            captured["registry_db_path"] = registry_db_path

        def get_project(self, project_id):
            captured["get_project"] = project_id
            return dict(state["project"])

        def get_result_group(self, project_id, result_group):
            captured.setdefault("get_result_group_calls", []).append((project_id, result_group))
            group = state["group"]
            if group and group["project_id"] == project_id and group["result_group"] == result_group:
                return dict(group)
            return None

        def create_result_group(self, **kwargs):
            captured["create_result_group"] = dict(kwargs)
            state["group"] = {
                "project_id": kwargs["project_id"],
                "result_group": kwargs["result_group"],
                "display_name": kwargs["display_name"],
                "source_path": kwargs["source_path"],
                "source_file": kwargs["source_file"],
                "status": "pending",
                "parse_options": kwargs["parse_options"],
            }

        def reset_result_group_for_resubmit(self, *args, **kwargs):
            raise AssertionError("reset_result_group_for_resubmit should not be called for a new result group")

    monkeypatch.setattr(pbs_service, "RegistryRepo", _FakeRepo)
    monkeypatch.setattr(pbs_service, "_project_workspace", lambda project_id: workspace.resolve())
    monkeypatch.setattr(local_job_runner, "_cleanup_result_group", lambda ws, rg: captured.setdefault("cleanup", []).append((ws, rg)))
    monkeypatch.setattr(
        local_job_runner,
        "_update_result_group_status",
        lambda project_id, result_group, status, error_message=None: (
            captured.setdefault("status_updates", []).append((project_id, result_group, status, error_message)),
            state["group"].update({"status": status, "error_message": error_message}),
        )[-1],
    )

    def _fake_run_result_group(project_id, result_group, source_path, parse_options_json, resolved_workspace):
        captured["run_result_group"] = {
            "project_id": project_id,
            "result_group": result_group,
            "source_path": source_path,
            "parse_options_json": parse_options_json,
            "workspace": resolved_workspace,
        }
        state["group"].update({"status": "ready"})
        return True

    monkeypatch.setattr(local_job_runner, "_run_result_group", _fake_run_result_group)

    result = pbs_service._submit_local_project_result_group_and_wait(
        project_id=1001,
        source_path=str(odb_path),
        job_name="case_a",
        result_group=None,
        display_name=None,
        parse_options={"consistency_check": "count-only", "invariants": "none"},
        default_result_group="default_result",
    )

    assert result["result_group"] == "default_result"
    assert result["display_name"] == "case_a"
    assert result["status"] == "ready"
    assert result["workspace"] == str(workspace.resolve())
    assert captured["create_result_group"]["result_group"] == "default_result"
    assert captured["status_updates"][0][:3] == ("1001", "default_result", "running")
    assert captured["run_result_group"]["result_group"] == "default_result"


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
                "submit_job": "/api/jobs",
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
                "submit_job": "/api/jobs",
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
                "submit_job": "/api/jobs",
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


def test_run_pbs_solver_job_uses_local_result_group_parse_for_abaqus(monkeypatch, tmp_path):
    input_file = tmp_path / "model.inp"
    input_file.write_text("*Heading\n", encoding="utf-8")
    odb_file = tmp_path / "job_a.odb"
    odb_file.write_text("odb", encoding="utf-8")

    captured = {}

    class _FakeClient:
        def __init__(self, config, *, timeout):
            captured["config"] = config
            captured["timeout"] = timeout

        def get_application_config(self, application):
            return {
                "application_id": "Abaqus",
                "application_name": "Abaqus",
                "version": "2022",
                "platform": "queue-a",
                "primary_file_exts": [".inp"],
                "result_exts": [".odb"],
            }

        def run_job(self, **kwargs):
            captured["run_job_kwargs"] = kwargs
            return {
                "job_id": "pbs-001",
                "resolved_job_state": "C",
                "downloaded_files": [str(odb_file)],
            }

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
            "PLATFORM": "queue-a",
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
                "submit_job": "/api/jobs",
                "job_status": "/api/jobs/{job_id}",
                "list_files": "/api/files/list",
                "download_file": "/api/files/download",
            },
            applications={
                "Abaqus": {
                    "application_id": "Abaqus",
                    "application_name": "Abaqus",
                    "version": "2022",
                    "platform": "queue-a",
                    "cores": 8,
                    "hosts": 1,
                    "precision": "off",
                    "primary_file_exts": [".inp"],
                    "result_exts": [".odb"],
                }
            },
        ),
    )
    monkeypatch.setattr(pbs_service, "PBSClient", _FakeClient)
    monkeypatch.setattr(
        pbs_service,
        "update_work_condition_project_status",
        lambda project_id, **fields: captured.setdefault("status_updates", []).append((project_id, fields)),
    )
    monkeypatch.setattr(
        pbs_service,
        "_submit_local_project_result_group_and_wait",
        lambda **kwargs: captured.update({"local_parse_kwargs": kwargs}) or {
            "result_group": kwargs["result_group"] or kwargs["default_result_group"],
            "status": "ready",
            "workspace": str(tmp_path / "workspace"),
        },
    )

    result = pbs_service.run_pbs_solver_job(
        project_id=32,
        application="Abaqus",
        input_file=str(input_file),
        job_name="job_a",
        wait=True,
        step="Step-1",
    )

    assert result["uploaded_result_file"] == str(odb_file.resolve())
    assert result["upload"]["result_group"] == "default_result"
    assert captured["local_parse_kwargs"]["default_result_group"] == "default_result"
    assert captured["local_parse_kwargs"]["source_path"] == str(odb_file.resolve())
    assert captured["local_parse_kwargs"]["parse_options"]["steps"] == ["Step-1"]
    assert captured["local_parse_kwargs"]["parse_options"]["frames"] == "all"
    assert captured["status_updates"][-1] == (32, {"simulation_result_status": 1})


def test_run_pbs_solver_job_uses_modal_import_parse_for_nastran(monkeypatch, tmp_path):
    input_file = tmp_path / "model.bdf"
    input_file.write_text("BEGIN BULK\n", encoding="utf-8")
    op2_file = tmp_path / "job_n.op2"
    op2_file.write_text("op2", encoding="utf-8")

    captured = {}

    class _FakeClient:
        def __init__(self, config, *, timeout):
            captured["config"] = config
            captured["timeout"] = timeout

        def get_application_config(self, application):
            return {
                "application_id": "Nastran",
                "application_name": "Nastran",
                "version": "2019",
                "platform": "queue-n",
                "primary_file_exts": [".bdf"],
                "result_exts": [".op2"],
            }

        def run_job(self, **kwargs):
            captured["run_job_kwargs"] = kwargs
            return {
                "job_id": "pbs-002",
                "resolved_job_state": "C",
                "downloaded_files": [str(op2_file)],
            }

    monkeypatch.setattr(
        pbs_service,
        "_load_project_pbs_settings",
        lambda project_id: {
            "env": "prod",
            "ApplicationId": "Nastran",
            "ApplicationName": "Nastran",
            "VERSION": "2019",
            "CORES": 8,
            "PLATFORM": "queue-n",
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
                "submit_job": "/api/jobs",
                "job_status": "/api/jobs/{job_id}",
                "list_files": "/api/files/list",
                "download_file": "/api/files/download",
            },
            applications={
                "Nastran": {
                    "application_id": "Nastran",
                    "application_name": "Nastran",
                    "version": "2019",
                    "platform": "queue-n",
                    "cores": 8,
                    "primary_file_exts": [".bdf"],
                    "result_exts": [".op2"],
                }
            },
        ),
    )
    monkeypatch.setattr(pbs_service, "PBSClient", _FakeClient)
    monkeypatch.setattr(
        pbs_service,
        "_submit_generic_project_result_group_and_wait",
        lambda **kwargs: captured.update({"generic_parse_kwargs": kwargs}) or {
            "result_group": kwargs["result_group"] or "solver_result_job_n",
            "status": "ready",
            "workspace": str(tmp_path / "workspace"),
        },
    )

    result = pbs_service.run_pbs_solver_job(
        project_id=32,
        application="Nastran",
        input_file=str(input_file),
        job_name="job_n",
        wait=True,
    )

    assert result["uploaded_result_file"] == str(op2_file.resolve())
    assert captured["generic_parse_kwargs"]["source_path"] == str(op2_file.resolve())
    assert captured["generic_parse_kwargs"]["parse_options"] == {
        "modal_import": {
            "bdf_path": str(input_file.resolve()),
            "overwrite": True,
            "async_submit": False,
        }
    }


def test_run_pbs_solver_job_sets_simulation_result_status_to_2_on_abaqus_failure(monkeypatch, tmp_path):
    input_file = tmp_path / "model.inp"
    input_file.write_text("*Heading\n", encoding="utf-8")
    odb_file = tmp_path / "job_a.odb"
    odb_file.write_text("odb", encoding="utf-8")

    captured = {}

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
            "PLATFORM": "queue-a",
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
                "submit_job": "/api/jobs",
                "job_status": "/api/jobs/{job_id}",
                "list_files": "/api/files/list",
                "download_file": "/api/files/download",
            },
            applications={
                "Abaqus": {
                    "application_id": "Abaqus",
                    "application_name": "Abaqus",
                    "version": "2022",
                    "platform": "queue-a",
                    "cores": 8,
                    "hosts": 1,
                    "precision": "off",
                    "primary_file_exts": [".inp"],
                    "result_exts": [".odb"],
                }
            },
        ),
    )

    class _FakeClient:
        def __init__(self, config, *, timeout):
            pass

        def get_application_config(self, application):
            return {
                "application_id": "Abaqus",
                "application_name": "Abaqus",
                "version": "2022",
                "platform": "queue-a",
                "primary_file_exts": [".inp"],
                "result_exts": [".odb"],
            }

        def run_job(self, **kwargs):
            return {
                "job_id": "pbs-001",
                "resolved_job_state": "C",
                "downloaded_files": [str(odb_file)],
            }

    monkeypatch.setattr(pbs_service, "PBSClient", _FakeClient)
    monkeypatch.setattr(
        pbs_service,
        "update_work_condition_project_status",
        lambda project_id, **fields: captured.setdefault("status_updates", []).append((project_id, fields)),
    )
    monkeypatch.setattr(
        pbs_service,
        "_submit_local_project_result_group_and_wait",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("parse failed")),
    )

    with pytest.raises(RuntimeError):
        pbs_service.run_pbs_solver_job(
            project_id=32,
            application="Abaqus",
            input_file=str(input_file),
            job_name="job_a",
            wait=True,
        )

    assert captured["status_updates"][-1] == (32, {"simulation_result_status": 2})
