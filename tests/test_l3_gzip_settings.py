import pytest
from fastapi import FastAPI, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient

from src.l3.core import config


def test_gzip_level_default_file_and_environment(monkeypatch):
    monkeypatch.delenv("APP_GZIP_COMPRESSLEVEL", raising=False)
    monkeypatch.setattr(config, "_load_config_file", lambda: {})
    assert config.Settings().gzip_compresslevel == 1
    monkeypatch.setattr(config, "_load_config_file", lambda: {"APP_GZIP_COMPRESSLEVEL": 6})
    assert config.Settings().gzip_compresslevel == 6
    monkeypatch.setenv("APP_GZIP_COMPRESSLEVEL", "3")
    assert config.Settings().gzip_compresslevel == 3


@pytest.mark.parametrize("value", ["0", "10", "fast"])
def test_gzip_level_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("APP_GZIP_COMPRESSLEVEL", value)
    with pytest.raises(ValueError):
        config.Settings()


def test_actual_app_uses_configured_gzip_level():
    from src.l3.main import app, settings

    middleware = [m for m in app.user_middleware if m.cls is GZipMiddleware]
    assert len(middleware) == int(settings.enable_gzip)
    if middleware:
        assert middleware[0].kwargs["compresslevel"] == settings.gzip_compresslevel


@pytest.mark.parametrize("level", [1, 9])
def test_gzip_keeps_binary_payload_and_identity_clients(level):
    payload = bytes(range(256)) * 4096
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=level)

    @app.get("/buffer")
    def buffer():
        return Response(payload, media_type="application/octet-stream")

    with TestClient(app) as client:
        compressed = client.get("/buffer", headers={"Accept-Encoding": "gzip"})
        assert compressed.headers["content-encoding"] == "gzip"
        assert compressed.content == payload  # HTTP client decodes the body
        identity = client.get("/buffer", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in identity.headers
        assert identity.content == payload
