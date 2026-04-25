import json
import os


def _load_service_config() -> dict:
    candidates = [
        os.getenv("CONFIG_FILE", ""),
        os.path.join(os.getcwd(), "service_config.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "service_config.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass
    return {}


def _cfg(cfg: dict, key: str, default):
    if key in os.environ:
        return os.environ[key]
    return cfg.get(key, default)


_SERVICE_CONFIG = _load_service_config()

DB_CONFIG = {
    "host": str(_cfg(_SERVICE_CONFIG, "DB_HOST", "127.0.0.1")),
    "port": int(_cfg(_SERVICE_CONFIG, "DB_PORT", 3306)),
    "user": str(_cfg(_SERVICE_CONFIG, "DB_USER", "root")),
    "password": str(_cfg(_SERVICE_CONFIG, "DB_PASSWORD", "sipesc")),
    "database": str(_cfg(_SERVICE_CONFIG, "DB_DATABASE", "db_simu_real_test")),
    "charset": str(_cfg(_SERVICE_CONFIG, "DB_CHARSET", "utf8mb4")),
}

APP_CONFIG = {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": False
}
