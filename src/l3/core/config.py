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

settings = Settings()
