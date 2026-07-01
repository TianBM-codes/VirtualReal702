import sqlite3
from pathlib import Path
from types import SimpleNamespace


def test_resolve_display_frame_maps_raw_sensitivity_alias_to_last_frame(monkeypatch, tmp_path: Path):
    from src.l3.api.routes import results

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "manifest.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE frames (
                result_group TEXT,
                step_name TEXT,
                frame_idx INTEGER,
                frame_value REAL,
                description TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO frames(result_group, step_name, frame_idx, frame_value, description) VALUES (?, ?, ?, ?, ?)",
            [
                ("sensitivity_batch_demo", "Step-1", 0, 0.0, "Increment 0"),
                ("sensitivity_batch_demo", "Step-1", 1, 1.0, "Increment 1"),
            ],
        )

    monkeypatch.setattr(
        results.registry,
        "get",
        lambda odb_id: SimpleNamespace(workspace=str(workspace)),
    )

    resolved = results._resolve_display_frame(
        odb_id="22",
        requested_step="Sensitivity",
        resolved_step="Step-1",
        requested_frame=0,
        result_group="sensitivity_batch_demo",
    )

    assert resolved == 1


def test_resolve_display_frame_keeps_explicit_nonzero_frame(monkeypatch, tmp_path: Path):
    from src.l3.api.routes import results

    monkeypatch.setattr(
        results.registry,
        "get",
        lambda odb_id: SimpleNamespace(workspace=str(tmp_path)),
    )

    resolved = results._resolve_display_frame(
        odb_id="22",
        requested_step="Sensitivity",
        resolved_step="Step-1",
        requested_frame=1,
        result_group="sensitivity_batch_demo",
    )

    assert resolved == 1
