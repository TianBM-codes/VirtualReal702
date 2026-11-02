import sys
import types

import pytest

from src.utils.external_runtime import BulkSymbol, load_bulk_module, load_result_constants


def test_optional_reader_does_not_load_until_used(monkeypatch):
    monkeypatch.delenv("FEM_BULK_PROVIDER_MODULE", raising=False)
    monkeypatch.delenv("FEM_RUNTIME_CONFIG", raising=False)
    reader = BulkSymbol("bdf.bdf", "BDF")
    with pytest.raises(ImportError, match="not configured"):
        reader()


def test_provider_identity_is_preserved(monkeypatch):
    module = types.ModuleType("sample_reader.bdf.bdf")

    class Reader:
        pass

    module.BDF = Reader
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("FEM_BULK_PROVIDER_MODULE", "sample_reader")
    monkeypatch.delenv("FEM_RUNTIME_CONFIG", raising=False)
    assert load_bulk_module("bdf.bdf") is module
    assert isinstance(BulkSymbol("bdf.bdf", "BDF")(), Reader)


def test_external_config_and_environment_override(monkeypatch, tmp_path):
    config = tmp_path / "runtime.json"
    config.write_text('{"FEM_RESULT_CONSTANTS_MODULE": "configured_constants"}')
    configured = types.ModuleType("configured_constants")
    override = types.ModuleType("override_constants")
    monkeypatch.setitem(sys.modules, configured.__name__, configured)
    monkeypatch.setitem(sys.modules, override.__name__, override)
    monkeypatch.setenv("FEM_RUNTIME_CONFIG", str(config))
    monkeypatch.delenv("FEM_RESULT_CONSTANTS_MODULE", raising=False)
    assert load_result_constants() is configured
    monkeypatch.setenv("FEM_RESULT_CONSTANTS_MODULE", override.__name__)
    assert load_result_constants() is override
