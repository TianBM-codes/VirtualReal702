from services.model_update.analysis import sensitivity_service


def test_build_project_dsa_config_preview_explicit_uses_thickness_parameters(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_project_optimization_parameters",
        lambda project_id: [
            {
                "id": 1,
                "parameter_name": "T1",
                "quantity_code": "H",
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
                "quantity_code": "H",
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
                "quantity_code": "H",
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
        {"set_name": "DSA_T1", "parameter": "T1", "elements": [11, 12, 13], "value": 1.2}
    ]
    assert "PARAMETER_ELEMENTS_EMPTY" not in {item["code"] for item in result["warnings"]}
    assert "PARAMETER_ELEMENTS_FROM_CAPABILITY" in {item["code"] for item in result["warnings"]}
