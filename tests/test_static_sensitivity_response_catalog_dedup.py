import pytest

from services.model_update.analysis import fem_catalog_service
from src.l3.core.errors import ValidationError


class _FakeCursor:
    def __init__(self, existing_rows):
        self.existing_rows = existing_rows
        self.executed = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = " ".join(str(sql).split())
        self.executed.append((self._last_sql, params))

    def fetchall(self):
        if "SELECT response_no, request_no, step_name, frequency, region_type, set_name," in self._last_sql:
            return list(self.existing_rows)
        return []

    def fetchone(self):
        if "SELECT COALESCE(MAX(response_no), 0) AS max_no" in self._last_sql:
            return {"max_no": 0}
        return {}

    def close(self):
        return None


class _FakeConnection:
    def __init__(self, existing_rows):
        self.cursor_obj = _FakeCursor(existing_rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary=True):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        return None


def test_create_design_response_catalog_entry_rejects_duplicate_set_response(monkeypatch):
    fake_conn = _FakeConnection(
        [
            {
                "response_no": 1,
                "request_no": 1,
                "step_name": "Step-1",
                "frequency": 1,
                "region_type": "NODE",
                "set_name": "RESP_NODES",
                "set_scope": "ASSEMBLY",
                "instance_name": "PART-1-1",
                "part_name": "PART-1",
                "variables_json": '["U2"]',
                "extra_json": '{"response_name": "DISP_U2"}',
            }
        ]
    )
    monkeypatch.setattr(fem_catalog_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(fem_catalog_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(fem_catalog_service, "safe_write_console_event", lambda *args, **kwargs: None)

    with pytest.raises(ValidationError, match="duplicate static sensitivity response already exists"):
        fem_catalog_service.create_design_response_catalog_entry(
            project_id=5,
            region_type="NODE",
            variables=["U2"],
            set_name="RESP_NODES",
            set_scope="ASSEMBLY",
            instance_name="PART-1-1",
            part_name="PART-1",
            step_name="Step-1",
            frequency=1,
            response_name="DISP_U2_AGAIN",
        )

    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True
    assert all("INSERT INTO t_mt_py_fem_static_sensitivity_response_catalog" not in sql for sql, _ in fake_conn.cursor_obj.executed)


def test_create_design_response_catalog_entry_rejects_duplicate_manual_labels_even_with_new_name(monkeypatch):
    fake_conn = _FakeConnection(
        [
            {
                "response_no": 2,
                "request_no": 1,
                "step_name": "Step-1",
                "frequency": 1,
                "region_type": "NODE",
                "set_name": "MANUAL_RESP_NODE_DISP_U2_NODE4_2",
                "set_scope": "ASSEMBLY",
                "instance_name": "PART-1-1",
                "part_name": "PART-1",
                "variables_json": '["U2"]',
                "extra_json": '{"response_name": "DISP_U2_NODE4", "set_source": "manual", "node_labels": [4]}',
            }
        ]
    )
    monkeypatch.setattr(fem_catalog_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(fem_catalog_service, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(fem_catalog_service, "safe_write_console_event", lambda *args, **kwargs: None)

    with pytest.raises(ValidationError, match="duplicate static sensitivity response already exists"):
        fem_catalog_service.create_design_response_catalog_entry(
            project_id=5,
            region_type="NODE",
            variables=["U2"],
            instance_name="PART-1-1",
            part_name="PART-1",
            set_scope="ASSEMBLY",
            node_labels=[4],
            step_name="Step-1",
            frequency=1,
            response_name="ANOTHER_NAME",
        )

    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True
    assert all("INSERT INTO t_mt_py_fem_static_sensitivity_response_catalog" not in sql for sql, _ in fake_conn.cursor_obj.executed)
