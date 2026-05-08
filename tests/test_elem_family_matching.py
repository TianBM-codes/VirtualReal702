"""
Tests for Abaqus element family matching (suffix stripping).

Covers:
  - src/inp/model.py  map_element_type()
  - src/l2/ingest.py  _resolve_elem_code()

src/l1/abaqus_dump.py is not imported because it requires Abaqus's odbAccess
at import time.  Its _resolve_elem_code / _resolve_midnode_indices are
identical in logic to the L2 versions and are covered by the L2 tests.
"""
import pytest
from src.inp.model import map_element_type, ABAQUS_TO_FACTORY
from src.l2.ingest import _resolve_elem_code, ELEM_TYPE_CODE


# ---------------------------------------------------------------------------
# map_element_type — INP parser
# ---------------------------------------------------------------------------

class TestMapElementType:

    # --- exact matches still work ---
    def test_exact_common(self):
        assert map_element_type("S4R")    == "QUAD4"
        assert map_element_type("C3D8")   == "HEX8"
        assert map_element_type("C3D10")  == "TET10"
        assert map_element_type("C3D20R") == "HEX20"
        assert map_element_type("B31")    == "LINE2"
        assert map_element_type("T3D2")   == "LINE2"

    def test_exact_new_entries(self):
        assert map_element_type("STRI65") == "TRI6"
        assert map_element_type("S6")     == "TRI6"

    # --- single-char suffix stripping ---
    def test_strip_T_gives_quad4(self):
        assert map_element_type("S4T")   == "QUAD4"   # S4T → S4
        assert map_element_type("S4RT")  == "QUAD4"   # S4RT → S4R

    def test_strip_T_gives_hex8(self):
        assert map_element_type("C3D8T")  == "HEX8"   # C3D8T → C3D8
        assert map_element_type("C3D8RT") == "HEX8"   # C3D8RT → C3D8R

    def test_strip_gives_quad8(self):
        assert map_element_type("S8RT")  == "QUAD8"   # S8RT → S8R

    def test_strip_T_gives_tet10(self):
        assert map_element_type("C3D10T") == "TET10"  # C3D10T → C3D10

    # --- multi-char suffix stripping ---
    def test_strip_RH(self):
        # C3D8RHT (hypothetical heat-transfer hybrid) → C3D8RH → C3D8 → HEX8
        assert map_element_type("C3D8RH") == "HEX8"   # already in table
        assert map_element_type("C3D20RH") == "HEX20"  # already in table

    def test_strip_OS(self):
        assert map_element_type("B31OS") == "LINE2"   # already in table
        assert map_element_type("B32OS") == "LINE3"   # already in table

    # --- case insensitive ---
    def test_lowercase(self):
        assert map_element_type("s4r") == "QUAD4"
        assert map_element_type("c3d8") == "HEX8"

    # --- truly unknown types return None ---
    def test_unknown_returns_none(self):
        assert map_element_type("XYZZY")   is None
        assert map_element_type("FOO123")  is None

    def test_single_char_returns_none(self):
        assert map_element_type("R") is None
        assert map_element_type("T") is None

    def test_empty_string_returns_none(self):
        # Stripping can never produce an empty string; should return None.
        assert map_element_type("") is None

    # --- all existing table entries still resolve ---
    def test_all_table_entries_unchanged(self):
        for abaqus_type, expected in ABAQUS_TO_FACTORY.items():
            assert map_element_type(abaqus_type) == expected, \
                f"{abaqus_type} should still map to {expected}"


# ---------------------------------------------------------------------------
# _resolve_elem_code — L2
# ---------------------------------------------------------------------------

class TestResolveElemCode:

    # --- exact matches ---
    def test_exact_common(self):
        assert _resolve_elem_code("S4R")   == ELEM_TYPE_CODE["S4R"]
        assert _resolve_elem_code("C3D8")  == ELEM_TYPE_CODE["C3D8"]
        assert _resolve_elem_code("C3D10") == ELEM_TYPE_CODE["C3D10"]

    def test_stri65(self):
        assert _resolve_elem_code("STRI65") == 8

    def test_line_elements(self):
        assert _resolve_elem_code("T3D2")  == 9
        assert _resolve_elem_code("B31")   == 9
        assert _resolve_elem_code("T3D3")  == 10
        assert _resolve_elem_code("B32")   == 10

    # --- suffix stripping ---
    def test_S4T(self):
        assert _resolve_elem_code("S4T") == ELEM_TYPE_CODE["S4"]

    def test_S8RT(self):
        assert _resolve_elem_code("S8RT") == ELEM_TYPE_CODE["S8R"]

    def test_C3D8T(self):
        assert _resolve_elem_code("C3D8T") == ELEM_TYPE_CODE["C3D8"]

    def test_C3D8RT(self):
        assert _resolve_elem_code("C3D8RT") == ELEM_TYPE_CODE["C3D8R"]

    # --- unknown returns -1 (sentinel used by collect_faces) ---
    def test_unknown_returns_minus1(self):
        assert _resolve_elem_code("XYZZY")  == -1
        assert _resolve_elem_code("FOO123") == -1

    def test_single_char_returns_minus1(self):
        assert _resolve_elem_code("R") == -1

    # --- all existing table entries still resolve ---
    def test_all_table_entries_unchanged(self):
        for etype, expected in ELEM_TYPE_CODE.items():
            assert _resolve_elem_code(etype) == expected, \
                f"{etype} should still resolve to code {expected}"
