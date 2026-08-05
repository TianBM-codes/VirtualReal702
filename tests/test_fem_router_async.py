import asyncio


class _FakeRequest:
    pass


def test_import_fem_static_from_project_result_route_submits_background_task_by_default(monkeypatch):
    from webapi.models import ImportProjectStaticResultRequest
    from webapi.routers import fem

    captured = {}

    async def _noop_log_request(request, body):
        return None

    monkeypatch.setattr(fem, "log_request", _noop_log_request)
    monkeypatch.setattr(
        fem,
        "submit_background_task",
        lambda **kwargs: captured.update(kwargs) or {
            "task_id": "task-static-1",
            "status": "submitted",
            "task_type": kwargs["task_type"],
            "interface_code": kwargs["interface_code"],
            "request": kwargs["request_payload"],
        },
    )

    body = ImportProjectStaticResultRequest(
        project_id=101,
        result_group="rg_static",
        load_case_no=7,
    )

    response = asyncio.run(
        fem.import_fem_static_from_project_result_api(_FakeRequest(), body)
    )

    assert response["code"] == 200
    assert response["data"]["task_id"] == "task-static-1"
    assert response["data"]["task_type"] == "import.fem.static.from_project_result"
    assert captured["project_id"] == 101
    assert captured["kwargs"] == {
        "project_id": 101,
        "result_group": "rg_static",
        "load_case_no": 7,
        "step": None,
        "frame": None,
        "instances": None,
        "overwrite": True,
    }
    assert captured["request_payload"]["async_submit"] is True


def test_import_fem_static_from_project_result_route_supports_sync_override(monkeypatch):
    from webapi.models import ImportProjectStaticResultRequest
    from webapi.routers import fem

    captured = {}

    async def _noop_log_request(request, body):
        return None

    monkeypatch.setattr(fem, "log_request", _noop_log_request)
    monkeypatch.setattr(
        fem,
        "_import_fem_static_from_project_result_job",
        lambda **kwargs: captured.update(kwargs) or {
            "project_id": kwargs["project_id"],
            "result_group": kwargs["result_group"],
            "row_count": 12,
        },
    )

    body = ImportProjectStaticResultRequest(
        project_id=101,
        result_group="rg_static",
        load_case_no=7,
        async_submit=False,
    )

    response = asyncio.run(
        fem.import_fem_static_from_project_result_api(_FakeRequest(), body)
    )

    assert response["code"] == 200
    assert response["data"]["row_count"] == 12
    assert captured["project_id"] == 101
    assert captured["result_group"] == "rg_static"
    assert captured["load_case_no"] == 7


def test_import_fem_static_from_project_result_route_accepts_step_name_and_frame_idx(monkeypatch):
    from webapi.models import ImportProjectStaticResultRequest
    from webapi.routers import fem

    captured = {}

    async def _noop_log_request(request, body):
        return None

    monkeypatch.setattr(fem, "log_request", _noop_log_request)
    monkeypatch.setattr(
        fem,
        "_import_fem_static_from_project_result_job",
        lambda **kwargs: captured.update(kwargs) or {
            "project_id": kwargs["project_id"],
            "result_group": kwargs["result_group"],
            "step_name": kwargs["step"],
            "frame_idx": kwargs["frame"],
        },
    )

    body = ImportProjectStaticResultRequest(
        project_id=101,
        result_group="rg_static",
        load_case_no=7,
        step_name="Step-2",
        frame_idx=3,
        async_submit=False,
    )

    response = asyncio.run(
        fem.import_fem_static_from_project_result_api(_FakeRequest(), body)
    )

    assert response["code"] == 200
    assert response["data"]["step_name"] == "Step-2"
    assert response["data"]["frame_idx"] == 3
    assert captured["step"] == "Step-2"
    assert captured["frame"] == 3


def test_project_result_steps_route_returns_catalog(monkeypatch):
    from webapi.models import ProjectResultStepCatalogRequest
    from webapi.routers import fem

    async def _noop_log_request(request, body):
        return None

    monkeypatch.setattr(fem, "log_request", _noop_log_request)
    monkeypatch.setattr(
        fem,
        "list_project_result_steps",
        lambda **kwargs: {
            "project_id": kwargs["project_id"],
            "result_group": kwargs["result_group"],
            "steps": [
                {
                    "step_name": "Step-1",
                    "frames": [{"frame_idx": 0, "frame_value": 1.0, "description": "final"}],
                    "fields": [{"field_name": "U"}],
                    "default_frame_idx": 0,
                }
            ],
        },
    )

    body = ProjectResultStepCatalogRequest(project_id=101, result_group="rg_static")
    response = asyncio.run(fem.get_project_result_steps_api(_FakeRequest(), body))

    assert response["code"] == 200
    assert response["data"]["project_id"] == 101
    assert response["data"]["result_group"] == "rg_static"
    assert response["data"]["steps"][0]["step_name"] == "Step-1"
