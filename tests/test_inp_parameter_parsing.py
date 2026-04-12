from pathlib import Path

from src.inp import parse_inp


def test_parse_parameter_design_parameter_and_design_response(tmp_path: Path):
    inp_path = tmp_path / "parameterized.inp"
    inp_path.write_text(
        "\n".join(
            [
                "*Heading",
                "*Parameter",
                "T1 = 1.25",
                "*Part, name=P1",
                "*Node",
                "1, 0, 0, 0",
                "2, 1, 0, 0",
                "3, 1, 1, 0",
                "4, 0, 1, 0",
                "*Element, type=S4, elset=EALL",
                "1, 1, 2, 3, 4",
                "*Elset, elset=SET_SHELL",
                "1",
                "*Shell Section, elset=SET_SHELL, material=MAT1",
                "<T1>",
                "*End Part",
                "*Assembly, name=Assembly",
                "*Instance, name=INST_A, part=P1",
                "*End Instance",
                "*End Assembly",
                "*Design Parameter",
                "T1",
                "*Step, name=Step-1, DSA=YES",
                "*Static",
                "*Design Response, Frequency=3",
                "*Element Response, ELSET=SET_SHELL",
                "S,",
                "*End Step",
                "",
            ]
        ),
        encoding="utf-8",
    )

    model = parse_inp(str(inp_path))

    assert model.parameters["T1"].expression == "1.25"
    assert model.parameters["T1"].scalar_value == 1.25
    assert [item.name for item in model.design_parameters] == ["T1"]

    section = model.parts["P1"].sections[0]
    assert section.thickness_parameter == "T1"
    assert section.thickness_expression == "<T1>"
    assert section.thickness == 1.25

    assert len(model.design_responses) == 1
    design_response = model.design_responses[0]
    assert design_response.step_name == "Step-1"
    assert design_response.frequency == 3
    assert len(design_response.requests) == 1
    assert design_response.requests[0].region_type == "ELEMENT"
    assert design_response.requests[0].set_name == "SET_SHELL"
    assert design_response.requests[0].variables == ["S"]
