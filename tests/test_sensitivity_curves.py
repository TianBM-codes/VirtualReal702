from services.model_update.analysis import sensitivity_service


def test_get_stored_sensitivity_parameter_curves_formats_series(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_stored_sensitivity_run",
        lambda **kwargs: {
            "analysis_run_id": 11,
            "project_id": 1001,
            "batch_no": "2",
            "case_name": "demo",
            "created_at": "2026-04-27T12:00:00",
            "row_names": ["response1", "response2"],
            "col_names": ["T1", "T2", "E1"],
            "matrix": [
                [1.1, 1.2, 1.3],
                [2.1, 2.2, 2.3],
            ],
        },
    )

    result = sensitivity_service.get_stored_sensitivity_parameter_curves(
        project_id=1001,
        batch_no="2",
    )

    assert result["curve_type"] == "parameter"
    assert result["summary"]["curve_count"] == 2
    assert result["data"] == [
        {
            "data": [["T1", 1.1], ["T2", 1.2], ["E1", 1.3]],
            "label": "response1",
            "xaxis": ["T1", "T2", "E1"],
        },
        {
            "data": [["T1", 2.1], ["T2", 2.2], ["E1", 2.3]],
            "label": "response2",
            "xaxis": ["T1", "T2", "E1"],
        },
    ]


def test_get_stored_sensitivity_response_curves_formats_series(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_stored_sensitivity_run",
        lambda **kwargs: {
            "analysis_run_id": 11,
            "project_id": 1001,
            "batch_no": "2",
            "case_name": "demo",
            "created_at": "2026-04-27T12:00:00",
            "row_names": ["response1", "response2"],
            "col_names": ["T1", "T2", "E1"],
            "matrix": [
                [1.1, 1.2, 1.3],
                [2.1, 2.2, 2.3],
            ],
        },
    )

    result = sensitivity_service.get_stored_sensitivity_response_curves(
        project_id=1001,
        batch_no="2",
    )

    assert result["curve_type"] == "response"
    assert result["summary"]["curve_count"] == 3
    assert result["data"] == [
        {
            "data": [["response1", 1.1], ["response2", 2.1]],
            "label": "T1",
            "xaxis": ["response1", "response2"],
        },
        {
            "data": [["response1", 1.2], ["response2", 2.2]],
            "label": "T2",
            "xaxis": ["response1", "response2"],
        },
        {
            "data": [["response1", 1.3], ["response2", 2.3]],
            "label": "E1",
            "xaxis": ["response1", "response2"],
        },
    ]
