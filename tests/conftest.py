import sqlite3
import sys
from pathlib import Path

import h5py
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.l3.core.state import ModelIndex, OdbRegistry  # noqa: E402


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for rel in ("l1/geometry", "l1/results"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(tmp_path / "manifest.db")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS result_blocks (
            step_name TEXT,
            field_name TEXT,
            instance_name TEXT,
            position TEXT,
            elem_type TEXT,
            file_path TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return tmp_path


def _make_registry(workspace: Path, *, odb_id: str = "odb", instance: str = "PART-1-1") -> OdbRegistry:
    idx = ModelIndex(odb_id=odb_id, workspace=str(workspace))
    idx.is_render_ready = True
    registry = OdbRegistry()
    registry.loaded[odb_id] = idx
    return registry


def _write_geometry_file(
    workspace: Path,
    instance: str,
    *,
    node_labels: np.ndarray,
    element_defs: dict,
) -> Path:
    path = workspace / "l1" / "geometry" / f"{instance}.h5"
    with h5py.File(path, "w") as f:
        nodes = f.create_group("nodes")
        nodes.create_dataset("labels", data=node_labels.astype(np.int32))

        elems = f.create_group("elements")
        for etype, spec in element_defs.items():
            grp = elems.create_group(etype)
            grp.create_dataset("labels", data=np.asarray(spec["labels"], dtype=np.int32))
            grp.create_dataset("conn", data=np.asarray(spec["conn"], dtype=np.int32))
    return path


def _write_nodal_result_file(
    workspace: Path,
    instance: str,
    *,
    step: str,
    field: str,
    data: np.ndarray,
) -> Path:
    fname = f"{step.replace('/', '__').replace(' ', '_')}__{field.replace('/', '__').replace(' ', '_')}.h5"
    path = workspace / "l1" / "results" / fname
    with h5py.File(path, "w") as f:
        grp = f.create_group("NODAL").create_group(instance)
        grp.create_dataset("data", data=np.asarray(data, dtype=np.float32))
    return path


def _register_result_block(
    workspace: Path,
    *,
    step: str,
    field: str,
    instance: str,
    position: str,
    file_path: Path,
    elem_type=None,
) -> None:
    conn = sqlite3.connect(workspace / "manifest.db")
    conn.execute(
        """
        INSERT INTO result_blocks
        (step_name, field_name, instance_name, position, elem_type, file_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (step, field, instance, position, elem_type, str(file_path)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def make_registry():
    return _make_registry


@pytest.fixture
def write_geometry_file():
    return _write_geometry_file


@pytest.fixture
def write_nodal_result_file():
    return _write_nodal_result_file


@pytest.fixture
def register_result_block():
    return _register_result_block
