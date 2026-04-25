"""
ExternalResultWriter — write custom nodal/element results into the L1 HDF5 store.

Files written:
  l1/results/{result_group}/external__{step}__{field}.h5

HDF5 internal layout mirrors the ODB schema exactly:
  Nodal:   /NODAL/{instance}/data          float32 [num_frames, N_nodes, ncomp]
  Element: /ELEMENT_NODAL/{instance}/{etype}/data
                                            float32 [num_frames, N_elem_of_etype, 1, ncomp]

After writing, a result_files + result_blocks row is inserted into manifest.db
so the field appears in overview and is readable by the existing result_service.
"""
import os
import json
import numpy as np
import h5py

from ..infra.manifest_repo import ManifestRepo


class ExternalResultWriter:
    def __init__(self, workspace: str, result_group: str):
        self.workspace = workspace
        self.result_group = result_group
        self._manifest = ManifestRepo(workspace)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _geom_h5(self, instance: str) -> str:
        return self._manifest.get_geom_path(instance) or \
               os.path.join(self.workspace, "l1", "geometry", f"{instance}.h5")

    def _out_h5(self, step: str, field: str) -> str:
        rg_dir = os.path.join(
            self.workspace, "l1", "results", self.result_group
        )
        os.makedirs(rg_dir, exist_ok=True)
        return os.path.join(rg_dir, f"external__{step}__{field}.h5")

    def _out_h5_rel(self, step: str, field: str) -> str:
        return os.path.join(
            "l1", "results", self.result_group, f"external__{step}__{field}.h5"
        )

    def clear_field(self, step: str, field: str) -> None:
        """Delete existing HDF5 file and manifest rows for this (result_group, step, field)."""
        h5 = self._out_h5(step, field)
        if os.path.exists(h5):
            os.remove(h5)
        self._manifest.delete_external_result(self.result_group, step, field)

    # ── public API ────────────────────────────────────────────────────────────

    def write_nodal(
        self,
        instance: str,
        step: str,
        field: str,
        components: list,
        frames: list,
    ) -> int:
        """
        Write per-node scalar/vector values.

        frames: [{"frame_idx": int, "frame_value": float,
                  "data": [{"label": int, "values": [float, ...]}]}]

        Returns number of frames written.
        """
        geom = self._geom_h5(instance)
        if not os.path.exists(geom):
            raise FileNotFoundError(f"Geometry HDF5 not found: {geom}")

        with h5py.File(geom, "r") as gf:
            node_labels = gf["nodes/labels"][:]   # sorted int32 [N]

        N = len(node_labels)
        ncomp = len(components)
        num_frames = len(frames)

        data = np.full((num_frames, N, ncomp), np.nan, dtype=np.float32)

        for fi, frame in enumerate(frames):
            for entry in frame["data"]:
                label = int(entry["label"])
                row = int(np.searchsorted(node_labels, label))
                if row < N and node_labels[row] == label:
                    data[fi, row, :] = entry["values"][:ncomp]

        out = self._out_h5(step, field)
        with h5py.File(out, "a") as hf:
            path = f"/NODAL/{instance}/data"
            if path in hf:
                del hf[path]
            hf.create_dataset(path, data=data, compression="gzip", compression_opts=4)
            hf[f"/NODAL/{instance}"].attrs["components"] = json.dumps(components)

        self._manifest.register_external_result(
            result_group=self.result_group,
            step_name=step,
            field_name=field,
            file_path=self._out_h5_rel(step, field),
            components=components,
            positions=["NODAL"],
            instance_name=instance,
            position="NODAL",
            frames=frames,
        )
        return num_frames

    def write_element(
        self,
        instance: str,
        step: str,
        field: str,
        components: list,
        frames: list,
    ) -> int:
        """
        Write per-element scalar/vector values.
        One value per element (n_local_nodes=1), stored as ELEMENT_NODAL.

        frames: same structure as write_nodal.
        Returns number of frames written.
        """
        geom = self._geom_h5(instance)
        if not os.path.exists(geom):
            raise FileNotFoundError(f"Geometry HDF5 not found: {geom}")

        # Build label → (etype, row) map from all element types
        label_to_etype_row: dict = {}
        etype_labels: dict = {}   # etype → sorted int32 array
        with h5py.File(geom, "r") as gf:
            if "elements" not in gf:
                raise KeyError(f"No /elements group in geometry HDF5: {geom}")
            for etype in gf["elements"]:
                lbl = gf[f"elements/{etype}/labels"][:]
                etype_labels[etype] = lbl
                for row_idx, lbl_val in enumerate(lbl):
                    label_to_etype_row[int(lbl_val)] = (etype, row_idx)

        ncomp = len(components)
        num_frames = len(frames)

        # Pre-allocate per-etype arrays
        etype_data: dict = {
            et: np.full((num_frames, len(lbl), 1, ncomp), np.nan, dtype=np.float32)
            for et, lbl in etype_labels.items()
        }

        for fi, frame in enumerate(frames):
            for entry in frame["data"]:
                label = int(entry["label"])
                if label not in label_to_etype_row:
                    continue
                etype, row = label_to_etype_row[label]
                etype_data[etype][fi, row, 0, :] = entry["values"][:ncomp]

        out = self._out_h5(step, field)
        with h5py.File(out, "a") as hf:
            for etype, arr in etype_data.items():
                path = f"/ELEMENT_NODAL/{instance}/{etype}/data"
                if path in hf:
                    del hf[path]
                hf.create_dataset(path, data=arr, compression="gzip", compression_opts=4)
            hf[f"/ELEMENT_NODAL/{instance}"].attrs["components"] = json.dumps(components)

        self._manifest.register_external_result(
            result_group=self.result_group,
            step_name=step,
            field_name=field,
            file_path=self._out_h5_rel(step, field),
            components=components,
            positions=["ELEMENT_NODAL"],
            instance_name=instance,
            position="ELEMENT_NODAL",
            frames=frames,
        )
        return num_frames
