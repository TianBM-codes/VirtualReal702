import importlib
import os

import config as config_module


def test_db_config_reads_service_config(monkeypatch, tmp_path):
    config_path = tmp_path / "service_config.json"
    config_path.write_text(
        """
        {
          "DB_HOST": "192.168.0.10",
          "DB_PORT": 3307,
          "DB_USER": "tester",
          "DB_PASSWORD": "secret",
          "DB_DATABASE": "demo_db",
          "DB_CHARSET": "latin1"
        }
        """,
        encoding="utf-8",
    )

    original_cwd = os.getcwd()
    for key in ("CONFIG_FILE", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_DATABASE", "DB_CHARSET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.DB_CONFIG == {
            "host": "192.168.0.10",
            "port": 3307,
            "user": "tester",
            "password": "secret",
            "database": "demo_db",
            "charset": "latin1",
        }
    finally:
        monkeypatch.chdir(original_cwd)
        importlib.reload(config_module)
