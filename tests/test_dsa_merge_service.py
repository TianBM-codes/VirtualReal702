import sqlite3
from pathlib import Path

import h5py
import numpy as np

from src.l1.manifest_schema import MANIFEST_SCHEMA
from src.l3.services.dsa_merge_service import merge_dsa_fields


def _build_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "l1" / "geometry").mkdir(parents=True, exist_ok=True)
    (workspace / "l1" / "results" / "source_rg").mkdir(parents=True, exist_ok=True)
    (workspace / "l1" / "sets").mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.executescript(MANIFEST_SCHEMA)
        conn.execute(
            "INSERT INTO instances(instance_name, part_name, geom_path) VALUES (?, ?, ?)",
            ("PART-1-1", "PART-1", "l1/geometry/PART-1-1.h5"),
        )
        conn.execute(
            "INSERT INTO element_sets(set_name, set_scope, instance_name, h5_path, elem_count, is_internal) VALUES (?, ?, ?, ?, ?, ?)",
            ("SET_E", "PART", "PART-1-1", "l1/sets/sets.h5", 2, 0),
        )
        conn.execute(
            "INSERT INTO element_sets(set_name, set_scope, instance_name, h5_path, elem_count, is_internal) VALUES (?, ?, ?, ?, ?, ?)",
            ("SET_RHO", "PART", "PART-1-1", "l1/sets/sets.h5", 1, 0),
        )
        conn.execute(
            "INSERT INTO steps(result_group, step_name, step_number, procedure, num_frames) VALUES (?, ?, ?, ?, ?)",
            ("source_rg", "Sensitivity", 0, "EXTERNAL", 1),
        )
        conn.executemany(
            "INSERT INTO frames(result_group, step_name, frame_idx, frame_value, description) VALUES (?, ?, ?, ?, ?)",
            [("source_rg", "Sensitivity", 0, 0.0, "Frame 0")],
        )
        conn.executemany(
            """
            INSERT INTO result_files(result_group, step_name, field_name, file_path, components, invariants, positions, has_section, val_min, val_max, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("source_rg", "Sensitivity", "d_U_T1", "l1/results/source_rg/d_U_T1.h5", '["U1"]', "[]", '["NODAL"]', 0, None, None, "external"),
                ("source_rg", "Sensitivity", "d_U_T2", "l1/results/source_rg/d_U_T2.h5", '["U1"]', "[]", '["NODAL"]', 0, None, None, "external"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO result_blocks(result_group, step_name, field_name, instance_name, position, elem_type, h5_path, label_path, n_entities, n_ip, n_sp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("source_rg", "Sensitivity", "d_U_T1", "PART-1-1", "NODAL", None, "/NODAL/PART-1-1/data", None, 0, 1, 1),
                ("source_rg", "Sensitivity", "d_U_T2", "PART-1-1", "NODAL", None, "/NODAL/PART-1-1/data", None, 0, 1, 1),
            ],
        )

    with h5py.File(workspace / "l1" / "geometry" / "PART-1-1.h5", "w") as handle:
        handle.create_dataset("elements/C3D4/labels", data=np.asarray([101, 102, 103], dtype=np.int32))
        handle.create_dataset("nodes/labels", data=np.asarray([10], dtype=np.int32))

    with h5py.File(workspace / "l1" / "sets" / "sets.h5", "w") as handle:
        handle.create_dataset("element_sets/PART-1-1/SET_E", data=np.asarray([101, 102], dtype=np.int32))
        handle.create_dataset("element_sets/PART-1-1/SET_RHO", data=np.asarray([103], dtype=np.int32))

    for file_name, values in (("d_U_T1.h5", [[1.5]]), ("d_U_T2.h5", [[2.5]])):
        with h5py.File(workspace / "l1" / "results" / "source_rg" / file_name, "w") as handle:
            handle.create_dataset("/NODAL/PART-1-1/data/labels", data=np.asarray([10], dtype=np.int32))
            handle.create_dataset("/NODAL/PART-1-1/data/data", data=np.asarray(values, dtype=np.float32))

    return workspace


def test_merge_dsa_fields_publishes_per_response_groups_and_parameter_type_fields(tmp_path: Path):
    workspace = _build_workspace(tmp_path)

    written = merge_dsa_fields(
        workspace=str(workspace),
        step="Sensitivity",
        frame=0,
        field_prefix="d_U_T",
        instances=["PART-1-1"],
        parameter_rows=[
            {"parameter_name": "E1", "quantity_code": "E", "set_name": "SET_E", "set_scope": "PART", "part_name": "PART-1"},
            {"parameter_name": "RHO1", "quantity_code": "RHO", "set_name": "SET_RHO", "set_scope": "PART", "part_name": "PART-1"},
        ],
        result_group="sensitivity_5_U",
        source_result_group="source_rg",
    )

    assert written == [
        {
            "result_group": "sensitivity_5_U_10_U1",
            "field_name": "E",
            "response_node_label": 10,
            "component": "U1",
            "component_idx": 0,
            "instance_count": 1,
            "element_count": 2,
        },
        {
            "result_group": "sensitivity_5_U_10_U1",
            "field_name": "RHO",
            "response_node_label": 10,
            "component": "U1",
            "component_idx": 0,
            "instance_count": 1,
            "element_count": 1,
        },
    ]

    with sqlite3.connect(workspace / "manifest.db") as conn:
        rows = conn.execute(
            "SELECT result_group, step_name, field_name, source FROM result_files WHERE result_group=? ORDER BY field_name",
            ("sensitivity_5_U_10_U1",),
        ).fetchall()
    assert rows == [
        ("sensitivity_5_U_10_U1", "Sensitivity", "E", "external"),
        ("sensitivity_5_U_10_U1", "Sensitivity", "RHO", "external"),
    ]
