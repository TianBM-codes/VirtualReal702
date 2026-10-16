import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from webapi.models import SensorNodeHistoryRequest
from services.model_update.analysis import sensor_node_history_service as service
from src.l3.core.errors import ValidationError


@pytest.mark.parametrize("point", [
    {"name": " ", "direction": [1, 0, 0]},
    {"name": "A", "direction": [0, 0, 0]},
    {"name": "A", "direction": [float("nan"), 0, 0]},
    {"name": "A", "direction": [1, 0]},
])
def test_invalid_direction(point):
    with pytest.raises(ValueError):
        SensorNodeHistoryRequest(project_id=1, sensors=[point])


def test_request_normalization_and_duplicates():
    point = {"name": " SG1 ", "direction": [0, 3, 4]}
    request = SensorNodeHistoryRequest(project_id=1, sensors=[point], fields=" le ")
    assert request.sensors[0].direction == [0, .6, .8]
    assert request.fields == "LE"
    with pytest.raises(ValueError):
        SensorNodeHistoryRequest(project_id=1, sensors=[point], fields=["U", "E"])
    with pytest.raises(ValueError):
        SensorNodeHistoryRequest(project_id=1, sensors=[point, point])


def test_group_selection_and_source(monkeypatch, tmp_path):
    source = tmp_path / "result.odb"
    source.touch()
    groups = [{"result_group": "other", "status": "ready", "source_path": str(source)}]
    repo = types.SimpleNamespace(get_project=lambda pid: {"workspace": str(tmp_path)},
                                 list_result_groups=lambda pid: groups,
                                 resolve_workspace=lambda workspace, root: workspace)
    monkeypatch.setattr(service, "RegistryRepo", lambda path: repo)
    assert service._resolve_source(7, None) == (str(source), "other")
    groups.append(dict(groups[0], result_group="second"))
    with pytest.raises(ValidationError):
        service._resolve_source(7, None)
    groups.append(dict(groups[0], result_group="default_result"))
    assert service._resolve_source(7, None)[1] == "default_result"


def test_database_matches_are_read_only_and_names_bound(monkeypatch):
    class Cursor:
        def execute(self, sql, params):
            assert sql.lstrip().startswith("SELECT")
            assert params == (7, "SG'1")

        def fetchall(self):
            return [{"instance_name": "P-1", "fem_node_label": 5, "distance": .2,
                     "x_position": 1, "y_position": 2, "z_position": 3}]

        def close(self):
            pass

    monkeypatch.setattr(service, "get_connection", lambda: types.SimpleNamespace(
        cursor=lambda **kwargs: Cursor(), close=lambda: None))
    result = service._load_matches(7, [{"name": "SG'1", "direction": [1, 0, 0]}])
    assert result[0]["node_label"] == 5
    assert result[0]["coordinates"] == [1., 2., 3.]


def test_service_launch_and_cleanup(monkeypatch):
    monkeypatch.setattr(service, "_resolve_source", lambda *args: ("job.odb", "default_result"))
    monkeypatch.setattr(service, "_load_matches", lambda *args: [{"name": "SG1"}])
    monkeypatch.setattr(service, "resolve_abaqus_command", lambda: "abaqus")
    files = []

    def run(command, **kwargs):
        assert command[:2] == ["abaqus", "python"]
        assert Path(command[2]).is_file()
        files.append(Path(command[3]))
        assert json.loads(files[0].read_text())["sensors"] == [{"name": "SG1"}]
        Path(command[4]).write_text('{"sensors": []}')
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(service.subprocess, "run", run)
    assert service.get_sensor_node_history(7, [], "U")["project_id"] == 7
    assert not files[0].exists()


def test_worker_all_frames_fallback_sections_and_direction_failures(monkeypatch):
    # Reuse the existing minimal ODB objects; the real Abaqus transformation
    # itself still requires a real ODB/CAE comparison.
    from test_odb_node_history_sensors import Asm, Instance, Value
    monkeypatch.setitem(sys.modules, "odbAccess", types.ModuleType("odbAccess"))
    monkeypatch.delitem(sys.modules, "tools.odb_node_history", raising=False)
    import tools.odb_node_history as history
    monkeypatch.setattr(history, "_ELEM_NODAL", "ELEMENT_NODAL")
    monkeypatch.setattr(history, "_CARTESIAN", "CARTESIAN")
    monkeypatch.setitem(sys.modules, "odb_node_history", history)
    spec = importlib.util.spec_from_file_location("history_worker", Path(__file__).parents[1] / "tools/odb_sensor_history.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    def transformed(datumCsys):
        if datumCsys == "NODE_HISTORY_DIR_1":
            raise RuntimeError("direction unavailable")
        return types.SimpleNamespace(values=[Value(5, (10.,), "SNEG"), Value(5, (20.,), "SNEG"),
                                            Value(5, (30.,), "SPOS")])

    tensor = types.SimpleNamespace(componentLabels=["LE11"], getSubset=lambda **kwargs:
        types.SimpleNamespace(values=[Value(5, (1.,), "SNEG"), Value(5, (3.,), "SPOS")],
                              getTransformedField=transformed))
    vector = types.SimpleNamespace(componentLabels=["U1", "U2", "U3"], getSubset=lambda **kwargs:
        types.SimpleNamespace(values=[Value(5, (1., 2., 3.))]))
    frame = types.SimpleNamespace(frameValue=1., description="frame", fieldOutputs={"LE": tensor, "U": vector})
    odb = types.SimpleNamespace(rootAssembly=Asm([Instance("P-1", [(5, (0, 0, 0))])]), parts={},
        steps={"Step-1": types.SimpleNamespace(frames=[frame, frame]),
               "Step-2": types.SimpleNamespace(frames=[frame])})
    sensors = [dict(name=name, direction=direction, instance_name="P-1", node_label=5,
                    coordinates=[0, 0, 0], distance=0) for name, direction in
               [("SG1", [1, 0, 0]), ("SG2", [0, 1, 0])]]
    result = worker.extract(odb, {"sensors": sensors, "fields": ["U", "E", "S"]})
    first, second = result["sensors"]
    assert [len(step["frames"]) for step in first["steps"]] == [2, 1]
    fields = first["steps"][0]["frames"][0]["fields"]
    assert fields[0]["values"][0]["projection"] == 1
    assert fields[1]["field"] == "LE"
    assert [(row["section_point"], row["projection"]) for row in fields[1]["values"]] == [("SNEG", 15), ("SPOS", 30)]
    assert fields[2]["status"] == "missing_field"
    other = second["steps"][0]["frames"][0]["fields"]
    assert other[0]["values"][0]["projection"] == 2
    assert other[1]["status"] == "projection_failed"
    assert other[1]["values"][0]["projection"] is None
    json.dumps(result, allow_nan=False)


def test_route(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    from webapi.routers import matching

    async def noop_log(*args):
        pass

    monkeypatch.setattr(matching, "log_request", noop_log)
    monkeypatch.setattr(matching, "get_sensor_node_history", lambda **kwargs: kwargs)
    app = FastAPI()
    app.include_router(matching.router)
    assert "/get/sensor_node_history" in app.openapi()["paths"]
    response = asyncio.run(matching.get_sensor_node_history_api(object(),
        SensorNodeHistoryRequest(project_id=7, sensors=[{"name": "SG1", "direction": [0, 3, 4]}])))
    assert response["ok"] is True
    assert response["data"]["sensors"][0]["direction"] == [0, .6, .8]


def test_curve_response_preserves_frames_sections_and_missing_values():
    def field(values, status="ok", name="LE"):
        return {"field": name, "values": values, "status": status}

    def value(section, projection, status="ok"):
        return {"section_point": section, "projection": projection, "status": status}

    sensor = {"name": "SG1", "coordinates": [0, 0, 0], "direction": [1, 0, 0],
              "match": {"instance_name": "P-1", "node_label": 1}, "steps": [
        {"step": "Step-1", "frames": [
            {"frame_idx": 0, "frame_value": 0., "fields": [field([], "missing_field")]},
            {"frame_idx": 1, "frame_value": 1., "fields": [field([value("SNEG", 2.), value("SPOS", 3.)])]}]},
        {"step": "Step-2", "frames": [
            {"frame_idx": 0, "frame_value": 0., "fields": [field([value("SNEG", None, "projection_failed")], "projection_failed")]}]}
    ]}
    result = service._history_curves({"project_id": 7, "result_group": "default_result", "sensors": [sensor]}, "E")
    assert result["data"] == [
        {"label": "SG1|Step-1|E|SNEG", "data": [["0.0", None], ["1.0", 2.]], "xaxis": ["0.0", "1.0"]},
        {"label": "SG1|Step-1|E|SPOS", "data": [["0.0", None], ["1.0", 3.]], "xaxis": ["0.0", "1.0"]},
        {"label": "SG1|Step-2|E|SNEG", "data": [["0.0", None]], "xaxis": ["0.0"]}]
    assert result["actual_fields"] == ["LE"]
    assert result["summary"]["curve_count"] == 3
    assert len(result["issues"]) == 2


def test_service_rejects_multiple_fields_before_extraction():
    with pytest.raises(ValidationError):
        service.get_sensor_node_history(7, [], ["U", "S"])
