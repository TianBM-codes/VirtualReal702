import os

# Note: In a real project you'd likely use pydantic-settings. 
# We use standard dict/env fallback here for simplicity in the skeleton.

class Settings:
    def __init__(self):
        self.app_name = os.getenv("APP_NAME", "odb-l3-service")
        self.app_env = os.getenv("APP_ENV", "prod")
        self.log_level = os.getenv("APP_LOG_LEVEL", "INFO")
        self.data_root = os.getenv("APP_DATA_ROOT", "/data")
        self.registry_db_path = os.getenv("APP_REGISTRY_DB_PATH", "/data/registry.db")
        self.default_chunk_size = int(os.getenv("APP_DEFAULT_CHUNK_SIZE", "50000"))
        self.gunicorn_workers = int(os.getenv("APP_GUNICORN_WORKERS", "4"))

        # Dev mode: directly specify a single workspace without registry.db
        # e.g. APP_ODB_WORKSPACE=E:\code\...\tmp
        self.odb_workspace = os.getenv("APP_ODB_WORKSPACE", "")
        # odb_id used in URLs when dev mode is active (defaults to workspace dir name)
        self.odb_id = os.getenv("APP_ODB_ID", "")

        # Security: restrict odb_path submissions to this root directory.
        # Empty string = no restriction (suitable for internal deployments).
        self.raw_odb_root = os.getenv("APP_RAW_ODB_ROOT", "")

        # Compression: APP_ENABLE_GZIP=1 — wrap all responses with HTTP gzip
        self.enable_gzip = os.getenv("APP_ENABLE_GZIP", "0") == "1"

settings = Settings()
