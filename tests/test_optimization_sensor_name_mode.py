from webapi.models import CreateAbaqusStaticResponseRequest
from webapi.routers import optimization


def test_create_abaqus_static_response_catalog_accepts_uy_in_sensor_mode(monkeypatch):
    captured = {}

    def fake_resolve_step_name(project_id, step_name):
        return "Step-2"

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

    monkeypatch.setattr(optimization, "_resolve_abaqus_static_response_step_name", fake_resolve_step_name)
    monkeypatch.setattr(optimization, "resolve_abaqus_sensor_node_match", fake_resolve_abaqus_sensor_node_match)
    monkeypatch.setattr(optimization, "resolve_abaqus_instance_context", fake_resolve_abaqus_instance_context)
    monkeypatch.setattr(optimization, "create_design_response_catalog_entry", fake_create_design_response_catalog_entry)

    body = CreateAbaqusStaticResponseRequest(
        project_id=5,
        region_type="NODE",
        sensor_name=["WY1", "WY2"],
        variables=["UY"],
        frequency=1,
    )

    result = optimization._create_abaqus_static_response_catalog(body)

    assert result["variables"] == ["UY"]
    assert captured["variables"] == ["UY"]
    assert captured["instance_name"] == "PART-1-1"
    assert captured["node_labels"] == [4, 6]
    assert captured["step_name"] == "Step-2"
