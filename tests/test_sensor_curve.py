import asyncio
from decimal import Decimal

import pytest

from services.model_update.analysis import sensor_curve_service as service
from src.l3.core.errors import ValidationError
from webapi.models import SensorCurveRequest
from tools import import_test_load_levels as importer


class Cursor:
    def __init__(self, rows=(), fail_insert=False):
        self.rows = rows
        self.executed = []
        self.closed = False
        self.fail_insert = fail_insert

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.fail_insert and sql.lstrip().startswith("INSERT"):
            raise ValueError("duplicate key")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, cursor):
        self.q = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **kwargs):
        return self.q

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_load_level_curve_format_and_project_scope(monkeypatch):
    cursor = Cursor([
        {"load_level": Decimal("0.00000000"), "reading_value": 0, "unit": "mm"},
        {"load_level": Decimal("1.50000000"), "reading_value": None, "unit": "mm"},
        {"load_level": Decimal("2.00000000"), "reading_value": .2, "unit": "mm"}])
    connection = Connection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)
    result = service.get_sensor_curve(7, "SG'1")
    assert result["data"] == [{"label": "SG'1", "data": [["0", 0.], ["1.5", None], ["2", .2]],
                              "xaxis": ["0", "1.5", "2"]}]
    assert result["unit"] == "mm"
    assert result["summary"] == {"curve_count": 1, "point_count": 3}
    sql, params = cursor.executed[0]
    assert params == (7, "SG'1", "", "") and "ORDER BY CASE" in sql
    assert connection.closed and cursor.closed


def test_empty_curve(monkeypatch):
    monkeypatch.setattr(service, "get_connection", lambda: Connection(Cursor()))
    assert service.get_sensor_curve(7, "missing")["data"] == []


def test_mixed_units_rejected(monkeypatch):
    monkeypatch.setattr(service, "get_connection", lambda: Connection(Cursor([
        {"unit": "mm"}, {"unit": "m"}])))
    with pytest.raises(ValidationError):
        service.get_sensor_curve(7, "SG1")


@pytest.mark.parametrize("text", [
    "sensor,load_level,value\nSG1,1,nan\n",
    "sensor,load_level,value\nSG1,-1,2\n",
    "sensor,load_level,value\nSG1,0.000000001,2\n",
    "sensor,load_level,value\nSG1,1,2\nSG1,1.0,3\n",
    "sensor,load_level,value\n,1,2\n",
    "sensor,load_level,value\n",
    "sensor,load_level,value,unit\nSG1,1,1,mm\nSG1,2,2,m\n",
    "sensor,value\nSG1,1\n",
])
def test_import_validation(tmp_path, text):
    path = tmp_path / "readings.csv"
    path.write_text(text, encoding="utf-8-sig")
    with pytest.raises(ValueError):
        importer.read_rows(path)


def test_csv_and_atomic_import(monkeypatch, tmp_path):
    monkeypatch.setattr(importer, "ensure_test_load_level_schema", lambda cursor: None)
    path = tmp_path / "readings.csv"
    path.write_text("sensor,load_level,value,unit,remark\nSG1,0,0,mm,start\nSG1,1.5,,mm,missing\n", encoding="utf-8-sig")
    rows = importer.read_rows(path)
    assert rows[1][1:3] == (Decimal("1.5"), None)
    cursor = Cursor()
    connection = Connection(cursor)
    monkeypatch.setattr(importer, "get_connection", lambda: connection)
    result = importer.store_rows(7, rows)
    assert result["row_count"] == 2
    assert connection.commits == 2 and connection.rollbacks == 0
    assert cursor.executed[-1][1] == (7, *rows[1], "", "")
    assert "DUPLICATE KEY" not in cursor.executed[-1][0]
    cursor.fail_insert = True
    with pytest.raises(ValueError):
        importer.store_rows(7, rows)
    assert connection.rollbacks == 1


def test_update_existing_is_explicit(monkeypatch):
    monkeypatch.setattr(importer, "ensure_test_load_level_schema", lambda cursor: None)
    cursor = Cursor()
    monkeypatch.setattr(importer, "get_connection", lambda: Connection(cursor))
    importer.store_rows(7, [("SG1", Decimal("1"), .2, None, None)], update_existing=True)
    assert "ON DUPLICATE KEY UPDATE" in cursor.executed[-1][0]


def test_route_and_request(monkeypatch):
    from fastapi import FastAPI
    from webapi.routers import test_data

    async def noop(*args):
        pass

    monkeypatch.setattr(test_data, "log_request", noop)
    monkeypatch.setattr(test_data, "get_sensor_curve", lambda **kwargs: dict(kwargs, data=[]))
    body = SensorCurveRequest(project_id=7, sensor=" SG1 ", work_condition_id="001", measurement_id="m-1")
    result = asyncio.run(test_data.get_sensor_load_level_curve_api(object(), body))
    assert result["data"] == {"project_id": 7, "sensor": "SG1", "data": [],
                              "work_condition_id": "001", "measurement_id": "m-1"}
    app = FastAPI()
    app.include_router(test_data.router)
    assert "/get/sensor_load_level_curve" in app.openapi()["paths"]
    assert "/get/sensor_curve" not in app.openapi()["paths"]
    with pytest.raises(ValueError):
        SensorCurveRequest(project_id=7, sensor=" ")
