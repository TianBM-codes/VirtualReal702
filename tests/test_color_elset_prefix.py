"""
Cross-instance elset color-code: element-set names are only unique within an
instance, so the schemes endpoint exposes them as 'INSTANCE.setname' and the
elset resolution recognises that prefix.

Goal (backend-only, front-end unchanged):
  - GET .../schemes returns the whole model's sets as qualified names.
  - The front-end broadcasts the full selection to every instance; each instance
    must pick out only its own sets, silently ignoring entries that belong to a
    different instance (no "Set not found"), while still raising for a set that
    *is* qualified to this instance but genuinely missing.
"""
import os

import h5py
import numpy as np

from src.inp import parse_inp
from src.inp.exporter import export_l1
from src.l3.services import color_service as C


# Two instances of one part, with instance-scoped assembly sets.
_INP = (
    "*Part, name=PA\n"
    "*Node\n"
    + "".join(f"{i}, {i}.0, 0.0, 0.0\n" for i in range(1, 17))
    + "*Element, type=C3D8R, elset=AllE\n"
    "1, 1, 2, 3, 4, 5, 6, 7, 8\n"
    "2, 9, 10, 11, 12, 13, 14, 15, 16\n"
    "*Elset, elset=_PickedSet1, internal\n1,\n"
    "*Solid Section, elset=AllE, material=Steel\n1.,\n"
    "*End Part\n"
    "*Assembly, name=Assembly\n"
    "*Instance, name=PA-1, part=PA\n*End Instance\n"
    "*Instance, name=PA-2, part=PA\n*End Instance\n"
    "*Elset, elset=UserA, instance=PA-1\n1,\n"
    "*Elset, elset=UserB, instance=PA-2\n2,\n"
    "*End Assembly\n"
    "*Material, name=Steel\n*Elastic\n210000., 0.3\n"
)


class _FakeIdx:
    """Minimal stand-in exposing only what the color helpers touch."""
    def __init__(self, ws):
        self.workspace = ws
        with h5py.File(os.path.join(ws, "l1", "sets", "sets.h5")) as f:
            insts = sorted(f["element_sets"].keys())
        self.source_elem_etype = {i: None for i in insts}
        self.legend_scan_cache = {}
        self.averaging_data = {}


def _build(tmp_path):
    inp = os.path.join(str(tmp_path), "m.inp")
    with open(inp, "w") as fh:
        fh.write(_INP)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    export_l1(parse_inp(inp), ws)
    return _FakeIdx(ws)


def test_split_instance_prefix():
    known = {"PA-1", "PA-2", "SIMPLECRDM-1-LIN-2-1"}
    assert C._split_instance_prefix("PA-1.UserA", known) == ("PA-1", "UserA")
    # First dot only; multi-dash instance still recognised.
    assert C._split_instance_prefix("SIMPLECRDM-1-LIN-2-1._PickedSet6", known) \
        == ("SIMPLECRDM-1-LIN-2-1", "_PickedSet6")
    # Unrecognised prefix → treated as a bare local name.
    assert C._split_instance_prefix("NotAnInstance.Foo", known) == (None, "NotAnInstance.Foo")
    assert C._split_instance_prefix("BarePlain", known) == (None, "BarePlain")


def test_schemes_returns_qualified_elsets(tmp_path):
    idx = _build(tmp_path)
    res = C.get_all_schemes(idx)
    assert "elset" in res["schemes"]
    elsets = set(res["elsets"])
    # Every set is instance-qualified, and both instances are represented.
    assert "PA-1.UserA" in elsets
    assert "PA-2.UserB" in elsets
    assert "PA-1._PickedSet1" in elsets and "PA-2._PickedSet1" in elsets


def test_labels_pick_own_sets_and_tolerate_foreign(tmp_path):
    idx = _build(tmp_path)
    et, row = C._full_element_arrays(idx, "PA-1")

    # Broadcast both instances' sets to PA-1: only PA-1's own set colors anything,
    # the PA-2 entry is silently ignored (no exception).
    labels = C._labels_from_elsets(idx, "PA-1", et, row, ["PA-1.UserA", "PA-2.UserB"])
    assert "PA-1.UserA" in labels          # element 1 → its own set
    assert "other" in labels               # element 2 → unlabeled
    assert "PA-2.UserB" not in labels      # foreign set never applied here

    # Only-foreign selection → all "other", still no error.
    only_foreign = C._labels_from_elsets(idx, "PA-1", et, row, ["PA-2.UserB"])
    assert set(only_foreign) == {"other"}


def test_missing_own_set_still_raises(tmp_path):
    idx = _build(tmp_path)
    et, row = C._full_element_arrays(idx, "PA-1")
    # A set qualified to THIS instance but absent is a real error.
    import pytest
    with pytest.raises(Exception):
        C._labels_from_elsets(idx, "PA-1", et, row, ["PA-1.NoSuchSet"])
