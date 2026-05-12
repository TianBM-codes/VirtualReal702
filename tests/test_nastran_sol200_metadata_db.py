from services.model_update.analysis import nastran_sol200_service


def test_generate_sol200_workflow_can_persist_metadata_to_db(monkeypatch):
    persisted = {}

    monkeypatch.setattr(
        nastran_sol200_service,
        "_resolve_phase1_input_and_parameters",
        lambda **kwargs: (
            "D:/demo/model_localized.bdf",
            [{"name": "E1", "type": "E", "material_id": 2001, "element_id": 1, "initial": 1.0, "lower": 0.8, "upper": 1.2}],
            {},
        ),
    )
    monkeypatch.setattr(
        nastran_sol200_service,
        "generate_nastran_sol200_job",
        lambda **kwargs: {
            "output_bdf": "D:/demo/model_sol200.bdf",
            "generated_files": {"metadata_json": "D:/demo/model_sol200.bdf.sol200.json"},
        },
    )

    from services.model_update.analysis import sensitivity_service

    monkeypatch.setattr(
        sensitivity_service,
        "persist_sensitivity_metadata",
        lambda **kwargs: persisted.update(kwargs) or {"analysis_run_id": 1},
    )

    result = nastran_sol200_service.generate_sol200_workflow(
        project_id=11,
        batch_no="4",
        case_name="sol200_case",
        input_bdf="D:/demo/model.bdf",
        responses=[{"name": "FREQ_MODE_1", "type": "FREQ", "mode_number": 1}],
    )

    assert persisted["project_id"] == 11
    assert persisted["batch_no"] == "4"
    assert persisted["case_name"] == "sol200_case"
    assert persisted["response_rows"][0]["mode_number"] == 1
    assert persisted["parameter_columns"][0]["element_id"] == 1
    assert persisted["source"]["metadata_path"] == "D:/demo/model_sol200.bdf.sol200.json"
    assert result["output_bdf"] == "D:/demo/model_sol200.bdf"
