import sqlite3
from pathlib import Path

from services.model_update.analysis import sensitivity_service


def test_ensure_workspace_result_group_meta_many_inserts_missing_groups(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "manifest.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE result_group_meta (
                result_group TEXT PRIMARY KEY,
                display_name TEXT,
                source_file TEXT,
                consistency_check TEXT,
                created_at TEXT
            )
            """
        )

    sensitivity_service._ensure_workspace_result_group_meta_many(
        str(workspace),
        ["sensitivity_5_U_10_U1", "sensitivity_5_U_10_U1", "sen_demo"],
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT result_group, display_name, consistency_check FROM result_group_meta ORDER BY result_group"
        ).fetchall()

    assert rows == [
        ("sen_demo", "sen_demo", "count-only"),
        ("sensitivity_5_U_10_U1", "sensitivity_5_U_10_U1", "count-only"),
    ]
