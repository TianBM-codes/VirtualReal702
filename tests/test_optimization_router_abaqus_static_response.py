import pytest


def test_abaqus_static_response_meta_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    def fake_get_project_abaqus_instances_and_steps(project_id):
        return {
            "project_id": project_id,
            "instances": [{"instance_name": "PART-1-1", "part_name": "PART-1", "set_scope": "ASSEMBLY"}],
            "steps": [{"step_name": "Step-1", "step_index": 0}],
        }

    monkeypatch.setattr(
        optimization,
        "get_project_abaqus_instances_and_steps",
        fake_get_project_abaqus_instances_and_steps,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post("/optimization/abaqus/static_response/meta", json={"project_id": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["instances"][0]["instance_name"] == "PART-1-1"
    assert payload["data"]["steps"][0]["step_name"] == "Step-1"


def test_abaqus_static_response_node_validate_route(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    def fake_validate_abaqus_instance_node_label(project_id, instance_name, node_label):
        return {
            "project_id": project_id,
            "instance_name": instance_name,
            "part_name": "PART-1",
            "set_scope": "ASSEMBLY",
            "node_label": node_label,
            "exists": True,
        }

    monkeypatch.setattr(
        optimization,
        "validate_abaqus_instance_node_label",
        fake_validate_abaqus_instance_node_label,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/abaqus/static_response/node/validate",
        json={"project_id": 5, "instance_name": "PART-1-1", "node_label": 4},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["exists"] is True
    assert payload["data"]["node_label"] == 4


def test_create_abaqus_static_response_route_autofills_instance_context(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    captured = {}

    def fake_resolve_abaqus_instance_context(project_id, instance_name):
        return {
            "project_id": project_id,
            "instance_name": instance_name,
            "part_name": "PART-1",
            "set_scope": "ASSEMBLY",
        }

    def fake_create_design_response_catalog_entry(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        optimization,
        "resolve_abaqus_instance_context",
        fake_resolve_abaqus_instance_context,
    )
    monkeypatch.setattr(
        optimization,
        "create_design_response_catalog_entry",
        fake_create_design_response_catalog_entry,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/abaqus/static_response/create",
        json={
            "project_id": 5,
            "region_type": "NODE",
            "variables": ["U2"],
            "node_labels": [4],
            "instance_name": "PART-1-1",
            "step_name": "Step-1",
            "frequency": 1,
        },
    )

    assert response.status_code == 200
    assert captured["set_scope"] == "ASSEMBLY"
    assert captured["part_name"] == "PART-1"
    assert captured["instance_name"] == "PART-1-1"
    assert captured["node_labels"] == [4]


def test_create_abaqus_static_response_route_defaults_to_last_step_for_sensor_mode(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    captured = {}

    def fake_get_project_abaqus_instances_and_steps(project_id):
        return {
            "project_id": project_id,
            "steps": [
                {"step_name": "Step-1", "step_index": 0},
                {"step_name": "Step-2", "step_index": 1},
            ],
        }

    def fake_resolve_abaqus_sensor_node_match(project_id, sensor_name):
        node_map = {"WY1": 4, "WY2": 6}
        return {"project_id": project_id, "instance_name": "PART-1-1", "node_label": node_map[sensor_name]}

    def fake_resolve_abaqus_instance_context(project_id, instance_name):
        return {
            "project_id": project_id,
            "instance_name": instance_name,
            "part_name": "PART-1",
            "set_scope": "ASSEMBLY",
        }

    def fake_create_design_response_catalog_entry(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        optimization,
        "get_project_abaqus_instances_and_steps",
        fake_get_project_abaqus_instances_and_steps,
    )
    monkeypatch.setattr(
        optimization,
        "resolve_abaqus_sensor_node_match",
        fake_resolve_abaqus_sensor_node_match,
    )
    monkeypatch.setattr(
        optimization,
        "resolve_abaqus_instance_context",
        fake_resolve_abaqus_instance_context,
    )
    monkeypatch.setattr(
        optimization,
        "create_design_response_catalog_entry",
        fake_create_design_response_catalog_entry,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/abaqus/static_response/create",
        json={
            "project_id": 5,
            "region_type": "NODE",
            "sensor_name": ["WY1", "WY2"],
            "variables": ["U2"],
            "frequency": 1,
        },
    )

    assert response.status_code == 200
    assert captured["step_name"] == "Step-2"
    assert captured["instance_name"] == "PART-1-1"
    assert captured["node_labels"] == [4, 6]


def test_create_abaqus_static_response_route_accepts_ux_uy_uz_in_sensor_mode(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    captured = {}

    def fake_get_project_abaqus_instances_and_steps(project_id):
        return {
            "project_id": project_id,
            "steps": [{"step_name": "Step-2", "step_index": 1}],
        }

    def fake_resolve_abaqus_sensor_node_match(project_id, sensor_name):
        node_map = {"WY1": 4, "WY2": 6}
        return {"project_id": project_id, "instance_name": "PART-1-1", "node_label": node_map[sensor_name]}

    def fake_resolve_abaqus_instance_context(project_id, instance_name):
        return {
            "project_id": project_id,
            "instance_name": instance_name,
            "part_name": "PART-1",
            "set_scope": "ASSEMBLY",
        }

    def fake_create_design_response_catalog_entry(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        optimization,
        "get_project_abaqus_instances_and_steps",
        fake_get_project_abaqus_instances_and_steps,
    )
    monkeypatch.setattr(
        optimization,
        "resolve_abaqus_sensor_node_match",
        fake_resolve_abaqus_sensor_node_match,
    )
    monkeypatch.setattr(
        optimization,
        "resolve_abaqus_instance_context",
        fake_resolve_abaqus_instance_context,
    )
    monkeypatch.setattr(
        optimization,
        "create_design_response_catalog_entry",
        fake_create_design_response_catalog_entry,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/abaqus/static_response/create",
        json={
            "project_id": 5,
            "region_type": "NODE",
            "sensor_name": ["WY1", "WY2"],
            "variables": ["UY"],
            "frequency": 1,
        },
    )

    assert response.status_code == 200
    assert captured["variables"] == ["UY"]
    assert captured["step_name"] == "Step-2"
    assert captured["instance_name"] == "PART-1-1"
    assert captured["node_labels"] == [4, 6]


def test_create_abaqus_static_response_route_rejects_cross_instance_sensor_list(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    def fake_get_project_abaqus_instances_and_steps(project_id):
        return {
            "project_id": project_id,
            "steps": [{"step_name": "Step-1", "step_index": 0}],
        }

    def fake_resolve_abaqus_sensor_node_match(project_id, sensor_name):
        if sensor_name == "WY1":
            return {"project_id": project_id, "instance_name": "PART-1-1", "node_label": 4}
        return {"project_id": project_id, "instance_name": "PART-2-1", "node_label": 8}

    monkeypatch.setattr(
        optimization,
        "get_project_abaqus_instances_and_steps",
        fake_get_project_abaqus_instances_and_steps,
    )
    monkeypatch.setattr(
        optimization,
        "resolve_abaqus_sensor_node_match",
        fake_resolve_abaqus_sensor_node_match,
    )

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/abaqus/static_response/create",
        json={
            "project_id": 5,
            "region_type": "NODE",
            "sensor_name": ["WY1", "WY2"],
            "variables": ["U2"],
            "frequency": 1,
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert "same instance" in payload["message"]


def test_create_abaqus_static_response_route_rejects_removed_fields():
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapi.routers import optimization

    app = FastAPI()
    app.include_router(optimization.router)
    client = TestClient(app)

    response = client.post(
        "/optimization/abaqus/static_response/create",
        json={
            "project_id": 5,
            "region_type": "NODE",
            "variables": ["U2"],
            "node_labels": [4],
            "instance_name": "PART-1-1",
            "part_name": "PART-1",
            "set_scope": "ASSEMBLY",
            "step_name": "Step-1",
            "frequency": 1,
        },
    )

    assert response.status_code == 422
