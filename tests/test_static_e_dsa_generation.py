import pytest

from services.model_update.analysis import sensitivity_service as service
from services.model_update.analysis.abaqusDSAInpGenerator import (
    analyze_element_set_tasks_scoped,
    build_dsa_include_layout,
    parse_main_inp,
)


def test_selected_e_changes_material_and_preserves_shell_thickness():
    lines = ["*Part, name=P", "*Element, type=S4", "1,1,2,3,4",
             "*Elset, elset=S", "1", "*Shell Section, elset=S, material=M",
             "0.001", "*End Part", "*Material, name=M", "*Density", "1890",
             "*Elastic", "2.1e11,0.3", "*Step, name=Static", "*Static", "*End Step"]
    config = {"element_sets": [{"set_name": "DSA_E", "parameter": "E@S", "elements": [1],
                               "set_scope": "PART", "part_name": "P", "value": 2.1e11}],
              "node_sets": [], "responses": []}
    parsed = parse_main_inp(lines)
    tasks, remainders, _ = analyze_element_set_tasks_scoped(config["element_sets"], parsed)
    tasks[0]["quantity_code"] = "E"
    layout = build_dsa_include_layout(config, tasks, parsed, remainders, include_file="include.inp")
    part_text = layout["files"]["include_part_P.inp"]
    assert "MATERIAL=DSA_E_S_M\n0.001" in part_text
    assert "<E_S>" not in part_text
    material_text = "\n".join(service._build_dsa_elastic_materials(lines, tasks))
    assert "*MATERIAL, NAME=DSA_E_S_M" in material_text
    assert "<E_S>,0.3" in material_text
    assert "*Density\n1890" in material_text
    assert lines[12] == "2.1e11,0.3"


def test_e_selection_is_not_replaced_by_thickness(monkeypatch):
    row = {"quantity_code": "E", "parameter_name": "E@S"}
    monkeypatch.setattr(service, "_load_project_optimization_parameters", lambda *a, **k: [row])
    assert service._load_project_thickness_parameters(1) == [row]


def test_e_rejects_temperature_dependent_material():
    lines = ["*Material, name=M", "*Elastic", "2.1e11,0.3,20", "2.0e11,0.3,100"]
    tasks = [{"parameter": "E@S", "quantity_code": "E", "source_groups": [{"material": "M"}]}]
    with pytest.raises(ValueError, match="single constant"):
        service._build_dsa_elastic_materials(lines, tasks)


def test_flat_inp_resolves_catalog_part_parameters_in_root():
    lines = [
        "*Element, type=S4", "1,1,2,3,4", "2,2,3,4,5",
        "** Flat assembly", "*Elset, elset=S", "1,2",
        "*Shell Section, elset=S, material=M, controls=EC-1", "4.4, 7",
    ]
    config = {
        "element_sets": [
            {"set_name": "DSA_E", "parameter": "E@S1", "elements": [1],
             "set_scope": "PART", "part_name": "PART-1-1", "value": 210000},
            {"set_name": "DSA_T", "parameter": "T@S2", "elements": [2],
             "set_scope": "PART", "part_name": "PART-1-1", "value": 4.4},
        ],
        "node_sets": [],
        "responses": [{"type": "node", "set": "RESP", "nodes": [1], "instance_name": "PART-1-1"}],
    }
    parsed = parse_main_inp(lines)
    tasks, remainders, _ = analyze_element_set_tasks_scoped(config["element_sets"], parsed)
    tasks[0]["quantity_code"] = "E"
    tasks[1]["quantity_code"] = "T"
    layout = build_dsa_include_layout(config, tasks, parsed, remainders, include_file="include.inp")

    assert layout["scope_include_refs"] == {}
    root_text = layout["files"]["include.inp"]
    assert "MATERIAL=DSA_E_S1_M, controls=EC-1\n4.4, 7" in root_text
    assert "MATERIAL=M, controls=EC-1\n<T_S2>, 7" in root_text
    assert "*NSET, NSET=RESP\n1" in root_text



def test_generated_fields_map_to_parameters_by_name():
    rows = [
        {"parameter_name": "E@PART-1-1_P30006"},
        {"parameter_name": "T@PART-1-1_P30002"},
    ]
    fields = ["d_U_T_PART_1_1_P30002", "d_U_E_PART_1_1_P30006"]

    assert service._build_dsa_parameter_row_map(rows, "d_U_", fields) == {
        fields[0]: rows[1],
        fields[1]: rows[0],
    }


def test_overlapping_elastic_and_thickness_parameters_share_one_section():
    lines = ["*Element, type=S4", "1,1,2,3,4", "2,2,3,4,5",
             "*Elset, elset=S", "1,2", "*Shell Section, elset=S, material=M", "0.2, 5"]
    config = {"element_sets": [
        {"set_name": "DSA_E", "parameter": "E@S", "elements": [1, 2], "value": 210000},
        {"set_name": "DSA_T", "parameter": "T@S", "elements": [2], "value": 0.2},
    ], "node_sets": [], "responses": []}
    parsed = parse_main_inp(lines)
    tasks, remainders, _ = analyze_element_set_tasks_scoped(config["element_sets"], parsed)
    tasks[0]["quantity_code"] = "E"
    tasks[1]["quantity_code"] = "T"
    layout = build_dsa_include_layout(config, tasks, parsed, remainders, include_file="include.inp")
    text = layout["files"]["include.inp"]
    sections = [i for i, line in enumerate(text.splitlines()) if line.lower().startswith("*shell section")]
    assert len(sections) == 2
    assert text.count("MATERIAL=DSA_E_S_M") == 2
    assert "<T_S>, 5" in text
    assert "0.2, 5" in text


def test_static_dsa_extracts_only_response_and_derivatives_at_last_frame():
    options = service._build_project_result_parse_options(step="LC1", frame=None, field_prefix="d_U_")
    assert options["frames"] == "last"
    assert options["fields"] == ["U", "d_U_*"]
    assert options["steps"] == ["LC1"]
    assert "field_prefix" not in options
    assert service._build_project_result_parse_options(
        step="LC1", frame=7, field_prefix="d_U_")["frames"] == [7]



def test_dsa_response_targets_selected_step_in_multi_step_model():
    from services.model_update.analysis.abaqusDSAInpGenerator import patch_static_step_to_dsa
    lines = ["*STEP, NAME=LC1", "*STATIC", "*END STEP",
             "*STEP, NAME=LC2", "*STATIC", "*END STEP"]
    result = patch_static_step_to_dsa(lines, {"responses": [
        {"type": "node", "set": "N", "variables": ["U"], "step_name": "LC1"}]})
    text = "\n".join(result)
    assert "*STEP,NAME=LC1,DSA" in text
    assert "*STEP, NAME=LC2" in text
    assert text.index("*DESIGN RESPONSE") < text.index("*STEP, NAME=LC2")


def test_solid_elastic_parameter_preserves_solid_section_data():
    lines = ["*Element, type=C3D8", "1,1,2,3,4,5,6,7,8",
             "*Elset, elset=S", "1", "*Solid Section, elset=S, material=M", ","]
    config = {"element_sets": [
        {"set_name": "DSA_E", "parameter": "E@S", "elements": [1],
         "quantity_code": "E", "value": 34500},
    ], "node_sets": [], "responses": []}
    parsed = parse_main_inp(lines)
    tasks, remainders, _ = analyze_element_set_tasks_scoped(config["element_sets"], parsed)
    tasks[0]["quantity_code"] = "E"
    layout = build_dsa_include_layout(config, tasks, parsed, remainders, include_file="include.inp")
    assert "*Solid Section, ELSET=DSA_E, MATERIAL=DSA_E_S_M\n," in layout["files"]["include.inp"]
