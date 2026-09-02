from src.l1.bdf_read import read_bdf_safe


def test_read_bdf_safe_reads_valid_prod(tmp_path):
    bdf_path = tmp_path / "valid_prod.bdf"
    bdf_path.write_text(
        "\n".join(
            [
                "SOL 103",
                "CEND",
                "BEGIN BULK",
                "GRID,1,,0.,0.,0.",
                "GRID,2,,1.,0.,0.",
                "MAT1,1,210000.,,0.3",
                "PROD,10,1,2.5",
                "CROD,20,10,1,2",
                "ENDDATA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    model = read_bdf_safe(str(bdf_path), xref=False)

    assert 10 in model.properties
    assert 20 in model.elements
    assert getattr(model, "read_bdf_safe_warnings", []) == []


def test_read_bdf_safe_skips_invalid_prod_and_dependent_crod(tmp_path):
    bdf_path = tmp_path / "invalid_prod.bdf"
    bdf_path.write_text(
        "\n".join(
            [
                "SOL 103",
                "CEND",
                "BEGIN BULK",
                "GRID,1,,0.,0.,0.",
                "GRID,2,,1.,0.,0.",
                "GRID,3,,2.,0.,0.",
                "MAT1,1,210000.,,0.3",
                "PROD,10,1",
                "CROD,20,10,1,2",
                "PROD,11,1,3.5",
                "CROD,21,11,2,3",
                "ENDDATA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    model = read_bdf_safe(str(bdf_path), xref=False)

    assert 10 not in model.properties
    assert 20 not in model.elements
    assert 11 in model.properties
    assert 21 in model.elements
    assert len(model.read_bdf_safe_warnings) == 1
    assert "PID(s) 10" in model.read_bdf_safe_warnings[0]


def test_read_bdf_safe_renumbers_duplicate_property_ids_by_type(tmp_path):
    bdf_path = tmp_path / "duplicate_properties.bdf"
    bdf_path.write_text(
        "\n".join(
            [
                "SOL 103",
                "CEND",
                "BEGIN BULK",
                "GRID,1,,0.,0.,0.",
                "GRID,2,,1.,0.,0.",
                "GRID,3,,1.,1.,0.",
                "GRID,4,,0.,1.,0.",
                "MAT1,1,210000.,,0.3",
                "PSHELL,7,1,0.1",
                "CQUAD4,100,7,1,2,3,4",
                "PBAR,7,1,1.0",
                "CBAR,200,7,1,2,0.,1.,0.",
                "ENDDATA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    model = read_bdf_safe(str(bdf_path), xref=False)

    assert 7 in model.properties
    assert model.properties[7].type == "PSHELL"
    new_pids = [pid for pid, prop in model.properties.items() if prop.type == "PBAR"]
    assert len(new_pids) == 1
    assert new_pids[0] != 7
    assert model.elements[100].pid == 7
    assert model.elements[200].pid == new_pids[0]
    assert "renumbered duplicate property PID(s) 7" in model.read_bdf_safe_warnings[0]
