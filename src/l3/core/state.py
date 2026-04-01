import logging
import os
import h5py
import numpy as np
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ModelIndex:
    """
    Holds the in-memory indexes and routing arrays for a specific ODB.
    Loaded pre-fork to maximize CoW sharing across Gunicorn workers.
    """
    def __init__(self, odb_id: str, workspace: str):
        self.odb_id = odb_id
        self.workspace = workspace
        self.is_render_ready = False

        # L2 Global Coords [N, 3] float32: instance_name -> coords
        # Used for fast numpy-based BBox intersect/contained filtering.
        self.coords_global: Dict[str, np.ndarray] = {}

        # Pick Map: instance_name -> [R] array mapping render_face_idx to source_elem_row
        # O(1) array lookup for pick queries.
        self.render_source_elem_row: Dict[str, np.ndarray] = {}

        # Smooth mode result slicing: instance_name -> [R, K] array mapping
        # render_face_idx to source node rows in the L1 results file.
        self.source_node_rows: Dict[str, np.ndarray] = {}

        # Per-face element type string (b"S4R", b"C3D8R", ...): instance_name -> [R] S8 array.
        # Used by hdf5_repo to build the correct L1 dataset path: elements/<etype>/labels.
        self.source_elem_etype: Dict[str, np.ndarray] = {}

        # Octree for spatial coarse-filtering (bbox queries).
        # instance_name -> dict with keys:
        #   node_bbox     [N, 6] float32   xmin,ymin,zmin,xmax,ymax,zmax
        #   node_children [N, 8] int32     child node indices (-1 = absent)
        #   node_is_leaf  [N]    uint8
        #   face_indices  [F]    int32     render face rows (leaf nodes only, concatenated)
        #   leaf_offsets  [N+1]  int32     slice bounds into face_indices per node
        self.octree: Dict[str, dict] = {}

    def load_l2_render_data(self):
        """Called when status == 'ready'. Loads L2 HDF5 buffers into memory."""
        l2_render_dir = os.path.join(self.workspace, "l2", "render")
        l2_geom_dir = os.path.join(self.workspace, "l2", "geometry")

        if not os.path.exists(l2_render_dir) or not os.path.exists(l2_geom_dir):
            logger.warning(
                "ODB %s: L2 directories not found (render=%s, geom=%s)",
                self.odb_id, l2_render_dir, l2_geom_dir,
            )
            return

        all_ok = True
        for fname in os.listdir(l2_render_dir):
            if not fname.endswith("_render.h5"):
                continue
            inst_name = fname.replace("_render.h5", "")
            render_h5_path = os.path.join(l2_render_dir, fname)

            try:
                with h5py.File(render_h5_path, "r") as f:
                    if "render/source_elem_row" in f:
                        self.render_source_elem_row[inst_name] = f["render/source_elem_row"][:]
                    if "render/source_node_rows" in f:
                        self.source_node_rows[inst_name] = f["render/source_node_rows"][:]
                    if "render/source_etype_str" in f:
                        self.source_elem_etype[inst_name] = f["render/source_etype_str"][:]
                    if "octree/node_bbox" in f:
                        self.octree[inst_name] = {
                            "node_bbox":     f["octree/node_bbox"][:],
                            "node_children": f["octree/node_children"][:],
                            "node_is_leaf":  f["octree/node_is_leaf"][:],
                            "face_indices":  f["octree/face_indices"][:],
                            "leaf_offsets":  f["octree/leaf_offsets"][:],
                        }
            except Exception:
                logger.exception("Failed to load render HDF5: %s", render_h5_path)
                all_ok = False
                continue

            geom_h5_path = os.path.join(l2_geom_dir, f"{inst_name}_surface.h5")
            if os.path.exists(geom_h5_path):
                try:
                    with h5py.File(geom_h5_path, "r") as f:
                        if "nodes/coords_global" in f:
                            self.coords_global[inst_name] = f["nodes/coords_global"][:]
                except Exception:
                    logger.exception("Failed to load geometry HDF5: %s", geom_h5_path)
                    all_ok = False

        if all_ok:
            self.is_render_ready = True
        else:
            logger.error(
                "ODB %s: one or more L2 files failed to load; is_render_ready remains False",
                self.odb_id,
            )


class OdbRegistry:
    """
    Global singleton registry holding ModelIndex instances.
    """
    def __init__(self):
        # odb_id -> ModelIndex
        self.loaded: Dict[str, ModelIndex] = {}

    def load(self, odb_id: str, workspace: str, status: str):
        idx = ModelIndex(odb_id, workspace)
        if status == "ready":
            idx.load_l2_render_data()
        self.loaded[odb_id] = idx

    def get(self, odb_id: str) -> Optional[ModelIndex]:
        return self.loaded.get(odb_id)


# Global singleton instance (initialized during FastAPI lifespan / Gunicorn pre-fork)
registry = OdbRegistry()
