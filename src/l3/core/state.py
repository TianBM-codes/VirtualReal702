import logging
import os
import threading
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

        # Smooth mode result slicing: instance_name -> [Nt, 3] array mapping
        # each triangle to its 3 node rows in the L1 results file.
        # With indexed geometry, computed as vtx_node_row[indices].
        self.source_node_rows: Dict[str, np.ndarray] = {}

        # Per-face element type string (b"S4R", b"C3D8R", ...): instance_name -> [Nt] S8 array.
        # Used by hdf5_repo to build the correct L1 dataset path: elements/<etype>/labels.
        self.source_elem_etype: Dict[str, np.ndarray] = {}

        # Indexed geometry extras (present when render.h5 uses indexed format).
        # vtx_node_row [Nv]: FEM node row for each vertex in the indexed vertex buffer.
        # vtx_tri_idx  [Nv]: first triangle index for each vertex (for element-level mapping).
        self.vtx_node_row:    Dict[str, np.ndarray] = {}
        self.vtx_tri_idx:     Dict[str, np.ndarray] = {}
        # render_indices [Nt, 3] int32: vertex indices per triangle
        self.render_indices:  Dict[str, np.ndarray] = {}

        # source_local_node_idx [Nt, 3] int16: local node index within the source
        # element's conn array for each triangle corner. Used for ELEMENT_NODAL lookup.
        self.source_local_node_idx: Dict[str, np.ndarray] = {}

        # Averaging domain data loaded from render.h5 averaging/ group.
        # Keys per instance: elem_etype, elem_row, elem_section_id, elem_kind,
        #   default_domain_id, default_feature_angle_deg,
        #   adj_src, adj_dst, adj_angle_deg  (may be absent if no adjacency)
        self.averaging_data: Dict[str, dict] = {}

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
                    # Indexed geometry extras
                    if "render/vtx_node_row" in f:
                        self.vtx_node_row[inst_name] = f["render/vtx_node_row"][:]
                    if "render/vtx_tri_idx" in f:
                        self.vtx_tri_idx[inst_name]  = f["render/vtx_tri_idx"][:]
                    if "render/indices" in f:
                        self.render_indices[inst_name] = f["render/indices"][:]
                    if "render/source_local_node_idx" in f:
                        self.source_local_node_idx[inst_name] = \
                            f["render/source_local_node_idx"][:]
                    # Averaging domain data
                    if "averaging/elem_etype" in f:
                        ag = f["averaging"]
                        avd = {
                            "elem_etype":      ag["elem_etype"][:],
                            "elem_row":        ag["elem_row"][:],
                            "elem_section_id": ag["elem_section_id"][:],
                            "elem_kind":       ag["elem_kind"][:],
                            "default_domain_id": ag["default_domain_id"][:],
                            "default_feature_angle_deg": float(
                                ag.attrs.get("default_feature_angle_deg", 20.0)),
                        }
                        if "adj_src" in ag:
                            avd["adj_src"]       = ag["adj_src"][:]
                            avd["adj_dst"]       = ag["adj_dst"][:]
                            avd["adj_angle_deg"] = ag["adj_angle_deg"][:]
                        else:
                            avd["adj_src"]       = np.zeros(0, dtype=np.int32)
                            avd["adj_dst"]       = np.zeros(0, dtype=np.int32)
                            avd["adj_angle_deg"] = np.zeros(0, dtype=np.float32)
                        self.averaging_data[inst_name] = avd
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
    Thread-safe: a Lock protects the loaded dict, which is mutated by both
    request handlers and the per-worker polling daemon thread.
    """
    def __init__(self):
        self.loaded: Dict[str, ModelIndex] = {}
        self._lock = threading.Lock()

    def load(self, odb_id: str, workspace: str, status: str):
        """
        Load an ODB into memory.  Called at startup and by the polling thread
        when a new ready/l1_done job is detected.
        Skipped silently if workspace does not exist on disk.
        """
        if not os.path.exists(workspace):
            logger.warning(
                "ODB %s: workspace '%s' not found on disk, skipping load",
                odb_id, workspace,
            )
            return
        idx = ModelIndex(odb_id, workspace)
        if status == "ready":
            idx.load_l2_render_data()
        with self._lock:
            self.loaded[odb_id] = idx

    def upgrade(self, odb_id: str):
        """
        Supplement an already-loaded ModelIndex with L2 render data after
        the job transitions from l1_done → ready.
        The heavy IO runs outside the lock; only the final flag-set is locked.
        """
        with self._lock:
            idx = self.loaded.get(odb_id)
        if idx is None:
            return
        idx.load_l2_render_data()       # IO outside lock
        with self._lock:
            idx.is_render_ready = True

    def unload(self, odb_id: str):
        """
        Remove an ODB from memory.  Called by DELETE /api/jobs before the DB
        record is removed so that in-flight requests receive a 404 immediately.
        """
        with self._lock:
            self.loaded.pop(odb_id, None)

    def get(self, odb_id: str) -> Optional[ModelIndex]:
        with self._lock:
            return self.loaded.get(odb_id)


# Global singleton instance (initialized during FastAPI lifespan / Gunicorn pre-fork)
registry = OdbRegistry()
