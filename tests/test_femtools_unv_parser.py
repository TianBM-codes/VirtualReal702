from FemToolsUNVParser import parse_unv


def test_parse_unv_dataset_55_analysis_type_1_static_displacement(tmp_path):
    unv_path = tmp_path / "static_disp.unv"
    unv_path.write_text(
        "\n".join(
            [
                "-1",
                "15",
                "1001 0 0 0 1.0 2.0 3.0",
                "-1",
                "-1",
                "55",
                "NONE",
                "NONE",
                "NONE",
                "NONE",
                "NONE",
                "1 1 1 0 2 3",
                "0 0 7 1 0 0 0 0",
                "2.5 0 0 0 0 0",
                "1001",
                "0.1 0.2 0.3",
                "-1",
            ]
        ),
        encoding="utf-8",
    )

    nodes, nodes_dict, trace_lines, modes, elements, message = parse_unv(str(unv_path))

    assert len(nodes) == 1
    assert nodes_dict == {1001: [1.0, 2.0, 3.0]}
    assert trace_lines == []
    assert elements == []
    assert message["is_real"] is True
    assert len(modes) == 1

    mode = modes[0]
    assert mode["analysis_type"] == 1
    assert mode["load_case"] == 7
    assert mode["modal_number"] == 1
    assert mode["frequency"] == 0.0
    assert mode["load_factor"] == 2.5
    assert mode["damping"] == 0.0
    assert mode["displacements"][1001]["real"] == (0.1, 0.2, 0.3)
    assert mode["displacements"][1001]["imag"] == (0, 0, 0)


def test_parse_unv_dataset_55_static_displacement_with_rotation(tmp_path):
    unv_path = tmp_path / "static_disp_with_rotation.unv"
    unv_path.write_text(
        "\n".join(
            [
                "-1",
                "15",
                "1001 0 0 0 1.0 2.0 3.0",
                "-1",
                "-1",
                "55",
                "NONE",
                "NONE",
                "NONE",
                "NONE",
                "NONE",
                "1 1 1 0 2 6",
                "0 0 7 1 0 0 0 0",
                "2.5 0 0 0 0 0",
                "1001",
                "0.1 0.2 0.3 0.01 0.02 0.03",
                "-1",
            ]
        ),
        encoding="utf-8",
    )

    _, _, _, modes, _, message = parse_unv(str(unv_path))

    assert message["is_static"] is True
    assert message["is_real"] is True
    assert len(modes) == 1
    assert modes[0]["displacements"][1001]["rotate"] == (0.01, 0.02, 0.03)
