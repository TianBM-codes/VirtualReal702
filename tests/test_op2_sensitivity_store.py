from services.model_update.importers import op2_service


def test_store_op2_sensitivity_passes_metadata_rows_to_persistence(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        op2_service,
        "preview_op2_sensitivity",
        lambda **kwargs: {
            "source": {
                "source_kind": "op2",
                "op2_path": "D:/demo/model.op2",
                "bdf_path": "D:/demo/model.bdf",
                "metadata_path": "D:/demo/model.bdf.sol200.json",
            },
            "response_rows": [{"response_name": "FREQ_MODE_1", "response_type": "FREQ", "mode_number": 1}],
            "parameter_columns": [{"parameter_name": "E1", "type": "E", "element_id": 1, "material_id": 2001}],
            "matrix_preview": [[0.25]],
            "warnings": [],
        },
    )

    from services.model_update.analysis import sensitivity_service

    def fake_persist(**kwargs):
        captured["payload"] = kwargs["matrix_payload"]
        return {
            "analysis_run_id": 1,
            "project_id": kwargs["project_id"],
            "batch_no": kwargs["batch_no"],
            "case_name": kwargs["case_name"],
            "response_names": ["FREQ_MODE_1"],
            "parameter_names": ["E1"],
            "response_count": 1,
            "parameter_count": 1,
            "point_count": 1,
        }

    monkeypatch.setattr(sensitivity_service, "_persist_sensitivity_matrix", fake_persist)

    op2_service.store_op2_sensitivity(
        project_id=5,
        batch_no="2",
        case_name="sol200_case",
        op2_path="D:/demo/model.op2",
    )

    assert captured["payload"]["source"]["metadata_path"] == "D:/demo/model.bdf.sol200.json"
    assert captured["payload"]["response_rows"][0]["mode_number"] == 1
    assert captured["payload"]["parameter_columns"][0]["element_id"] == 1


def test_preview_op2_sensitivity_can_fallback_to_db_metadata(monkeypatch):
    captured = {}

    monkeypatch.setattr(op2_service, "_resolve_sensitivity_result_source", lambda **kwargs: ("D:/demo/model.op2", "op2"))
    monkeypatch.setattr(op2_service, "_abs_file", lambda path, field_name: path)
    monkeypatch.setattr(op2_service, "_resolve_sidecar_metadata_info", lambda metadata_json, bdf_path: ({}, None))
    monkeypatch.setattr(op2_service, "_read_op2", lambda path: object())
    monkeypatch.setattr(op2_service, "_collect_matrix_candidates", lambda op2: [{"shape": (1, 1), "matrix": [[0.5]]}])

    def fake_choose(candidates, response_names, parameter_names):
        captured["response_names"] = list(response_names)
        captured["parameter_names"] = list(parameter_names)
        return {"path": "op2", "matrix": [[0.5]], "row_labels": None, "column_labels": None}

    monkeypatch.setattr(op2_service, "_choose_matrix_candidate", fake_choose)

    from services.model_update.analysis import sensitivity_service

    monkeypatch.setattr(
        sensitivity_service,
        "_load_stored_sensitivity_run",
        lambda **kwargs: {
            "response_rows": [{"response_name": "FREQ_MODE_1", "response_type": "FREQ", "mode_number": 1}],
            "parameter_columns": [{"param_name": "E1", "param_type": "E", "element_id": 1}],
        },
    )

    result = op2_service.preview_op2_sensitivity(
        project_id=9,
        batch_no="3",
        op2_path="D:/demo/model.op2",
    )

    assert captured["response_names"] == ["FREQ_MODE_1"]
    assert captured["parameter_names"] == ["E1"]
    assert result["response_rows"][0]["mode_number"] == 1
    assert result["parameter_columns"][0]["element_id"] == 1


def test_parse_formatted_sensitivity_csv_can_match_response_by_mode_number(tmp_path):
    csv_path = tmp_path / "sol200_sens.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Local Sensitivity Results File",
                "",
                "DV ID,Label,Current Value,Lower Limit,Upper Limit",
                "1,E_MAT1,9.0000E+04,2.0000E+03,2.0000E+05",
                "",
                "Design Response ID,   Label,Response Type,Elem/Grid ID,Component ID,Superelement ID,Subcase ID,Response value,Freq/Time",
                "1,FREQ_MOD,EIGN,1,,0,1,3.2198E+02,0.0",
                "E_MAT1",
                "1.5888E-03",
                "",
            ])
        + "\n",
        encoding="utf-8",
    )

    result = op2_service._parse_formatted_sensitivity_csv(
        str(csv_path),
        parameter_names=["E_MAT1"],
        response_names=["FREQ_MODE_1"],
        response_rows=[{"response_name": "FREQ_MODE_1", "mode_number": 1}],
    )

    assert result is not None
    assert result["row_labels"] == ["FREQ_MOD"]
    assert result["column_labels"] == ["E_MAT1"]
    assert result["matrix"].shape == (1, 1)


def test_parse_formatted_sensitivity_csv_can_match_modal_displacement_response(tmp_path):
    csv_path = tmp_path / "sol200_sens.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Local Sensitivity Results File",
                "",
                "DV ID,Label,Current Value,Lower Limit,Upper Limit",
                "1,E_MAT1,9.0000E+04,2.0000E+03,2.0000E+05",
                "",
                "Design Response ID,   Label,Response Type,Elem/Grid ID,Component ID,Superelement ID,Subcase ID,Response value,Freq/Time",
                "2,MODE1_NODE3_U3,DISP,3,3,0,1,6.3810E-01,0.0",
                "E_MAT1",
                "2.5000E-04",
                "",
            ])
        + "\n",
        encoding="utf-8",
    )

    result = op2_service._parse_formatted_sensitivity_csv(
        str(csv_path),
        parameter_names=["E_MAT1"],
        response_names=["MODE1_NODE3_U3"],
        response_rows=[{
            "response_name": "MODE1_NODE3_U3",
            "response_type": "DISP",
            "mode_number": 1,
            "node_id": 3,
            "component": "U3",
        }],
    )

    assert result is not None
    assert result["row_labels"] == ["MODE1_NODE3_U3"]
    assert result["response_types"] == ["DISP"]
    assert result["matrix"].shape == (1, 1)
    assert result["matrix"][0, 0] == 2.5e-4


def test_parse_formatted_sensitivity_csv_can_fallback_to_parameter_order_when_labels_are_truncated(tmp_path):
    csv_path = tmp_path / "sol200_sens.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Local Sensitivity Results File",
                "",
                "DV ID,Label,Current Value,Lower Limit,Upper Limit",
                "1,T@PROPER,3.0000E-03,2.4000E-03,3.6000E-03",
                "2,T@PROPER,6.0000E-03,4.8000E-03,7.2000E-03",
                "",
                "Design Response ID,   Label,Response Type,Elem/Grid ID,Component ID,Superelement ID,Subcase ID,Response value,Freq/Time",
                "1,FREQ1,EIGN,1,,0,1,1.4382E-04,0.0",
                "T@PROPER,T@PROPER",
                "5.7322E+00,3.2450E-01",
                "",
            ])
        + "\n",
        encoding="utf-8",
    )

    result = op2_service._parse_formatted_sensitivity_csv(
        str(csv_path),
        parameter_names=["T@PROPERTY_181362", "T@PROPERTY_181363"],
        response_names=["FREQ_MODE_1"],
        response_rows=[{"response_name": "FREQ_MODE_1", "mode_number": 1}],
    )

    assert result is not None
    assert result["column_labels"] == ["T@PROPERTY_181362", "T@PROPERTY_181363"]
    assert result["matrix"].shape == (1, 2)
    assert result["matrix"][0, 0] == 5.7322
    assert result["matrix"][0, 1] == 0.3245
