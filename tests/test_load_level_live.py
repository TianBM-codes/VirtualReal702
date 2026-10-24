"""Opt-in MySQL + local HTTP integration; test readings are rolled back."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from types import SimpleNamespace

import pytest
from mysql.connector import IntegrityError


@pytest.mark.skipif(os.environ.get("RUN_LOAD_LEVEL_MYSQL_TEST") != "1", reason="requires configured MySQL")
def test_http_download_mysql_and_simright_protocol(monkeypatch, tmp_path):
    from db import ensure_test_load_level_schema, get_connection
    from services.model_update.analysis import test_load_level_service as imports
    from services.model_update.analysis import simright_load_level_service as comparison
    from services.model_update.analysis import sensor_curve_service as curves
    from test_simright_load_level_comparison import make_txt

    payload = make_txt(tmp_path / "readings.txt").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if request["name"] == "loadcases":
                data = {"value": [{"id": 42, "name": "Step-1"}]}
            else:
                assert request["name"] == "nodeInfo"
                assert request["args"]["loadcaseIndex"] == 0
                data = [{"nodeId": 10, "partId": 5, "stateId": 2, "values": [12.]}]
            response = json.dumps({"code": 0, "data": data, "message": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)

    connection = get_connection()
    q = connection.cursor(dictionary=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project_id = 2026102199
    try:
        ensure_test_load_level_schema(q)
        connection.commit()
        q.execute("SELECT pid FROM t_mt_py_project_config WHERE pid=%s", (project_id,))
        assert q.fetchone() is None, "Choose an unused integration-test project ID"
        q.execute("SELECT id FROM t_mt_py_test_load_level_data WHERE project_id=%s", (project_id,))
        assert q.fetchone() is None
        connection.rollback()
        # Suppress commits ONLY within this test. Read/write through the same
        # real transaction, then roll it back; schema migration remains applied.
        proxy = SimpleNamespace(cursor=connection.cursor, commit=lambda: None,
                                rollback=connection.rollback, close=lambda: None)
        monkeypatch.setattr(imports, "get_connection", lambda: proxy)
        monkeypatch.setattr(curves, "get_connection", lambda: proxy)
        monkeypatch.setattr(imports, "resolve_project_workspace", lambda pid: str(tmp_path / "project"))
        base = "http://127.0.0.1:%s" % server.server_port
        result = imports.import_test_load_level(project_id, base + "/readings.txt", unit="mm")
        assert result["row_count"] == 6
        assert curves.get_sensor_curve(project_id, "UP")["data"][0]["data"] == [["0", 0.], ["1", 10.], ["0", 2.]]
        q.execute("SELECT extra_json FROM t_mt_py_project_config WHERE pid=%s", (project_id,))
        meta = json.loads(q.fetchone()["extra_json"])["test_load_level_import"]
        assert len(meta["axis_rows"]) == 3 and meta["ignored_trailing_values"]["UP"] == 999
        from services.model_update.analysis import project_config_service as configs
        monkeypatch.setattr(configs, "get_connection", lambda: proxy)
        monkeypatch.setattr(comparison, "get_connection", lambda: proxy)
        configs.upsert_project_config(project_id, extra_json={"simright": {
            "filename": "model", "base_url": base, "sensor_bindings": {
                "UP": {"node_id": 10, "part_id": 5, "quantity": "scalar", "var_name": "D.N.X"}}}}, cursor=q)
        result = comparison.get_simright_test_load_level_comparison(project_id, example_mode=False,
            sensors=[{"name": "UP"}], steps=[None, 42, None])
        assert result["curves"][-1]["data"] == [["0", None], ["1", 12.], ["0", None]]
        assert not result["issues"]
        # Same project and sensor, independent scopes and different TXT axes.
        scoped_path = make_txt(tmp_path / "scoped.txt", axes={"Level": [2, 3]},
                               gauges={"UP": [20, 30, 999]})
        imports.import_test_load_level(project_id, str(scoped_path), work_condition_id="001", measurement_id="m-1")
        imports.import_test_load_level(project_id, str(scoped_path), work_condition_id="002", measurement_id="m-1")
        scoped_path = make_txt(tmp_path / "updated.txt", axes={"Level": [4, 5]},
                               gauges={"UP": [40, 50, 999]})
        imports.import_test_load_level(project_id, str(scoped_path), work_condition_id="001", measurement_id="m-1")
        assert curves.get_sensor_curve(project_id, "UP", "001", "m-1")["data"][0]["data"] == [["4", 40.], ["5", 50.]]
        assert curves.get_sensor_curve(project_id, "UP", "002", "m-1")["data"][0]["data"] == [["2", 20.], ["3", 30.]]
        assert curves.get_sensor_curve(project_id, "UP")["summary"]["point_count"] == 3
        assert curves.get_sensor_curve(project_id, "UP", "001", "missing")["data"] == []
        scoped_meta, scoped_readings = imports.load_test_data(project_id, "001", "m-1")
        assert scoped_meta["sample_count"] == 2 and scoped_readings["UP"] == [40., 50.]
        selected = comparison.get_simright_test_load_level_comparison(project_id, example_mode=False,
            work_condition_id="001", measurement_id="m-1", sensors=[{"name": "UP"}], steps=[42, None])
        assert selected["curves"][0]["data"] == [["4", 40.], ["5", 50.]]
        q.execute("""INSERT INTO t_mt_py_test_load_level_data
            (project_id,sensor,load_level,reading_value) VALUES (%s,'LEGACY',0,1)""", (project_id,))
        with pytest.raises(IntegrityError):
            q.execute("""INSERT INTO t_mt_py_test_load_level_data
                (project_id,sensor,load_level,reading_value) VALUES (%s,'LEGACY',0,2)""", (project_id,))
    finally:
        connection.rollback()
        q.close()
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
