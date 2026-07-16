import logging
import os
import threading
import time
from collections import OrderedDict
import h5py
import numpy as np
from typing import Dict, Optional, Tuple

from .config import settings

logger = logging.getLogger(__name__)

# Thrash detection: an ODB evicted and then reloaded within this window means
# the active set is larger than the cap, so the LRU is buying nothing.
_THRASH_WINDOW_S = 300.0
# Thrashing produces one event per access; report at most this often.
_THRASH_LOG_INTERVAL_S = 60.0


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

        # Read-only cache for legend/color-code computations. L1 geometry data
        # is immutable once the ODB is loaded, so the expensive per-instance H5
        # scans (unique etypes / materials / section_types) and the global color
        # map can be memoized. Keyed by tuples, see color_service. NOT used for
        # anything that depends on user-editable state (legend overrides).
        self.legend_scan_cache: Dict[tuple, object] = {}

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

    Two-tier by design:
      - `_known`  : every ODB/project we could serve → (workspace, status).
                    One dict entry each, so this stays flat regardless of count.
      - `loaded`  : the ModelIndex objects actually resident in RAM, capped at
                    `max_loaded` and evicted least-recently-used first.

    A ModelIndex costs roughly 42 B per surface triangle + 12 B per node
    (~1.4 GB for a 10M-node model), so loading every ready project — as this
    class used to do — grows without bound and makes startup scale with the
    project count. `get()` therefore materialises on demand and keeps only the
    working set.

    Thread-safe. `_lock` guards the dicts and is never held across HDF5 IO;
    a per-odb_id load lock keeps two concurrent requests from loading the same
    workspace twice. Lock order is always load-lock → `_lock`, never reversed.
    """
    def __init__(self, max_loaded: Optional[int] = None):
        # Public name kept: tests inject via registry.loaded[id] = idx and
        # simright_service reads registry.loaded.keys().
        # OrderedDict doubles as the LRU list — leftmost is least recent.
        self.loaded: "OrderedDict[str, ModelIndex]" = OrderedDict()
        self._known: Dict[str, Tuple[str, str]] = {}
        self._load_locks: Dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self.max_loaded = max(1, int(
            settings.max_loaded_projects if max_loaded is None else max_loaded))

        # Thrash detection state: odb_id -> monotonic time it was evicted.
        self._evicted_at: "OrderedDict[str, float]" = OrderedDict()
        self._thrash_events = 0
        # None = never logged. Not 0.0: time.monotonic() counts from boot, so on
        # a freshly started container it is small enough that `now - 0.0` would
        # fall inside the rate-limit window and swallow the first warning.
        self._last_thrash_log: Optional[float] = None

    def register(self, odb_id: str, workspace: str, status: str) -> None:
        """
        Record that an ODB exists and where, without reading a single byte.
        Cheap enough to call for every row on every poll.
        """
        if not os.path.exists(workspace):
            logger.warning(
                "ODB %s: workspace '%s' not found on disk, skipping register",
                odb_id, workspace,
            )
            return
        with self._lock:
            self._known[odb_id] = (workspace, status)

    def load(self, odb_id: str, workspace: str, status: str) -> None:
        """
        Register an ODB and materialise it now. Used by the startup preload and
        by callers that must close the poll gap (see GET /api/projects/{id}).
        """
        self.register(odb_id, workspace, status)
        self._materialize(odb_id)

    def _materialize(self, odb_id: str) -> Optional[ModelIndex]:
        """Load `odb_id` into RAM if known. Heavy IO runs outside `_lock`."""
        with self._lock:
            idx = self.loaded.get(odb_id)
            if idx is not None:
                self.loaded.move_to_end(odb_id)
                return idx
            entry = self._known.get(odb_id)
            if entry is None:
                return None
            load_lock = self._load_locks.get(odb_id)
            if load_lock is None:
                load_lock = self._load_locks[odb_id] = threading.Lock()

        with load_lock:
            # Another thread may have finished loading while we queued here.
            with self._lock:
                idx = self.loaded.get(odb_id)
                if idx is not None:
                    self.loaded.move_to_end(odb_id)
                    return idx

            workspace, status = entry
            idx = ModelIndex(odb_id, workspace)
            if status == "ready":
                idx.load_l2_render_data()

            with self._lock:
                self.loaded[odb_id] = idx
                self.loaded.move_to_end(odb_id)
                self._note_reload_locked(odb_id)
                self._evict_locked()
            return idx

    def _evict_locked(self) -> None:
        """Drop LRU entries past the cap. Caller must hold `_lock`."""
        while len(self.loaded) > self.max_loaded:
            victim_id, _ = self.loaded.popitem(last=False)
            # An in-flight request may still hold a reference; refcounting frees
            # the arrays once it returns. A later get() simply reloads it.
            self._evicted_at[victim_id] = time.monotonic()
            self._evicted_at.move_to_end(victim_id)
            # Only recent evictions can prove thrashing — keep the map bounded.
            while len(self._evicted_at) > 4 * self.max_loaded:
                self._evicted_at.popitem(last=False)
            logger.info(
                "Evicted ODB %s from memory (LRU, cap=%d)", victim_id, self.max_loaded
            )

    def _note_reload_locked(self, odb_id: str) -> None:
        """
        Warn when the working set outgrows the cap. Caller must hold `_lock`.

        Reloading an ODB shortly after evicting it means the LRU is evicting
        exactly what is about to be needed — hit rate collapses to ~0 and every
        access re-reads L2 from disk. That degrades silently, so say it out loud.
        """
        evicted_at = self._evicted_at.pop(odb_id, None)
        if evicted_at is None:
            return
        now = time.monotonic()
        if now - evicted_at > _THRASH_WINDOW_S:
            return          # a genuinely cold project coming back, not thrashing

        self._thrash_events += 1
        if (self._last_thrash_log is not None
                and now - self._last_thrash_log < _THRASH_LOG_INTERVAL_S):
            return          # rate-limit: thrashing fires on every access
        self._last_thrash_log = now
        logger.warning(
            "ODB %s reloaded %.0fs after eviction — %d thrash event(s) so far. "
            "The active set exceeds APP_MAX_LOADED_PROJECTS=%d, so cached "
            "projects are evicted before reuse and every access re-reads L2 "
            "from disk. Raise the cap above the number of concurrently active "
            "projects (each costs ~42 B/triangle + 12 B/node).",
            odb_id, now - evicted_at, self._thrash_events, self.max_loaded,
        )
        self._thrash_events = 0

    def get(self, odb_id: str) -> Optional[ModelIndex]:
        """
        Return the ModelIndex, loading it on first use. None only when the id is
        unknown. Callers see the same contract as the old eager registry.
        """
        with self._lock:
            idx = self.loaded.get(odb_id)
            if idx is not None:
                self.loaded.move_to_end(odb_id)
                return idx
            if odb_id not in self._known:
                return None
        return self._materialize(odb_id)

    def peek(self, odb_id: str) -> Optional[ModelIndex]:
        """
        Resident-only lookup — never triggers a load. The poll thread uses this:
        calling get() there would drag every project back into RAM every 10s.
        """
        with self._lock:
            return self.loaded.get(odb_id)

    def known_ids(self) -> set:
        """Every id we can serve, resident or not."""
        with self._lock:
            return set(self._known) | set(self.loaded)

    def upgrade(self, odb_id: str):
        """
        Supplement an ODB with L2 render data after l1_done → ready.
        If it is not resident there is nothing to patch: the status bump is
        enough, and the next get() loads it with L2 data included.
        """
        with self._lock:
            entry = self._known.get(odb_id)
            if entry is not None:
                self._known[odb_id] = (entry[0], "ready")
            idx = self.loaded.get(odb_id)
        if idx is None:
            return
        idx.load_l2_render_data()       # IO outside lock
        with self._lock:
            idx.is_render_ready = True

    def unload(self, odb_id: str):
        """
        Forget an ODB entirely.  Called by DELETE /api/jobs before the DB record
        is removed, so it must also drop the `_known` entry — otherwise the next
        get() would happily reload the deleted workspace.
        """
        with self._lock:
            self.loaded.pop(odb_id, None)
            self._known.pop(odb_id, None)
            self._load_locks.pop(odb_id, None)


# Global singleton instance (initialized during FastAPI lifespan / Gunicorn pre-fork)
registry = OdbRegistry()
