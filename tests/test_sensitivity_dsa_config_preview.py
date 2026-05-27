from pathlib import Path

from services.model_update.analysis import sensitivity_service
from services.model_update.analysis.abaqusDSAInpGenerator import normalize_step_line_to_dsa


def test_normalize_step_line_to_dsa_removes_adjoint_sensitivity():
    assert normalize_step_line_to_dsa("*STEP,SENSITIVITY=ADJOINT") == "*STEP,DSA"
    assert normalize_step_line_to_dsa("*STEP, name=Step-1, SENSITIVITY=ADJOINT") == "*STEP,name=Step-1,DSA"


def test_build_project_dsa_config_preview_explicit_uses_thickness_parameters(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "id": 1,
                "parameter_name": "T1",
                "quantity_code": "T",
                "set_name": "OLD_SET",
                "element_label": None,
                "scalar_value": 2.5,
                "extra_json": '{"element_labels": [101, 102]}',
            },
            {
                "id": 2,
                "parameter_name": "E1",
                "quantity_code": "E",
                "set_name": "IGNORED",
                "element_label": 201,
                "scalar_value": 210000.0,
                "extra_json": None,
            },
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_design_responses",
        lambda project_id: [
            {
                "response_no": 1,
                "request_no": 1,
                "region_type": "NODE",
                "set_name": "RESP_NODES",
                "variables": ["U2"],
            }
        ],
    )
    monkeypatch.setattr(sensitivity_service, "_load_project_thickness_capabilities", lambda project_id: {})

    result = sensitivity_service.build_project_dsa_config_preview(project_id=1001, value_mode="explicit")

    assert result["parameter_count"] == 1
    assert result["response_count"] == 1
    assert result["config_json"]["element_sets"] == [
        {"set_name": "DSA_T1", "parameter": "T1", "elements": [101, 102], "value": 2.5}
    ]
    assert result["config_json"]["responses"] == [
        {"type": "node", "set": "RESP_NODES", "variables": ["U2"]}
    ]
    assert result["warnings"] == []


def test_build_project_dsa_config_preview_inherit_omits_value_and_warns(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "id": 1,
                "parameter_name": "T 1",
                "quantity_code": "T",
                "set_name": "OLD_SET",
                "element_label": 101,
                "scalar_value": None,
                "extra_json": "{}",
            }
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_design_responses",
        lambda project_id: [
            {
                "response_no": 1,
                "request_no": 1,
                "region_type": "ELEMENT",
                "set_name": "",
                "variables": [],
            }
        ],
    )
    monkeypatch.setattr(sensitivity_service, "_load_project_thickness_capabilities", lambda project_id: {})

    result = sensitivity_service.build_project_dsa_config_preview(project_id=1001, value_mode="inherit")

    element_set = result["config_json"]["element_sets"][0]
    assert element_set == {"set_name": "DSA_T_1", "parameter": "T 1", "elements": [101]}
    assert "value" not in element_set
    warning_codes = {item["code"] for item in result["warnings"]}
    assert "INHERIT_MODE_REQUIRES_UNIQUE_ORIGINAL_THICKNESS" in warning_codes
    assert "RESPONSE_SET_NAME_EMPTY" in warning_codes
    assert "RESPONSE_VARIABLES_EMPTY" in warning_codes


def test_build_project_dsa_config_preview_falls_back_to_capability_element_labels(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "id": 1,
                "parameter_name": "T1",
                "quantity_code": "T",
                "set_name": "SHELL1",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
                "element_label": None,
                "scalar_value": 1.2,
                "extra_json": "{}",
            }
        ],
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_thickness_capabilities",
        lambda project_id: {
            ("SHELL1", "ELSET", "PART", "", "P1"): {
                "set_name": "SHELL1",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
                "extra_json": '{"element_labels": [11, 12, 13]}',
            }
        },
    )
    monkeypatch.setattr(sensitivity_service, "_load_project_design_responses", lambda project_id: [])

    result = sensitivity_service.build_project_dsa_config_preview(project_id=1001, value_mode="explicit")

    assert result["config_json"]["element_sets"] == [
        {
            "set_name": "DSA_T1",
            "parameter": "T1",
            "elements": [11, 12, 13],
            "set_scope": "PART",
            "part_name": "P1",
            "value": 1.2,
        }
    ]
    assert "PARAMETER_ELEMENTS_EMPTY" not in {item["code"] for item in result["warnings"]}
    assert "PARAMETER_ELEMENTS_FROM_CAPABILITY" in {item["code"] for item in result["warnings"]}


def test_generate_project_dsa_inp_from_db_writes_include_and_main(monkeypatch, tmp_path: Path):
    source_inp = tmp_path / "model.inp"
    source_inp.write_text(
        "\n".join(
            [
                "*Heading",
                "*Part, name=P1",
                "*Node",
                "1, 0, 0, 0",
                "2, 1, 0, 0",
                "3, 1, 1, 0",
                "4, 0, 1, 0",
                "*Element, type=S4, elset=SHELL1",
                "1, 1, 2, 3, 4",
                "*Elset, elset=SHELL1",
                "1",
                "*Shell Section, elset=SHELL1, material=MAT1",
                "1.0",
                "*End Part",
                "*Assembly, name=Assembly",
                "*Instance, name=P1-1, part=P1",
                "*End Instance",
                "*Nset, nset=RESP_NODES, instance=P1-1",
                "1",
                "*End Assembly",
                "*Step, name=Step-1, SENSITIVITY=ADJOINT",
                "*Static",
                "1., 1.",
                "*End Step",
                "",
            ]
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    monkeypatch.setattr(sensitivity_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "id": 1,
                "parameter_name": "T1",
                "quantity_code": "T",
                "set_name": "SHELL1",
                "set_type": "ELSET",
                "set_scope": "PART",
                "instance_name": None,
                "part_name": "P1",
                "element_label": None,
                "scalar_value": 1.5,
                "extra_json": '{"element_labels": [1]}',
            }
        ],
    )
    monkeypatch.setattr(sensitivity_service, "_load_project_thickness_capabilities", lambda project_id: {})
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_design_responses",
        lambda project_id: [
            {
                "response_no": 1,
                "request_no": 1,
                "region_type": "NODE",
                "set_name": "RESP_NODES",
                "variables": ["U2"],
            }
        ],
    )

    result = sensitivity_service.generate_project_dsa_inp_from_db(
        project_id=1001,
        input_inp=str(source_inp),
        output_dir=str(out_dir),
        value_mode="explicit",
    )

    analysis_inp = Path(result["analysis_inp"])
    include_file = Path(result["include_file"])
    include_files = [Path(item) for item in result["include_files"]]
    config_file = Path(result["config_file"])
    part_include = out_dir / "include_part_P1.inp"

    assert analysis_inp.exists()
    assert include_file.exists()
    assert part_include.exists()
    assert config_file.exists()
    assert include_file in include_files
    assert part_include.resolve() in [item.resolve() for item in include_files]
    assert analysis_inp.name == "model_dsa.inp"
    assert "*Include, input=include.inp" in analysis_inp.read_text(encoding="utf-8")
    analysis_text = analysis_inp.read_text(encoding="utf-8")
    assert "*Include, input=include_part_P1.inp" in analysis_text
    assert "*STEP,name=Step-1,DSA" in analysis_text
    assert "SENSITIVITY=ADJOINT,DSA" not in analysis_text
    include_text = include_file.read_text(encoding="utf-8")
    assert "T1=1.5" in include_text
    assert "*DESIGN PARAMETER" in include_text
    assert "*SHELL SECTION" not in include_text.upper()
    part_include_text = part_include.read_text(encoding="utf-8")
    assert "*SHELL SECTION, ELSET=DSA_T1, MATERIAL=MAT1" in part_include_text
    assert "** DSA_AUTO_SCOPE_BEGIN PART:P1" in part_include_text


def test_generate_project_dsa_inp_from_db_resolves_project_paths_when_omitted(monkeypatch, tmp_path: Path):
    source_inp = tmp_path / "model.inp"
    source_inp.write_text("*Heading\n*Step, name=Step-1\n*Static\n1., 1.\n*End Step\n", encoding="utf-8")
    project_workspace = tmp_path / "project_ws"

    monkeypatch.setattr(sensitivity_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(
        sensitivity_service,
        "_resolve_inp_path_from_project",
        lambda project_id: str(source_inp),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "_project_sensitivity_output_dir",
        lambda project_id: str(project_workspace / "sensitivity"),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "build_project_dsa_config_preview",
        lambda project_id, value_mode="inherit": {
            "value_mode": value_mode,
            "config_json": {
                "include_file": "include.inp",
                "main_output": "model_dsa.inp",
                "element_sets": [],
                "node_sets": [],
                "responses": [],
            },
            "parameter_count": 0,
            "response_count": 0,
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        sensitivity_service,
        "analyze_element_set_tasks_scoped",
        lambda element_sets, parsed: ([], {}, []),
    )
    monkeypatch.setattr(
        sensitivity_service,
        "build_dsa_include_layout",
        lambda config, enriched_tasks, parsed, mother_set_to_remainder_name, include_file: {
            "global_include_name": include_file,
            "scope_include_refs": {},
            "assembly_include_name": None,
            "files": {include_file: "*DESIGN PARAMETER\n"},
        },
    )

    result = sensitivity_service.generate_project_dsa_inp_from_db(project_id=1001)

    assert result["input_inp"] == str(source_inp.resolve())
    assert Path(result["analysis_inp"]).parent == (project_workspace / "sensitivity").resolve()
    assert Path(result["include_file"]).exists()
    assert Path(result["config_file"]).exists()
    assert len(result["include_files"]) == 1


def test_generate_project_dsa_inp_from_db_keeps_flat_inp_in_single_global_include(monkeypatch, tmp_path: Path):
    source_inp = tmp_path / "flat_model.inp"
    source_inp.write_text(
        "\n".join(
            [
                "*Heading",
                "*Node",
                "1, 0, 0, 0",
                "2, 1, 0, 0",
                "3, 1, 1, 0",
                "4, 0, 1, 0",
                "*Element, type=S4, elset=SHELL1",
                "1, 1, 2, 3, 4",
                "*Elset, elset=SHELL1",
                "1",
                "*Shell Section, elset=SHELL1, material=MAT1",
                "1.0",
                "*Step, name=Step-1",
                "*Static",
                "1., 1.",
                "*End Step",
                "",
            ]
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    monkeypatch.setattr(sensitivity_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "id": 1,
                "parameter_name": "T1",
                "quantity_code": "T",
                "set_name": "SHELL1",
                "set_type": "ELSET",
                "set_scope": "ROOT",
                "instance_name": None,
                "part_name": None,
                "element_label": None,
                "scalar_value": 1.5,
                "extra_json": '{"element_labels": [1]}',
            }
        ],
    )
    monkeypatch.setattr(sensitivity_service, "_load_project_thickness_capabilities", lambda project_id: {})
    monkeypatch.setattr(sensitivity_service, "_load_project_design_responses", lambda project_id: [])

    result = sensitivity_service.generate_project_dsa_inp_from_db(
        project_id=1001,
        input_inp=str(source_inp),
        output_dir=str(out_dir),
        value_mode="explicit",
    )

    analysis_text = Path(result["analysis_inp"]).read_text(encoding="utf-8")
    include_text = Path(result["include_file"]).read_text(encoding="utf-8")
    assert "*Include, input=include.inp" in analysis_text
    assert "include_part_" not in analysis_text
    assert "*SHELL SECTION, ELSET=DSA_T1, MATERIAL=MAT1" in include_text
    assert len(result["include_files"]) == 1
