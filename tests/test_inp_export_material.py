"""
Regression tests for material_name resolution in the INP → L1 exporter.

These cover three layouts that previously left material_name empty (shown as
"none" in the L3 color-code legend):

  1. Part-level section whose elset name differs only in case from the
     *Element elset (Abaqus set names are case-insensitive).
  2. Section assignment written inside the *Assembly block, referencing an
     instance-scoped elset (flattened / Hypermesh / ANSA layout).
  3. Flat (no *Assembly) INP with a case-mismatched section elset.
"""
import os

import h5py

from src.inp import parse_inp
from src.inp.exporter import export_l1


def _export_and_read_material(tmp_path, inp_text):
    inp_path = os.path.join(str(tmp_path), "m.inp")
    with open(inp_path, "w") as fh:
        fh.write(inp_text)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    model = parse_inp(inp_path)
    export_l1(model, ws)

    geom_dir = os.path.join(ws, "l1", "geometry")
    fname = os.listdir(geom_dir)[0]
    with h5py.File(os.path.join(geom_dir, fname), "r") as f:
        grp = f["elements/C3D8R"]
        return set(grp["material_name"][:].tolist())


_ONE_HEX_BODY = """*Node
1, 0.0, 0.0, 0.0
2, 1.0, 0.0, 0.0
3, 1.0, 1.0, 0.0
4, 0.0, 1.0, 0.0
5, 0.0, 0.0, 1.0
6, 1.0, 0.0, 1.0
7, 1.0, 1.0, 1.0
8, 0.0, 1.0, 1.0
*Element, type=C3D8R, elset=AllElems
1, 1, 2, 3, 4, 5, 6, 7, 8
"""


def test_part_section_case_insensitive_elset(tmp_path):
    inp = (
        "*Part, name=PartA\n"
        + _ONE_HEX_BODY
        + "*Solid Section, elset=ALLELEMS, material=steel\n,\n"
        "*End Part\n"
        "*Assembly, name=Assembly\n"
        "*Instance, name=PartA-1, part=PartA\n"
        "*End Instance\n"
        "*End Assembly\n"
        "*Material, name=steel\n"
    )
    assert _export_and_read_material(tmp_path, inp) == {b"steel"}


def test_assembly_level_section_assignment(tmp_path):
    inp = (
        "*Part, name=PartA\n"
        + _ONE_HEX_BODY
        + "*End Part\n"
        "*Assembly, name=Assembly\n"
        "*Instance, name=PartA-1, part=PartA\n"
        "*End Instance\n"
        "*Elset, elset=SteelSet, instance=PartA-1\n1,\n"
        "*Solid Section, elset=SteelSet, material=steel\n,\n"
        "*End Assembly\n"
        "*Material, name=steel\n"
    )
    assert _export_and_read_material(tmp_path, inp) == {b"steel"}


def test_flat_inp_case_insensitive_elset(tmp_path):
    inp = (
        _ONE_HEX_BODY
        + "*Solid Section, elset=allelems, material=Aluminum\n,\n"
        "*Material, name=Aluminum\n"
    )
    assert _export_and_read_material(tmp_path, inp) == {b"Aluminum"}
