"""
Regression test for the INP → L1 exporter writing **assembly-level** element
sets to sets.h5.

In a CAE-exported INP the user-named sets (CDM_FIX, QA_TEST, …) and almost all
internal surface sets (_Head-Seal_S3, _PickedSetNN, …) are defined inside the
*Assembly block, each scoped to an instance. The exporter previously wrote only
part-level elsets, so those assembly sets never reached sets.h5 and the L3
color-code `elset` scheme could not list them (only the few part-level
_PickedSetNN showed up).

Assembly elsets carry instance-local element labels, so they are written under
element_sets/{INSTANCE}/{set} alongside part-level sets.
"""
import os
import sqlite3

import h5py

from src.inp import parse_inp
from src.inp.exporter import export_l1


_INP = (
    "*Part, name=PartA\n"
    "*Node\n"
    "1, 0.0, 0.0, 0.0\n2, 1.0, 0.0, 0.0\n3, 1.0, 1.0, 0.0\n4, 0.0, 1.0, 0.0\n"
    "5, 0.0, 0.0, 1.0\n6, 1.0, 0.0, 1.0\n7, 1.0, 1.0, 1.0\n8, 0.0, 1.0, 1.0\n"
    "*Element, type=C3D8R, elset=AllElems\n1, 1, 2, 3, 4, 5, 6, 7, 8\n"
    "*Elset, elset=_PickedSet1, internal\n1,\n"
    "*Solid Section, elset=_PickedSet1, material=Steel\n1.,\n"
    "*End Part\n"
    "*Assembly, name=Assembly\n"
    "*Instance, name=PartA-1, part=PartA\n*End Instance\n"
    "*Elset, elset=UserBC, instance=PartA-1\n1,\n"
    "*Elset, elset=_Surf_S3, internal, instance=PartA-1\n1,\n"
    "*End Assembly\n"
    "*Material, name=Steel\n*Elastic\n210000., 0.3\n"
)


def test_assembly_elsets_written_to_sets_h5(tmp_path):
    inp_path = os.path.join(str(tmp_path), "m.inp")
    with open(inp_path, "w") as fh:
        fh.write(_INP)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    export_l1(parse_inp(inp_path), ws)

    with h5py.File(os.path.join(ws, "l1", "sets", "sets.h5"), "r") as f:
        names = set(f["element_sets/PARTA-1"].keys())

    # Part-level internal set (always was written) plus both assembly-level sets.
    assert "_PickedSet1" in names
    assert "UserBC" in names      # user-named assembly set — previously dropped
    assert "_Surf_S3" in names    # internal assembly surface set — previously dropped

    # manifest carries scope + is_internal flag.
    con = sqlite3.connect(os.path.join(ws, "manifest.db"))
    rows = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT set_name, set_scope, is_internal FROM element_sets "
            "WHERE instance_name='PARTA-1'"
        )
    }
    assert rows["UserBC"] == ("ASSEMBLY", 0)
    assert rows["_Surf_S3"] == ("ASSEMBLY", 1)
    assert rows["_PickedSet1"] == ("PART", 1)
