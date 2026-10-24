import asyncio
from decimal import Decimal
import json
from pathlib import Path

import pytest

from src.l3.core.errors import AppError, ValidationError
from services.model_update.analysis import test_load_level_service as imports
from services.model_update.analysis import simright_load_level_service as service
from webapi.load_level_models import ImportTestLoadLevelRequest, SimrightLoadLevelComparisonRequest


def make_txt(path, *, axes=None, gauges=None):
    axes = axes or {"Level": [0, 1, 0], "Factor": [0, .5, 0], "Temperature": [20, 21, 22]}
    gauges = gauges or {"UP": [0, 10, 2, 999], "DOWN": [0, -10, -2, -999]}
    lines = ["Test Strain Unit 2026.09.12 01:10:03", "-----"]
    lines += [f"{name:19}" + " ".join(str(value) for value in values) for name, values in axes.items()]
    lines += ["Number Name"]
    for index, (name, values) in enumerate(gauges.items(), 1):
        lines.append(f"{index:6d}{name:13}{values[0]:15}" + "".join(f"{value:9}" for value in values[1:]))
    path.write_bytes("\r\n".join(lines).encode("gb18030"))
    return path


def test_parse_preserves_dynamic_headers_repeated_levels_and_excluded_values(tmp_path):
    path = make_txt(tmp_path / "trial.txt")
    meta, rows = imports.prepare_import(path, "http://example/trial.txt", unit="microstrain")
    assert len(meta["axis_rows"]) == 3
    assert meta["sensor_names"] == ["UP", "DOWN"]
    assert meta["ignored_trailing_values"] == {"UP": 999, "DOWN": -999}
    assert [row[1] for row in rows[:3]] == [0, 1, 2]
    assert [row[2] for row in rows[:3]] == [Decimal(0), Decimal(1), Decimal(0)]
    assert [row[3] for row in rows[:3]] == [0, 10, 2]
    assert meta["page_headers"] and meta["start_datetime"] == "2026-09-12T01:10:03"
    assert "frequency" not in meta


def test_mismatch_rejected_without_silent_truncation(tmp_path):
    path = make_txt(tmp_path / "trial.txt", gauges={"UP": [0, 1, 2]})
    with pytest.raises(ValidationError):
        imports.prepare_import(path, "local")
    meta, rows = imports.prepare_import(path, "local", ignore_last_reading=False)
    assert len(rows) == 3 and meta["ignored_trailing_values"] == {}


def test_different_page_axes_rejected(tmp_path):
    one = make_txt(tmp_path / "one.txt")
    two = make_txt(tmp_path / "two.txt", axes={"Level": [0, 2, 0]})
    combined = tmp_path / "both.txt"
    combined.write_bytes(one.read_bytes() + b"\x0c" + two.read_bytes())
    with pytest.raises(ValidationError):
        imports.prepare_import(combined, "local")


class Cursor:
    def __init__(self):
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, params):
        self.executed.append((sql, params))

    def fetchall(self):
        return []

    def close(self):
        self.closed = True


class Connection:
    def __init__(self):
        self.q = Cursor()
        self.commits = self.rollbacks = 0

    def cursor(self, **kwargs):
        return self.q

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


@pytest.mark.parametrize("fail", [False, True])
def test_download_parse_and_metadata_share_transaction(monkeypatch, tmp_path, fail):
    source = make_txt(tmp_path / "source.txt")
    monkeypatch.setattr(imports, "resolve_project_workspace", lambda pid: str(tmp_path / "project"))
    downloaded = []

    def download(url, folder, dest_name):
        downloaded.append(url)
        path = Path(folder) / dest_name
        path.write_bytes(source.read_bytes())
        return str(path)

    monkeypatch.setattr(imports, "materialize_source_file", download)
    monkeypatch.setattr(imports, "ensure_test_load_level_schema", lambda cursor: None)
    connection = Connection()
    monkeypatch.setattr(imports, "get_connection", lambda: connection)

    def save(project_id, *, extra_json, cursor):
        assert cursor is connection.q
        assert extra_json["test_load_level_import"]["sample_count"] == 3
        if fail:
            raise RuntimeError("metadata write failed")

    monkeypatch.setattr(imports, "upsert_project_config", save)
    if fail:
        with pytest.raises(RuntimeError):
            imports.import_test_load_level(7, "http://example/trial.txt")
        assert connection.rollbacks == 1 and connection.commits == 1
    else:
        result = imports.import_test_load_level(7, "http://example/trial.txt")
        assert result["row_count"] == 6 and connection.commits == 2
    assert downloaded == ["http://example/trial.txt"]
    delete = next(item for item in connection.q.executed if item[0].startswith("DELETE"))
    assert delete[1] == (7, "", "")
    assert connection.q.closed


@pytest.fixture
def comparison(monkeypatch):
    meta = {"axis_label": "Level", "axis_rows": [
        {"label": "Level", "value": [0, 1, 0]}, {"label": "Factor", "value": [0, .5, 0]}],
        "sensor_names": ["UP", "DOWN"], "sample_count": 3, "unit": "mm", "sha256": "abc"}
    monkeypatch.setattr(service, "load_test_data", lambda pid, **kwargs: (meta, {"UP": [0, 1, .1], "DOWN": [0, -1, -.1]}))
    config = {"filename": "model.rdb", "base_url": "http://simright", "sensor_bindings": {
        "UP": dict(node_id=10, part_id=5, quantity="scalar", var_name="D.N.X", simulation_scale=2)}}
    monkeypatch.setattr(service, "get_project_config", lambda pid: {"extra_json": {"simright": config}})
    connection = Connection()
    connection.q.fetchall = lambda: [{"instance_name": "PART-1", "fem_node_label": 10}]
    monkeypatch.setattr(service, "get_connection", lambda: connection)
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def loadcases(self, filename):
            assert filename == "model.rdb"
            return {"value": [{"id": 42, "name": "Step-A"}, {"id": 91, "name": "Step-B"}]}
        def node_info(self, filename, nodes, variable, **options):
            calls.append((variable, options))
            assert options["loadcaseIndex"] in (0, 1)
            assert options["stateIds"] == []
            return [{"nodeId": nodes[0], "partId": 5, "stateId": state,
                     "values": [float(state)]} for state in [7, 2]]
    monkeypatch.setattr(service, "SimRightClient", Client)
    return config, calls, connection


def query(**kwargs):
    return service.get_simright_test_load_level_comparison(7, example_mode=False, **kwargs)


@pytest.mark.parametrize("sensors", [None, []])
def test_initial_response_only_contains_axes_and_step_options(comparison, sensors):
    result = query(sensors=sensors)
    assert [row["key"] for row in result["table"]] == ["axis:0", "axis:1", "simright_step"]
    assert result["table"][-1]["value"] == [None] * 3
    assert result["table"][-1]["selectOptions"] == [{"label": "Step-A", "value": 42}, {"label": "Step-B", "value": 91}]
    assert result["curves"] == [] and comparison[1] == []


def test_selected_steps_latest_frame_nulls_and_cache(comparison):
    result = query(sensors=[{"name": "UP"}], steps=[42, None, 42])
    assert result["curves"][0]["data"] == [["0", 0], ["1", 1], ["0", .1]]
    assert result["curves"][-1]["data"] == [["0", 14.], ["1", None], ["0", 14.]]
    assert all(row["state_id"] == 7 for row in result["resolved_states"])
    assert not result["issues"] and len(comparison[1]) == 1
    assert [row["key"] for row in result["table"]] == ["axis:0", "axis:1", "simright_step"]


def test_no_selected_steps_does_not_resolve_nodes(comparison, monkeypatch):
    monkeypatch.setattr(service, "_resolve_bindings", lambda *args: pytest.fail("Unexpected node lookup"))
    result = query(sensors=[{"name": "UP"}])
    assert all(point[1] is None for point in result["curves"][-1]["data"])
    assert comparison[1] == []


@pytest.mark.parametrize("steps", [[42], [], [None, 0, None]])
def test_invalid_steps_rejected(comparison, steps):
    with pytest.raises(ValidationError):
        query(sensors=[{"name": "UP"}], steps=steps)


def test_unknown_sensor_rejected(comparison):
    with pytest.raises(ValidationError):
        query(sensors=[{"name": "UNKNOWN"}])


def test_stale_import_rejected(comparison):
    with pytest.raises(AppError) as exc:
        query(import_sha256="old")
    assert exc.value.status_code == 409


def test_saved_node_match_and_server_side_part_config(comparison):
    config, calls, connection = comparison
    config["sensor_bindings"]["UP"].pop("node_id")
    config["instance_parts"] = {"PART-1": {"part_id": 5, "part_ori_id": 4}}
    query(sensors=[{"name": "UP"}], steps=[42, None, None])
    assert connection.q.executed[0][1] == (7, "UP")
    assert calls[0][1]["entitiesIds"] == [4]


def test_missing_match_or_result_config_rejected(comparison):
    config, calls, connection = comparison
    config["sensor_bindings"] = {}
    with pytest.raises(ValidationError):
        query(sensors=[{"name": "UP"}], steps=[42, None, None])
    connection.q.fetchall = lambda: []
    with pytest.raises(ValidationError):
        query(sensors=[{"name": "UP"}], steps=[42, None, None])


def test_server_side_tensor_projection(comparison):
    comparison[0]["sensor_bindings"]["UP"] = dict(node_id=10, part_id=5, quantity="stress", direction=[1, 0, 0])
    result = query(sensors=[{"name": "UP"}], steps=[42, None, None])
    assert result["curves"][-1]["data"][0] == ["0", 7.]
    assert len(comparison[1]) == 6


def test_tensor_components_cannot_mix_frames(comparison, monkeypatch):
    comparison[0]["sensor_bindings"]["UP"] = dict(node_id=10, part_id=5, quantity="stress", direction=[1, 0, 0])
    def node_info(client, filename, nodes, variable, **options):
        return [{"nodeId": 10, "partId": 5, "stateId": 2 if variable.endswith("12") else 7, "values": [1.]}]
    monkeypatch.setattr(service.SimRightClient, "node_info", node_info)
    result = query(sensors=[{"name": "UP"}], steps=[42, None, None])
    assert result["curves"][-1]["data"][0][1] is None
    assert "identities" in result["issues"][0]["message"]


def test_ambiguous_corner_values_return_null_and_issue(comparison, monkeypatch):
    monkeypatch.setattr(service.SimRightClient, "node_info", lambda *args, **kwargs:
        [{"nodeId": 10, "partId": 5, "stateId": 7, "values": [1, 2]}])
    result = query(sensors=[{"name": "UP"}], steps=[42, None, None])
    assert result["curves"][-1]["data"][0] == ["0", None]
    assert result["issues"] and not result["resolved_states"]


def test_vendor_failure_produces_502(comparison, monkeypatch):
    def fail(*args):
        raise service.SimRightError("offline")
    monkeypatch.setattr(service.SimRightClient, "loadcases", fail)
    with pytest.raises(AppError) as exc:
        query()
    assert exc.value.status_code == 502


def test_missing_project_connection_config(comparison):
    comparison[0].clear()
    with pytest.raises(ValidationError):
        query()


@pytest.mark.parametrize("extra", [
    {"filename": "model"}, {"step_mapping": []},
    {"sensors": [{"name": "UP", "node_id": 10}]},
    {"sensors": [{"name": "UP"}, {"name": " UP "}]},
    {"sensors": [{"name": " "}]}])
def test_removed_fields_and_invalid_names_rejected(extra):
    with pytest.raises(ValueError):
        SimrightLoadLevelComparisonRequest(project_id=7, **extra)


def test_example_mode_header_and_filtered_curves_without_external_data(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Example mode must not query external data")
    monkeypatch.setattr(service, "get_project_config", unexpected)
    monkeypatch.setattr(service, "load_test_data", unexpected)
    monkeypatch.setattr(service, "SimRightClient", unexpected)
    result = service.get_simright_test_load_level_comparison(7)
    assert result["is_example"] and result["data_source"] == "example"
    assert result["curves"] == []
    assert [row["key"] for row in result["table"]] == ["axis:0", "axis:1", "axis:2", "simright_step"]
    assert result["table"][-1]["value"] == [None] * 4
    selected = service.get_simright_test_load_level_comparison(7, sensors=[{"name": "310004a"}], steps=[3, None, 1, 0])
    assert len(selected["curves"]) == 2
    assert [row["key"] for row in selected["table"]] == ["axis:0", "axis:1", "axis:2", "simright_step"]
    assert selected["curves"][-1]["data"] == [["0", 29.5], ["1", None], ["2", 9.5], ["3", 0]]
    selected["curves"][0]["data"].clear()
    assert service.get_simright_test_load_level_comparison(7, sensors=[{"name": "310004a"}])["curves"][0]["data"]
    with pytest.raises(ValidationError):
        service.get_simright_test_load_level_comparison(7, sensors=[{"name": "bad"}])


def test_route_and_openapi(monkeypatch):
    from fastapi import FastAPI
    from webapi.routers import test_data
    async def no_log(*args):
        pass
    monkeypatch.setattr(test_data, "log_request", no_log)
    app = FastAPI()
    app.include_router(test_data.router)
    path = "/get/simright_test_load_level_comparison"
    schema = app.openapi()["components"]["schemas"]["SimrightLoadLevelComparisonRequest"]["properties"]
    assert "filename" not in schema and "step_mapping" not in schema and "steps" in schema
    body = SimrightLoadLevelComparisonRequest(project_id=42)
    response = asyncio.run(test_data.get_simright_test_load_level_comparison_api(object(), body))
    assert response["data"]["curves"] == []
    body = SimrightLoadLevelComparisonRequest(project_id=42, sensors=[{"name": "310004a"}], steps=[0, None, 2, 3],
        work_condition_id="condition-001", measurement_id="measurement-001")
    response = asyncio.run(test_data.get_simright_test_load_level_comparison_api(object(), body))
    assert response["ok"] and len(response["data"]["curves"]) == 2
