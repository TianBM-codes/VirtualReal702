from pathlib import Path

from services.model_update.analysis import inp_service


def test_get_project_abaqus_instances_and_steps_and_validate_node(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "meta_model.inp"
    inp_path.write_text(
        "\n".join(
            [
                "*Heading",
                "*Part, name=PART-1",
                "*Node",
                "1, 0, 0, 0",
                "4, 1, 0, 0",
                "*Element, type=T3D2, elset=EALL",
                "1, 1, 4",
                "*End Part",
                "*Assembly, name=Assembly",
                "*Instance, name=PART-1-1, part=PART-1",
                "*End Instance",
                "*End Assembly",
                "*Step, name=Step-1",
                "*Static",
                "1., 1.",
                "*End Step",
                "*Step, name=Step-2",
                "*Static",
                "1., 1.",
                "*End Step",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(inp_service._catalog, "resolve_project_source_inp_path", lambda project_id: str(inp_path))

    meta = inp_service.get_project_abaqus_instances_and_steps(5)

    assert meta["project_id"] == 5
    assert meta["instance_count"] == 1
    assert meta["step_count"] == 2
    assert meta["instances"] == [
        {"instance_name": "PART-1-1", "part_name": "PART-1", "set_scope": "ASSEMBLY"}
    ]
    assert [item["step_name"] for item in meta["steps"]] == ["Step-1", "Step-2"]

    contains = inp_service.validate_abaqus_instance_node_label(5, "PART-1-1", 4)
    missing = inp_service.validate_abaqus_instance_node_label(5, "PART-1-1", 99)

    assert contains["exists"] is True
    assert contains["part_name"] == "PART-1"
    assert contains["set_scope"] == "ASSEMBLY"
    assert missing["exists"] is False
