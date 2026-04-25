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


def _cfg_bool(cfg: dict, key: str, default: bool) -> bool:
    value = _cfg(cfg, key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
    "host": str(_cfg(_SERVICE_CONFIG, "APP_HOST", "0.0.0.0")),
    "port": int(_cfg(_SERVICE_CONFIG, "APP_PORT", 5000)),
    "debug": _cfg_bool(_SERVICE_CONFIG, "APP_DEBUG", False),
}


def _normalize_local_service_host(host: str) -> str:
    text = str(host or "").strip()
    if text in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return text


def get_local_service_base_url() -> str:
    explicit = str(_cfg(_SERVICE_CONFIG, "APP_BASE_URL", "")).strip().rstrip("/")
    if explicit:
        return explicit

    scheme = str(_cfg(_SERVICE_CONFIG, "APP_SCHEME", "http")).strip() or "http"
    host = _normalize_local_service_host(
        str(_cfg(_SERVICE_CONFIG, "APP_SELF_HOST", APP_CONFIG["host"]))
    )
    port = int(APP_CONFIG["port"])
    return f"{scheme}://{host}:{port}"
