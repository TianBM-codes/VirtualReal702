"""
Internal sets (_PickedSetNN) must be EXPOSED in the model-update tunable
parameter catalog (not filtered out), each tagged with is_internal so the UI
can distinguish them from user-named sets. The section region a material is
assigned to is itself an internal set, so hiding them dropped the most
meaningful tunable property sets.
"""
import os

from src.inp import parse_inp
from services.model_update.analysis.fem_catalog_service import (
    _iter_elset_entries,
    _extract_quantity_set_capabilities,
)


def _model(tmp_path, body):
    p = os.path.join(str(tmp_path), "m.inp")
    with open(p, "w") as fh:
        fh.write(body)
    return parse_inp(p)


_INP = (
    "*Part, name=PartA\n"
    "*Node\n"
    "1, 0.0, 0.0, 0.0\n2, 1.0, 0.0, 0.0\n3, 1.0, 1.0, 0.0\n4, 0.0, 1.0, 0.0\n"
    "5, 0.0, 0.0, 1.0\n6, 1.0, 0.0, 1.0\n7, 1.0, 1.0, 1.0\n8, 0.0, 1.0, 1.0\n"
    "*Element, type=C3D8R, elset=AllElems\n1, 1, 2, 3, 4, 5, 6, 7, 8\n"
    "*Elset, elset=_PickedSet1, internal, generate\n1, 1, 1\n"
    "*Elset, elset=UserSet\n1,\n"
    "*Solid Section, elset=_PickedSet1, material=Steel\n1.,\n"
    "*End Part\n"
    "*Assembly, name=Assembly\n*Instance, name=PartA-1, part=PartA\n*End Instance\n*End Assembly\n"
    "*Material, name=Steel\n*Elastic\n210000., 0.3\n"
)


def test_internal_set_exposed_with_flag(tmp_path):
    entries = {e["set_name"]: e for e in _iter_elset_entries(_model(tmp_path, _INP))}
    # Both the internal _PickedSet1 and the user set are present (not filtered).
    assert entries["_PickedSet1"]["is_internal"] == 1
    assert entries["UserSet"]["is_internal"] == 0


def test_internal_property_set_is_tunable(tmp_path):
    caps = _extract_quantity_set_capabilities(_model(tmp_path, _INP))
    # The section region (_PickedSet1) is exposed as a tunable E capability,
    # flagged internal, carrying its material name.
    prop = [c for c in caps if c["set_name"] == "_PickedSet1" and c["quantity_code"] == "E"]
    assert prop, "internal section set should appear as a tunable E capability"
    assert prop[0]["is_internal"] == 1
    assert prop[0]["material_name"] == "Steel"
