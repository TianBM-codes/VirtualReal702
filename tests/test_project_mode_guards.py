import pytest

from services.model_update.analysis import inp_service
from src.l3.core.errors import ValidationError


class _FakeCursor:
    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        return None


class _FakeConnection:
    def cursor(self, dictionary=False):
        return _FakeCursor()

    def close(self):
        return None


def test_compute_static_correlation_rejects_modal_project(monkeypatch):
    monkeypatch.setattr(inp_service, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(inp_service, "get_test_data_mode", lambda project_id, cursor=None: "modal_unv")

    with pytest.raises(ValidationError) as exc_info:
        inp_service.compute_static_correlation(project_id=101)

    assert "static correlation is not available for modal projects" in str(exc_info.value)


def test_compute_modal_correlation_requires_modal_project(monkeypatch):
    monkeypatch.setattr(inp_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(inp_service, "get_connection", lambda: _FakeConnection())
    monkeypatch.setattr(inp_service, "get_test_data_mode", lambda project_id, cursor=None: "static_unv")

    with pytest.raises(ValidationError) as exc_info:
        inp_service.compute_modal_correlation(project_id=202)

    assert "modal correlation requires a modal project" in str(exc_info.value)
