"""Material values must survive both fast scanning and HDF5 serialization."""
import h5py
import numpy as np
import pytest

from src.l1.bdf_pack import _scan_fast_bdf, _write_fast_bdf_pack


def _fixed(name, values, width):
    return name.ljust(8) + "".join(str(value).rjust(width) for value in values)


def _shell_deck(material_lines):
    return [
        "SOL 103", "CEND", "BEGIN BULK",
        "GRID,1,,0.,0.,0.", "GRID,2,,1.,0.,0.",
        "GRID,3,,1.,1.,0.", "GRID,4,,0.,1.,0.",
        "PSHELL,1,1,1.", "CQUAD4,1,1,1,2,3,4",
        *material_lines, "ENDDATA",
    ]


def _pack_deck(tmp_path, material_lines):
    source = tmp_path / "shell.bdf"
    source.write_text("\n".join(_shell_deck(material_lines)) + "\n", encoding="ascii")
    parsed, fallback = _scan_fast_bdf(str(source))
    assert fallback is None
    workspace = tmp_path / "workspace"
    _write_fast_bdf_pack(parsed, "SHELL", str(workspace), source_bdf_path=str(source))
    return workspace / "l1/geometry/SHELL.h5"


@pytest.mark.parametrize("material", [
    "MAT1,1,2.1+11,8.07692+10,.3",
    _fixed("MAT1", [1, "2.1+11", "", ".3"], 8),
    # The exact MAT1* line that lost E/NU in the 200w model.
    "MAT1*    1              2.1+11          8.07692+10      .3",
    _fixed("MAT1*", [1, "2.1D+11", "8.07692D+10", ".3"], 16),
    _fixed("MAT1*", [1, "2.1+11", "", ".3"], 16),
])
def test_fast_pack_preserves_mat1_elastic_values(tmp_path, material):
    geometry = _pack_deck(tmp_path, [material])
    with h5py.File(geometry, "r") as h5:
        np.testing.assert_array_equal(h5["materials/1/elastic_table"][:], [[2.1e11, 0.3]])
        np.testing.assert_array_equal(h5["elements/S4R/material_name"][:], [b"1"])
        assert h5["nodes/coords"].shape == (4, 3)


def test_fast_pack_keeps_long_mat1_continuation_out_of_elastic_values(tmp_path):
    first = _fixed("MAT1*", [1, "2.1+11", "8.07692+10", ".3"], 16) + "*NEXT"
    continuation = _fixed("*NEXT", ["7.8-9", "1.2-5", "293.", ""], 16)
    geometry = _pack_deck(tmp_path, [
        first, "$ comment between continuation lines", continuation,
        "MAT1,2,7.+10,,.33",
    ])
    with h5py.File(geometry, "r") as h5:
        assert set(h5["materials"]) == {"1", "2"}
        np.testing.assert_array_equal(h5["materials/1/elastic_table"][:], [[2.1e11, 0.3]])
        np.testing.assert_array_equal(h5["materials/2/elastic_table"][:], [[7.0e10, 0.33]])


@pytest.mark.parametrize("geometry_card", [
    _fixed("GRID*", [1, "", "0.", "0."], 16),
    _fixed("PSHELL*", [1, 1, "1.", 1], 16),
])
def test_fast_scan_still_falls_back_for_unsupported_long_geometry(tmp_path, geometry_card):
    source = tmp_path / "long_geometry.bdf"
    source.write_text("\n".join([geometry_card, *_shell_deck([])]), encoding="ascii")
    parsed, fallback = _scan_fast_bdf(str(source))
    assert parsed is None
    assert "long-field" in fallback
