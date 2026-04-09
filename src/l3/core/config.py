import json
import os

# Priority: environment variable > config file > built-in default
#
# Config file location (checked in order):
#   1. Path in CONFIG_FILE env var
#   2. service_config.json in current working directory
#   3. service_config.json two levels up from this file (repo root)
#
# Example service_config.json:
# {
#   "APP_DATA_ROOT": "model/",
#   "APP_REGISTRY_DB_PATH": "model/registry.db"
# }


def _load_config_file() -> dict:
    candidates = [
        os.getenv("CONFIG_FILE", ""),
        os.path.join(os.getcwd(), "service_config.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "service_config.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


# Repo root = three levels up from this file: core/ → l3/ → src/ → repo_root
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DEFAULT_DATA_ROOT    = os.path.join(_REPO_ROOT, "model")
_DEFAULT_REGISTRY_DB  = os.path.join(_REPO_ROOT, "model", "registry.db")


def _get(cfg: dict, key: str, default: str) -> str:
    """env var wins; then config file; then default."""
    if key in os.environ:
        return os.environ[key]
    return str(cfg.get(key, default))


class Settings:
    def __init__(self):
        cfg = _load_config_file()

        self.app_name   = _get(cfg, "APP_NAME",    "odb-l3-service")
        self.app_env    = _get(cfg, "APP_ENV",      "prod")
        self.log_level  = _get(cfg, "APP_LOG_LEVEL","INFO")
        self.data_root  = _get(cfg, "APP_DATA_ROOT", _DEFAULT_DATA_ROOT)
        self.registry_db_path = _get(cfg, "APP_REGISTRY_DB_PATH", _DEFAULT_REGISTRY_DB)
        self.default_chunk_size = int(_get(cfg, "APP_DEFAULT_CHUNK_SIZE", "50000"))
        self.gunicorn_workers   = int(_get(cfg, "APP_GUNICORN_WORKERS",   "4"))

        # Dev mode: directly specify a single workspace without registry.db
        self.odb_workspace = _get(cfg, "APP_ODB_WORKSPACE", "")
        self.odb_id        = _get(cfg, "APP_ODB_ID",        "")

        # Security: restrict odb_path submissions to this root directory.
        self.raw_odb_root = _get(cfg, "APP_RAW_ODB_ROOT", "")

        # Compression: APP_ENABLE_GZIP=1 — wrap all responses with HTTP gzip
        self.enable_gzip = _get(cfg, "APP_ENABLE_GZIP", "0") == "1"

        # Embedded runner: run L1+L2 job pipeline as a daemon thread inside
        # the web process (default on).  Set APP_EMBEDDED_RUNNER=0 if you
        # want to run job_runner.py as a separate process instead.
        self.embedded_runner = _get(cfg, "APP_EMBEDDED_RUNNER", "1") == "1"

        # Abaqus executable — override if `abaqus` is not on PATH
        self.abaqus_cmd = _get(cfg, "APP_ABAQUS_CMD", "abaqus")

        # How often the embedded runner polls for new jobs (seconds)
        self.runner_poll_interval = int(_get(cfg, "JOB_RUNNER_POLL_INTERVAL", "10"))


settings = Settings()
