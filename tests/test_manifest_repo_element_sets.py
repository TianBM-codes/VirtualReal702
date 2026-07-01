import sqlite3
from pathlib import Path

import h5py
import numpy as np

from src.l1.manifest_schema import MANIFEST_SCHEMA
from src.l3.infra.manifest_repo import ManifestRepo


def test_get_element_set_labels_accepts_case_mismatched_set_name(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "l1" / "sets").mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.executescript(MANIFEST_SCHEMA)
        conn.execute(
            "INSERT INTO element_sets(set_name, set_scope, instance_name, h5_path, elem_count, is_internal) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("SET-149", "PART", "PART-1-1", "l1/sets/sets.h5", 2, 0),
        )

    with h5py.File(workspace / "l1" / "sets" / "sets.h5", "w") as handle:
        handle.create_dataset(
            "element_sets/PART-1-1/SET-149",
            data=np.asarray([101, 102], dtype=np.int32),
        )

    repo = ManifestRepo(str(workspace))
    labels = repo.get_element_set_labels("Set-149", "PART-1-1")

    assert labels is not None
    assert labels.tolist() == [101, 102]
