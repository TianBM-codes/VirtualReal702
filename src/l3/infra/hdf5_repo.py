"""
Read-only access to L1 HDF5 geometry files.
Used by query_service for pick queries (elem labels, node labels).
"""
from typing import List, Optional

import numpy as np
import h5py
import os


def _safe_instance_name(name: str) -> str:
    """Replicate l1_pack.safe() for fallback path construction."""
    return name.replace("/", "__").replace("\\", "__").replace(" ", "_")


class HDF5Repo:
    def __init__(self, workspace: str):
        self.workspace = workspace
        self.l1_geom_dir = os.path.join(workspace, "l1", "geometry")

    def _geom_path(self, instance_name: str) -> str:
        """Resolve geometry path via manifest; fall back to safe-name construction."""
        try:
            import sqlite3
            with sqlite3.connect(os.path.join(self.workspace, "manifest.db")) as conn:
                row = conn.execute(
                    "SELECT geom_path FROM instances WHERE instance_name=?",
                    (instance_name,),
                ).fetchone()
                if row and row[0]:
                    return os.path.join(self.workspace, row[0])
        except Exception:
            pass
        return os.path.join(self.l1_geom_dir, f"{_safe_instance_name(instance_name)}.h5")

    def get_elem_label(self, instance_name: str, etype_str: str, elem_row: int) -> int:
        """Return the element label for the given etype group and row index."""
        path = self._geom_path(instance_name)
        with h5py.File(path, "r") as f:
            return int(f[f"elements/{etype_str}/labels"][elem_row])

    def get_node_labels_for_elem(self, instance_name: str, etype_str: str, elem_row: int) -> list:
        """Return the corner node labels for a given etype group and element row."""
        path = self._geom_path(instance_name)
        with h5py.File(path, "r") as f:
            conn = f[f"elements/{etype_str}/conn"]  # [num_elems, n_corner_nodes]
            node_rows = conn[elem_row]
            # Filter sentinel -1 padding
            valid = node_rows[node_rows >= 0]
            node_labels = f["nodes/labels"][:]
            return [int(node_labels[r]) for r in valid]

    def get_node_labels_for_rows(self, instance_name: str, node_rows: list) -> list:
        """Return ODB node labels for a list of node row indices."""
        path = self._geom_path(instance_name)
        with h5py.File(path, "r") as f:
            labels = f["nodes/labels"][:]
            return [int(labels[r]) for r in node_rows]

    def get_node_coords(self, instance_name: str, node_row: int) -> list:
        """Return [x, y, z] undeformed coordinates for the given node row."""
        path = self._geom_path(instance_name)
        with h5py.File(path, "r") as f:
            return f["nodes/coords"][node_row].tolist()

    def get_attached_elem_labels(self, instance_name: str, node_row: int) -> Optional[List[int]]:
        """
        Return elem labels attached to the given node (via node_to_elements CSR).
        Returns None if the CSR group is absent (old workspace without the index).
        """
        path = self._geom_path(instance_name)
        with h5py.File(path, "r") as f:
            if "node_to_elements" not in f:
                return None
            g = f["node_to_elements"]
            offsets = g["offsets"][:]
            start, end = int(offsets[node_row]), int(offsets[node_row + 1])
            if start == end:
                return []
            return [int(x) for x in g["elem_label_data"][start:end]]

    def get_result_value_at_node(
        self,
        result_h5_path: str,
        instance_name: str,
        node_row: int,
        frame_idx: int,
        component_idx: int = 0,
    ) -> float:
        """
        Read a scalar result value from a NODAL result HDF5 file.
        Dataset layout: [num_frames, N, num_components]
        """
        with h5py.File(result_h5_path, "r") as f:
            ds = f[f"NODAL/{instance_name}/data"]
            if ds.ndim == 3:
                return float(ds[frame_idx, node_row, component_idx])
            elif ds.ndim == 2:
                return float(ds[frame_idx, node_row])
            else:
                return float(ds[node_row])
