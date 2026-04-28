from services.model_update.analysis import sensitivity_service
from src.l3.core.errors import NotFoundError


def test_response_display_name_strips_duplicate_instance_prefix():
    result = sensitivity_service._response_display_name(
        {
            "row_key": "PART-1-1|U|U2|NODAL|PART-1-1::108",
        }
    )

    assert result == "PART-1-1|U|U2|NODAL|108"


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


def test_get_stored_sensitivity_parameter_curves_normalizes_legacy_response_labels(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_stored_sensitivity_run",
        lambda **kwargs: {
            "analysis_run_id": 11,
            "project_id": 1001,
            "batch_no": "2",
            "case_name": "demo",
            "created_at": "2026-04-27T12:00:00",
            "row_names": ["PART-1-1|U|U2|NODAL|PART-1-1::108"],
            "col_names": ["T1"],
            "matrix": [[1.1]],
        },
    )

    result = sensitivity_service.get_stored_sensitivity_parameter_curves(
        project_id=1001,
        batch_no="2",
    )

    assert result["data"][0]["label"] == "PART-1-1|U|U2|NODAL|108"


def test_get_stored_sensitivity_table_and_matrix_normalize_legacy_response_labels(monkeypatch):
    monkeypatch.setattr(
        sensitivity_service,
        "_load_stored_sensitivity_run",
        lambda **kwargs: {
            "analysis_run_id": 11,
            "project_id": 1001,
            "batch_no": "2",
            "case_name": "demo",
            "created_at": "2026-04-27T12:00:00",
            "row_names": ["PART-1-1|U|U2|NODAL|PART-1-1::108"],
            "col_names": ["T1"],
            "matrix": [[1.1]],
        },
    )

    table_result = sensitivity_service.get_stored_sensitivity_table_points(
        project_id=1001,
        batch_no="2",
    )
    matrix_result = sensitivity_service.get_stored_sensitivity_matrix_payload(
        project_id=1001,
        batch_no="2",
    )

    assert table_result["rows"] == ["T1"]
    assert table_result["column"] == ["PART-1-1|U|U2|NODAL|108"]
    assert table_result["data"] == [{"PART-1-1|U|U2|NODAL|108": 1.1}]
    assert matrix_result["data"]["rows"] == ["PART-1-1|U|U2|NODAL|108"]


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


def test_get_stored_sensitivity_matrix_payload_returns_empty_when_not_found(monkeypatch):
    def _raise_not_found(**kwargs):
        raise NotFoundError("missing")

    monkeypatch.setattr(sensitivity_service, "_load_stored_sensitivity_run", _raise_not_found)

    result = sensitivity_service.get_stored_sensitivity_matrix_payload(
        project_id=1001,
        batch_no="2",
    )

    assert result["analysis_run_id"] is None
    assert result["data"] == {"column": [], "rows": [], "data": []}
    assert result["summary"] == {"response_count": 0, "parameter_count": 0}


def test_get_stored_sensitivity_parameter_curves_returns_empty_when_not_found(monkeypatch):
    def _raise_not_found(**kwargs):
        raise NotFoundError("missing")

    monkeypatch.setattr(sensitivity_service, "_load_stored_sensitivity_run", _raise_not_found)

    result = sensitivity_service.get_stored_sensitivity_parameter_curves(
        project_id=1001,
        batch_no="2",
    )

    assert result["analysis_run_id"] is None
    assert result["curve_type"] == "parameter"
    assert result["data"] == []
    assert result["summary"] == {"response_count": 0, "parameter_count": 0, "curve_count": 0}


def test_get_stored_sensitivity_response_curves_returns_empty_when_not_found(monkeypatch):
    def _raise_not_found(**kwargs):
        raise NotFoundError("missing")

    monkeypatch.setattr(sensitivity_service, "_load_stored_sensitivity_run", _raise_not_found)

    result = sensitivity_service.get_stored_sensitivity_response_curves(
        project_id=1001,
        batch_no="2",
    )

    assert result["analysis_run_id"] is None
    assert result["curve_type"] == "response"
    assert result["data"] == []
    assert result["summary"] == {"response_count": 0, "parameter_count": 0, "curve_count": 0}
