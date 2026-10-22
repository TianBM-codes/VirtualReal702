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
    assert delete[1] == (7,)
    assert connection.q.closed


@pytest.fixture
def comparison(monkeypatch):
    meta = {"axis_label": "Level", "axis_rows": [
        {"label": "Level", "value": [0, 1, 0]}, {"label": "Factor", "value": [0, .5, 0]}],
        "sensor_names": ["UP", "DOWN"], "sample_count": 3, "unit": "mm", "sha256": "abc"}
    monkeypatch.setattr(service, "load_test_data", lambda project_id: (meta, {"UP": [0, 1, .1], "DOWN": [0, -1, -.1]}))
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def loadcases(self, filename):
            return {"value": [{"id": 42, "name": "Step-A"}, {"id": 91, "name": "Step-B"}]}

        def node_info(self, filename, nodes, variable, **options):
            calls.append((variable, options))
            assert options["loadcaseIndex"] in (0, 1)  # IDs 42/91 are NOT indices.
            states = options["stateIds"] or [2, 7]
            return [{"nodeId": nodes[0], "partId": 5, "stateId": state,
                     "values": [float(state)]} for state in reversed(states)]

    monkeypatch.setattr(service, "SimRightClient", Client)
    return meta, calls


def binding(**kwargs):
    return dict(name="UP", node_id=10, part_id=5, quantity="scalar", var_name="D.N.X", **kwargs)


def test_initial_table_uses_all_file_headers_and_does_not_query_values(comparison):
    result = service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False)
    assert [row["label"] for row in result["table"]] == ["Level", "Factor", "UP", "DOWN", "仿真step"]
    assert result["table"][-1]["value"] == [None] * 3
    assert result["table"][-1]["selectOptions"] == [{"label": "Step-A", "value": 42}, {"label": "Step-B", "value": 91}]
    assert result["curves"][0]["data"] == [["0", 0], ["1", 1], ["0", .1]]
    assert comparison[1] == []


def test_selected_step_latest_frame_and_explicit_state(comparison):
    result = service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False,
        sensors=[binding(simulation_scale=2)], step_mapping=[
            {"sample_index": 1, "loadcase_id": 42}, {"sample_index": 2, "loadcase_id": 91, "state_id": 2}])
    assert result["curves"][-1]["data"] == [["0", None], ["1", 14.], ["0", 4.]]
    assert [state["state_id"] for state in result["resolved_states"]] == [7, 2]
    assert not result["issues"]


def test_tensor_projection(comparison):
    tensor = dict(name="UP", node_id=10, part_id=5, quantity="stress", direction=[1, 0, 0])
    result = service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False,
        sensors=[tensor], step_mapping=[{"sample_index": 0, "loadcase_id": 42}])
    assert result["curves"][-1]["data"][0] == ["0", 7.]
    assert len(comparison[1]) == 6


def test_invalid_step_is_not_silently_mapped_to_index(comparison):
    with pytest.raises(ValidationError):
        service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False,
            sensors=[binding()], step_mapping=[{"sample_index": 0, "loadcase_id": 0}])


def test_stale_import_mapping_is_rejected(comparison):
    with pytest.raises(AppError) as exc:
        service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False, import_sha256="old")
    assert exc.value.status_code == 409


def test_tensor_components_cannot_mix_frames(comparison, monkeypatch):
    def node_info(client, filename, nodes, variable, **options):
        return [{"nodeId": 10, "partId": 5, "stateId": 2 if variable.endswith("12") else 7, "values": [1.]}]
    monkeypatch.setattr(service.SimRightClient, "node_info", node_info)
    result = service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False,
        sensors=[dict(name="UP", node_id=10, part_id=5, quantity="stress", direction=[1, 0, 0])],
        step_mapping=[{"sample_index": 0, "loadcase_id": 42}])
    assert result["curves"][-1]["data"][0][1] is None
    assert "identities" in result["issues"][0]["message"]


def test_ambiguous_corner_values_return_null_and_issue(comparison, monkeypatch):
    monkeypatch.setattr(service.SimRightClient, "node_info", lambda *args, **kwargs:
        [{"nodeId": 10, "partId": 5, "stateId": 7, "values": [1, 2]}])
    result = service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False,
        sensors=[binding()], step_mapping=[{"sample_index": 0, "loadcase_id": 42}])
    assert result["curves"][-1]["data"][0] == ["0", None]
    assert result["issues"] and not result["resolved_states"]


def test_vendor_failure_produces_502(comparison, monkeypatch):
    def fail(*args):
        raise service.SimRightError("offline")
    monkeypatch.setattr(service.SimRightClient, "loadcases", fail)
    with pytest.raises(AppError) as exc:
        service.get_simright_test_load_level_comparison(7, "http://simright", "model", example_mode=False)
    assert exc.value.status_code == 502


def test_routes_and_validation(monkeypatch):
    from fastapi import FastAPI
    from webapi.routers import test_data

    async def no_log(*args):
        pass
    monkeypatch.setattr(test_data, "log_request", no_log)
    monkeypatch.setattr(test_data, "import_test_load_level", lambda **kwargs: kwargs)
    monkeypatch.setattr(test_data, "get_simright_test_load_level_comparison", lambda **kwargs: {"table": [], "curves": []})
    app = FastAPI()
    app.include_router(test_data.router)
    assert "/get/simright_test_load_level_comparison" in app.openapi()["paths"]
    body = ImportTestLoadLevelRequest(project_id=7, file_path="http://test/file.txt")
    assert asyncio.run(test_data.import_test_load_level_api(object(), body))["data"]["ignore_last_reading"] is True
    body = SimrightLoadLevelComparisonRequest(project_id=7, simright_base_url="http://simright", filename="model")
    assert asyncio.run(test_data.get_simright_test_load_level_comparison_api(object(), body))["data"]["curves"] == []
    with pytest.raises(ValueError):
        SimrightLoadLevelComparisonRequest(project_id=7, example_mode=False, simright_base_url="http://simright", filename="model",
            step_mapping=[{"sample_index": 1, "loadcase_id": 42}])


def test_example_mode_needs_no_database_or_simright(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Example mode must not query external data")
    monkeypatch.setattr(service, "load_test_data", unexpected)
    monkeypatch.setattr(service, "SimRightClient", unexpected)
    body = SimrightLoadLevelComparisonRequest(project_id=2026101601)
    result = service.get_simright_test_load_level_comparison(**body.model_dump())
    assert result["project_id"] == 2026101601 and result["is_example"] is True
    assert result["data_source"] == "example"
    count = result["summary"]["sample_count"]
    assert result["record_indices"] == list(range(count))
    assert all(len(column["value"]) == count for column in result["table"])
    assert len(result["curves"]) == result["summary"]["curve_count"]
    for curve in result["curves"]:
        assert set(curve) == {"label", "xaxis", "data"}
        assert curve["xaxis"] == [point[0] for point in curve["data"]]
        assert len(curve["data"]) == count
    result["curves"][0]["data"].clear()
    assert service.get_simright_test_load_level_comparison(123)["curves"][0]["data"]


def test_real_mode_still_requires_connection_details():
    with pytest.raises(ValueError):
        SimrightLoadLevelComparisonRequest(project_id=7, example_mode=False)
    with pytest.raises(ValidationError):
        service.get_simright_test_load_level_comparison(7, example_mode=False)


def test_example_route_returns_success_envelope_without_network(monkeypatch):
    from webapi.routers import test_data
    async def no_log(*args):
        pass
    monkeypatch.setattr(test_data, "log_request", no_log)
    response = asyncio.run(test_data.get_simright_test_load_level_comparison_api(
        object(), SimrightLoadLevelComparisonRequest(project_id=42)))
    assert response["ok"] and response["data"]["project_id"] == 42
    assert response["data"]["is_example"] and response["data"]["curves"]
