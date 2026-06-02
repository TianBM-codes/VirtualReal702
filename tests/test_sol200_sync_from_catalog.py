from services.model_update.analysis import nastran_sol200_service


def test_sync_sol200_config_from_catalog_maps_selected_parameters_and_modal_frequency_responses(monkeypatch):
    created_parameters = []
    created_responses = []

    monkeypatch.setattr(nastran_sol200_service, "ensure_tables_exist", lambda: None)
    monkeypatch.setattr(nastran_sol200_service, "clear_sol200_parameter_config_entries", lambda project_id: None)
    monkeypatch.setattr(nastran_sol200_service, "clear_sol200_response_config_entries", lambda project_id: None)
    monkeypatch.setattr(
        nastran_sol200_service,
        "create_sol200_parameter_config_entry",
        lambda **kwargs: created_parameters.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        nastran_sol200_service,
        "create_sol200_response_config_entry",
        lambda **kwargs: created_responses.append(kwargs) or kwargs,
    )

    from services.model_update.analysis import inp_service

    monkeypatch.setattr(
        inp_service,
        "list_optimization_parameters",
        lambda project_id: {
            "parameters": [
                {
                    "parameter_name": "E@MAT1_1001",
                    "quantity_code": "E",
                    "set_name": "MAT1_1001",
                    "current_value": 2.1e11,
                    "lower": 1.9e11,
                    "upper": 2.3e11,
                    "usage_scope": ["SENSITIVITY", "UPDATE"],
                    "extra_json": {},
                }
            ]
        },
    )
    monkeypatch.setattr(
        inp_service,
        "get_fe_response_catalog",
        lambda project_id: {
            "responses": [
                {
                    "response_name": "FREQ_MODE_1",
                    "response_type": "MODAL_FREQUENCY",
                    "enabled": True,
                    "solver_scope": ["SOL200", "BAYESIAN"],
                    "test_mode_no": 1,
                    "extra_json": {
                        "mode_number": 1,
                        "fem_mode_no": 1,
                        "test_mode_no": 1,
                        "mac": 98.0,
                    },
                }
            ]
        },
    )

    result = nastran_sol200_service.sync_sol200_config_from_catalog(
        project_id=3,
        overwrite=True,
    )

    assert result["parameter_count"] == 1
    assert result["response_count"] == 1
    assert created_parameters[0]["parameter_name"] == "E@MAT1_1001"
    assert created_parameters[0]["parameter_type"] == "E"
    assert created_parameters[0]["material_id"] == 1001
    assert created_responses[0]["response_name"] == "FREQ_MODE_1"
    assert created_responses[0]["response_type"] == "FREQ"
    assert created_responses[0]["mode_number"] == 1
