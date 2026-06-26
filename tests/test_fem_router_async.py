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
