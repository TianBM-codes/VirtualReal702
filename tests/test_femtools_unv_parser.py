from FemToolsUNVParser import parse_unv
from services.model_update.importers.unv_frf_service import list_dataset_ids


def test_parse_unv_dataset_2411_nodes(tmp_path):
    unv_path = tmp_path / "nodes_2411.unv"
    unv_path.write_text(
        "\n".join(
            [
                "-1",
                "2411",
                "1001 0 0 0",
                "1.0 2.0 3.0",
                "-1",
            ]
        ),
        encoding="utf-8",
    )

    nodes, nodes_dict, trace_lines, modes, elements, message = parse_unv(str(unv_path))

    assert len(nodes) == 1
    assert nodes[0].id == 1001
    assert nodes_dict == {1001: [1.0, 2.0, 3.0]}
    assert trace_lines == []
    assert modes == []
    assert elements == []
    assert message["coordinate_system_count"] == 0
    assert message["unsupported_coordinate_systems"] == []


def test_parse_unv_dataset_2420_cartesian_cs_with_2411_nodes(tmp_path):
    unv_path = tmp_path / "nodes_2411_with_cs.unv"
    unv_path.write_text(
        "\n".join(
            [
                "-1",
                "2420",
                "PART_NAME",
                "PART_DESC",
                "10 0 1",
                "LOCAL_CARTESIAN",
                "1.0 2.0 3.0",
                "1.0 0.0 0.0",
                "0.0 1.0 0.0",
                "0.0 0.0 1.0",
                "-1",
                "-1",
                "2411",
                "1001 10 0 0",
                "1.0 2.0 3.0",
                "-1",
            ]
        ),
        encoding="utf-8",
    )

    nodes, nodes_dict, trace_lines, modes, elements, message = parse_unv(str(unv_path))

    assert len(nodes) == 1
    assert nodes[0].id == 1001
    assert nodes_dict == {1001: [2.0, 4.0, 6.0]}
    assert trace_lines == []
    assert modes == []
    assert elements == []
    assert message["coordinate_system_count"] == 1
    assert message["unsupported_coordinate_systems"] == []


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


def test_parse_unv_collects_dataset_151_and_164_metadata(tmp_path):
    unv_path = tmp_path / "meta.unv"
    unv_path.write_text(
        "\n".join(
            [
                "-1",
                "151",
                "MODEL HEADER",
                "1 2 3 4",
                "-1",
                "-1",
                "164",
                "UNITS HEADER",
                "9 8 7 6",
                "-1",
            ]
        ),
        encoding="utf-8",
    )

    _, _, _, _, _, message = parse_unv(str(unv_path))

    assert message["dataset_151_count"] == 1
    assert message["dataset_164_count"] == 1
    assert message["dataset_151"][0]["title"] == "MODEL HEADER"
    assert message["dataset_164"][0]["title"] == "UNITS HEADER"


def test_parse_unv_handles_gb18030_chinese_metadata(tmp_path):
    unv_path = tmp_path / "chinese.unv"
    content = "\n".join(
        [
            "-1",
            "151",
            "模型名称",
            "1 2 3 4",
            "-1",
            "-1",
            "15",
            "1001 0 0 0 1.0 2.0 3.0",
            "-1",
        ]
    )
    unv_path.write_bytes(content.encode("gb18030"))

    nodes, nodes_dict, _, _, _, message = parse_unv(str(unv_path))

    assert len(nodes) == 1
    assert nodes_dict[1001] == [1.0, 2.0, 3.0]
    assert message["dataset_151"][0]["title"] == "模型名称"
    assert list_dataset_ids(str(unv_path)) == ["151", "15"]
