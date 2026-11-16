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
