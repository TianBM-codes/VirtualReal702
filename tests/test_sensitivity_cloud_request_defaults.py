from services.model_update.analysis import sensitivity_service


def test_build_sensitivity_cloud_requests_infers_compact_default_result_group():
    payloads = sensitivity_service._build_sensitivity_cloud_requests(
        matrix_payload={
            "response_rows": [
                {
                    "row_key": "R1",
                    "response_name": "FREQ_MODE_1",
                    "response_label": "MODE 1",
                }
            ],
            "parameter_columns": [
                {
                    "parameter_name": "E1",
                    "type": "E",
                    "field": "d_U_P1",
                    "element_mapping": {
                        "target_kind": "cell",
                        "targets_by_scope": {"PART-1-1": [101]},
                    },
                }
            ],
            "matrix": [[0.1]],
        },
        batch_no="3",
        result_group=None,
        step_name="Sensitivity Step",
    )

    assert len(payloads) == 1
    assert payloads[0]["metadata"]["result_group"] == "sensitivity_3_FREQ_MODE_1"
    assert payloads[0]["request_body"]["result_group"] == "sensitivity_3_FREQ_MODE_1"
