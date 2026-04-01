"""
Layer 3: Resolver

Takes an InpModel from the Parser and resolves all forward references:
1. Set nesting: Nset/Elset that reference other sets by name → expand to labels
2. Instance transforms: Rotation (center + axis + angle) → 4×4 matrix
3. Surface entries: (elset_name, face_id) stored as-is (full face expansion
   into node lists requires topology lookup and is deferred to the Exporter)

The Resolver operates in-place, modifying the InpModel it receives.
After resolution:
- All Nset.node_labels and Elset.elem_labels are fully populated
- All set_refs lists are cleared
- All Instance objects have a transform_matrix (4×4 numpy array) attribute
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set

import numpy as np

from .diagnostics import (
    DiagnosticCollector,
    UNRESOLVED_SET, SET_CYCLE,
)
from .model import (
    Assembly, AssemblyElset, AssemblyNset,
    Elset, InpModel, Instance, Nset, Part, Rotation,
)


def resolve(model: InpModel) -> InpModel:
    """
    Resolve all forward references in-place.  Returns the same model.
    New diagnostics are appended to model.diagnostics.
    """
    r = Resolver(model)
    r.run()
    return model


class Resolver:
    def __init__(self, model: InpModel) -> None:
        self._model = model
        self._diag  = DiagnosticCollector()

    def run(self) -> None:
        # 1. Expand set nesting within each Part
        for part in self._model.parts.values():
            self._resolve_part_sets(part)

        # 2. Expand set nesting within Assembly
        if self._model.assembly is not None:
            self._resolve_assembly_sets(self._model.assembly)

        # 3. Compute transform matrices for each Instance
        if self._model.assembly is not None:
            for inst in self._model.assembly.instances.values():
                inst.transform_matrix = _build_transform(inst)  # type: ignore[attr-defined]

        # Append new diagnostics
        self._model.diagnostics.extend(self._diag.items)

    # ------------------------------------------------------------------
    # Set nesting — Part level
    # ------------------------------------------------------------------

    def _resolve_part_sets(self, part: Part) -> None:
        # Nsets
        for name in list(part.nsets.keys()):
            self._expand_nset(part, name, set())

        # Elsets
        for name in list(part.elsets.keys()):
            self._expand_elset(part, name, set())

    def _expand_nset(
        self, part: Part, name: str, visiting: Set[str]
    ) -> List[int]:
        nset = part.nsets.get(name)
        if nset is None:
            self._diag.warning(UNRESOLVED_SET,
                               f"Nset '{name}' not found in Part '{part.name}'")
            return []
        if not nset.set_refs:
            return nset.node_labels

        if name in visiting:
            self._diag.error(SET_CYCLE,
                             f"Nset cycle detected at '{name}' in Part '{part.name}'")
            nset.set_refs = []
            return nset.node_labels

        visiting = visiting | {name}
        for ref in nset.set_refs:
            nset.node_labels.extend(self._expand_nset(part, ref, visiting))
        nset.set_refs = []
        # Deduplicate while preserving order
        nset.node_labels = _dedup(nset.node_labels)
        return nset.node_labels

    def _expand_elset(
        self, part: Part, name: str, visiting: Set[str]
    ) -> List[int]:
        elset = part.elsets.get(name)
        if elset is None:
            self._diag.warning(UNRESOLVED_SET,
                               f"Elset '{name}' not found in Part '{part.name}'")
            return []
        if not elset.set_refs:
            return elset.elem_labels

        if name in visiting:
            self._diag.error(SET_CYCLE,
                             f"Elset cycle detected at '{name}' in Part '{part.name}'")
            elset.set_refs = []
            return elset.elem_labels

        visiting = visiting | {name}
        for ref in elset.set_refs:
            elset.elem_labels.extend(self._expand_elset(part, ref, visiting))
        elset.set_refs = []
        elset.elem_labels = _dedup(elset.elem_labels)
        return elset.elem_labels

    # ------------------------------------------------------------------
    # Set nesting — Assembly level
    # ------------------------------------------------------------------

    def _resolve_assembly_sets(self, asm: Assembly) -> None:
        for name in list(asm.nsets.keys()):
            self._expand_assembly_nset(asm, name, set())
        for name in list(asm.elsets.keys()):
            self._expand_assembly_elset(asm, name, set())

    def _expand_assembly_nset(
        self, asm: Assembly, name: str, visiting: Set[str]
    ) -> List[int]:
        anset = asm.nsets.get(name)
        if anset is None:
            return []
        if not anset.set_refs:
            return anset.node_labels

        if name in visiting:
            self._diag.error(SET_CYCLE,
                             f"Assembly Nset cycle at '{name}'")
            anset.set_refs = []
            return anset.node_labels

        visiting = visiting | {name}
        for ref in anset.set_refs:
            # ref may be "instance_name.set_name" or just "set_name"
            if "." in ref:
                inst_name, set_name = ref.split(".", 1)
                inst = asm.instances.get(inst_name)
                if inst:
                    part = self._model.parts.get(inst.part_name)
                    if part and set_name in part.nsets:
                        anset.node_labels.extend(part.nsets[set_name].node_labels)
                        continue
                self._diag.warning(UNRESOLVED_SET,
                                   f"Cannot resolve Assembly Nset ref '{ref}'")
            else:
                # Try another assembly-level nset
                anset.node_labels.extend(
                    self._expand_assembly_nset(asm, ref, visiting)
                )
        anset.set_refs = []
        anset.node_labels = _dedup(anset.node_labels)
        return anset.node_labels

    def _expand_assembly_elset(
        self, asm: Assembly, name: str, visiting: Set[str]
    ) -> List[int]:
        aelset = asm.elsets.get(name)
        if aelset is None:
            return []
        if not aelset.set_refs:
            return aelset.elem_labels

        if name in visiting:
            self._diag.error(SET_CYCLE,
                             f"Assembly Elset cycle at '{name}'")
            aelset.set_refs = []
            return aelset.elem_labels

        visiting = visiting | {name}
        for ref in aelset.set_refs:
            if "." in ref:
                inst_name, set_name = ref.split(".", 1)
                inst = asm.instances.get(inst_name)
                if inst:
                    part = self._model.parts.get(inst.part_name)
                    if part and set_name in part.elsets:
                        aelset.elem_labels.extend(part.elsets[set_name].elem_labels)
                        continue
                self._diag.warning(UNRESOLVED_SET,
                                   f"Cannot resolve Assembly Elset ref '{ref}'")
            else:
                aelset.elem_labels.extend(
                    self._expand_assembly_elset(asm, ref, visiting)
                )
        aelset.set_refs = []
        aelset.elem_labels = _dedup(aelset.elem_labels)
        return aelset.elem_labels


# ---------------------------------------------------------------------------
# Transform matrix construction
# ---------------------------------------------------------------------------

def _build_transform(inst: Instance) -> np.ndarray:
    """
    Build a 4×4 homogeneous transformation matrix for an Instance.

    Abaqus applies transforms as: first translate, then rotate.
    The rotation is defined as: rotate `angle_deg` degrees around `axis`
    passing through `center`.

    Matrix convention: column-vector  p' = M @ p  (same as L1 ODB convention).
    """
    T = np.eye(4, dtype=np.float64)
    tx, ty, tz = inst.translation
    T[0, 3] = tx
    T[1, 3] = ty
    T[2, 3] = tz

    if inst.rotation is None:
        return T

    rot = inst.rotation
    cx, cy, cz = rot.center
    ax, ay, az = rot.axis
    theta = math.radians(rot.angle_deg)

    # Normalise axis
    length = math.sqrt(ax*ax + ay*ay + az*az)
    if length < 1e-12:
        return T
    ax, ay, az = ax / length, ay / length, az / length

    # Rodrigues rotation matrix
    c = math.cos(theta)
    s = math.sin(theta)
    t = 1.0 - c
    R = np.array([
        [t*ax*ax + c,    t*ax*ay - s*az, t*ax*az + s*ay],
        [t*ax*ay + s*az, t*ay*ay + c,    t*ay*az - s*ax],
        [t*ax*az - s*ay, t*ay*az + s*ax, t*az*az + c   ],
    ], dtype=np.float64)

    # Rotation about a point: translate to origin, rotate, translate back
    # t_total = T_to_center_inv @ R @ T_to_center @ T_translation
    # Build as compound 4×4
    center = np.array([cx, cy, cz], dtype=np.float64)
    trans_vec = np.array([tx, ty, tz], dtype=np.float64)

    M = np.eye(4, dtype=np.float64)
    M[:3, :3] = R
    # The rotation is applied after translation, around `center`
    # Full transform: first move by translation, then rotate around center
    # p' = R @ (p + trans - center) + center
    #    = R @ p + R @ (trans - center) + center
    offset = R @ (trans_vec - center) + center
    M[:3, 3] = offset

    return M


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _dedup(lst: List[int]) -> List[int]:
    """Deduplicate preserving insertion order."""
    seen: Set[int] = set()
    result = []
    for x in lst:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result
