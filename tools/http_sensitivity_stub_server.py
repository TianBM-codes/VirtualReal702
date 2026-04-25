import os
import sqlite3
import sys
from pathlib import Path

import h5py
import numpy as np
import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.l1.manifest_schema import MANIFEST_SCHEMA

WORKSPACE = REPO_ROOT / "tmp_http_sensitivity_workspace"
GEOM_DIR = WORKSPACE / "l1" / "geometry"
GEOM_PATH = GEOM_DIR / "PART-1-1.h5"
MANIFEST_PATH = WORKSPACE / "manifest.db"
INPUT_INP = REPO_ROOT / "tmp_http_sensitivity_input.inp"
OUTPUT_DIR = REPO_ROOT / "tmp_http_sensitivity_output"
ODB_PATH = OUTPUT_DIR / "demo_job.odb"
REGISTRY_DB = REPO_ROOT / "tmp_http_registry.db"
PROJECT_ID = "101"
PORT = 5011


def _prepare_workspace() -> None:
    GEOM_DIR.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "l1" / "results").mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_INP.write_text("*Heading\n", encoding="utf-8")
    ODB_PATH.write_text("odb", encoding="utf-8")

    if GEOM_PATH.exists():
        GEOM_PATH.unlink()
    with h5py.File(GEOM_PATH, "w") as geom_h5:
        geom_h5.create_dataset("elements/C3D8/labels", data=np.asarray([101, 102, 103, 104], dtype=np.int32))

    conn = sqlite3.connect(MANIFEST_PATH)
    try:
        conn.executescript(MANIFEST_SCHEMA)
        conn.execute("DELETE FROM instances")
        conn.execute(
            """
            INSERT INTO instances (
                instance_name, part_name, geom_path, highorder_path,
                node_count, elem_count, bbox_min, bbox_max
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PART-1-1",
                "PART-1",
                str(Path("l1") / "geometry" / "PART-1-1.h5"),
                None,
                0,
                4,
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _install_stubs() -> None:
    from services.model_update.analysis import sensitivity_service

    sensitivity_service.ensure_tables_exist = lambda: None
    sensitivity_service._update_project_sensitivity_status = lambda *args, **kwargs: None
    sensitivity_service._persist_sensitivity_matrix = (
        lambda **kwargs: {
            "analysis_run_id": 9001,
            "project_id": int(kwargs["project_id"]),
            "batch_no": str(kwargs["batch_no"]),
            "case_name": "tmp_http_sensitivity_input",
            "response_names": ["R1", "R2"],
            "parameter_names": ["T1", "T2"],
            "response_count": 2,
            "parameter_count": 2,
            "point_count": 4,
        }
    )
    sensitivity_service._rebuild_selected_parameters_from_inp = (
        lambda **kwargs: {"selected_parameter_count": 2, "cleared_count": 0}
    )
    sensitivity_service.delete_abaqus_process_files = lambda workdir, job_name: []
    sensitivity_service.build_workspace_from_odb = lambda **kwargs: {"workspace": kwargs["workspace"]}
    sensitivity_service.run_abaqus_job = (
        lambda **kwargs: {
            "job_name": "demo_job",
            "generated_files": {"analysis_inp": str(INPUT_INP)},
            "solver": {"ok": True, "artifacts": {"odb": str(ODB_PATH)}},
        }
    )
    sensitivity_service._load_dsa_normalized_sensitivity_matrix = (
        lambda **kwargs: {
            "workspace": str(WORKSPACE),
            "base_url": None,
            "step": "Step-1",
            "instances": ["PART-1-1"],
            "aggregation": "max_abs",
            "frame": 0,
            "field_prefix": "d_U_",
            "matrix": [[0.1, 0.2], [0.3, 0.4]],
            "response_rows": [
                {
                    "row_key": "R1",
                    "instance": "PART-1-1",
                    "response_field": "U",
                    "response_component": "U1",
                    "response_position": "NODAL",
                    "response_label": "PART-1-1::10",
                },
                {
                    "row_key": "R2",
                    "instance": "PART-1-1",
                    "response_field": "U",
                    "response_component": "U1",
                    "response_position": "NODAL",
                    "response_label": "PART-1-1::20",
                },
            ],
            "parameter_columns": [
                {
                    "field": "d_U_T1",
                    "parameter_name": "T1",
                    "element_mapping": {
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [101, 102]},
                    },
                },
                {
                    "field": "d_U_T2",
                    "parameter_name": "T2",
                    "element_mapping": {
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [103, 104]},
                    },
                },
            ],
        }
    )


def main() -> None:
    _prepare_workspace()
    os.environ.setdefault("APP_ODB_WORKSPACE", str(WORKSPACE))
    os.environ.setdefault("APP_ODB_ID", PROJECT_ID)
    os.environ.setdefault("APP_EMBEDDED_RUNNER", "0")
    os.environ.setdefault("APP_DATA_ROOT", str(REPO_ROOT / "tmp_http_data_root"))
    os.environ.setdefault("APP_REGISTRY_DB_PATH", str(REGISTRY_DB))
    os.environ.setdefault("APP_PORT", str(PORT))
    _install_stubs()

    import app as root_app

    uvicorn.run(root_app.app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
