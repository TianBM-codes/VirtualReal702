import numpy as np

from services.model_update.analysis.inp_service import (
    _apply_transform,
    _best_fit_rigid_transform,
    _build_modal_match_row,
    _compute_dac_dsf,
    _rotation_to_matrix,
    get_modal_correlation_all_scatter_payload,
    get_modal_frequency_consistency_payload,
    get_modal_match_frequency_scatter_payload,
)


def test_best_fit_rigid_transform_recovers_rotation_and_translation():
    src = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [1.0, 1.0, 0.0],
    ], dtype=np.float64)
    expected_rot = _rotation_to_matrix({
        "axis": [0.0, 0.0, 1.0],
        "angle_deg": 90.0,
    })
    expected_trans = np.array([2.0, -1.0, 0.5], dtype=np.float64)
    dst = _apply_transform(src, translation=expected_trans, rotation={"matrix": expected_rot.tolist()})

    rot_m, trans = _best_fit_rigid_transform(src, dst)

    assert np.allclose(rot_m, expected_rot)
    assert np.allclose(trans, expected_trans)


def test_compute_dac_dsf_returns_expected_identity_metrics():
    test_vec = np.array([1.0 + 0.0j, 2.0 + 0.0j, -3.0 + 0.0j], dtype=np.complex128)
    fem_vec = np.array([1.0 + 0.0j, 2.0 + 0.0j, -3.0 + 0.0j], dtype=np.complex128)

    metrics = _compute_dac_dsf(test_vec, fem_vec)

    assert metrics["dac"] == 100.0
    assert metrics["dsf"] == 1.0
    assert metrics["residual_norm"] == 0.0


def test_compute_dac_dsf_complex_mac_returns_identity_for_same_complex_vector():
    test_vec = np.array([1.0 + 2.0j, -3.0 + 0.5j, 0.25 - 0.75j], dtype=np.complex128)
    fem_vec = np.array([1.0 + 2.0j, -3.0 + 0.5j, 0.25 - 0.75j], dtype=np.complex128)

    metrics = _compute_dac_dsf(test_vec, fem_vec, mac_mode="complex")

    assert metrics["mac"] == 100.0
    assert metrics["dac"] == 100.0
    assert metrics["mac_mode"] == "complex"


def test_compute_dac_dsf_complex_mac_is_phase_invariant():
    test_vec = np.array([1.0 + 2.0j, -3.0 + 0.5j, 0.25 - 0.75j], dtype=np.complex128)
    fem_vec = 1j * test_vec

    metrics = _compute_dac_dsf(test_vec, fem_vec, mac_mode="complex")

    assert round(metrics["mac"], 10) == 100.0
    assert round(metrics["dac"], 10) == 100.0
    assert metrics["mac_mode"] == "complex"


def test_build_modal_match_row_exposes_flip_and_scalar_fields():
    row = _build_modal_match_row(
        {
            "fem_mode_no": 4,
            "test_mode_no": 2,
            "mac": 98.765,
            "freq_fem": 12.5,
            "freq_test": 12.0,
            "freq_error_ratio": 0.0416667,
            "flip": True,
        },
        status="matched",
        recommended=True,
        rank=1,
        subcase_name=["SUBCASE_1", "SUBCASE_2"],
    )

    assert row["status"] == "matched"
    assert row["recommended"] is True
    assert row["rank"] == 1
    assert row["fem_mode_no"] == 4
    assert row["test_mode_no"] == 2
    assert row["flip"] is True
    assert row["mac"] == 98.765
    assert row["freq_fem"] == 12.5
    assert row["freq_test"] == 12.0
    assert row["fem_step"] == "SUBCASE_2"
    assert row["fem_frame"] == 3
    assert "MAC:98.765" in row["title"]


def test_modal_match_frequency_scatter_payload_contains_points_and_tooltips(monkeypatch):
    monkeypatch.setattr(
        "services.model_update.analysis.inp_service.match_modal_modes",
        lambda project_id, **kwargs: {
            "project_id": int(project_id),
            "method": kwargs["method"],
            "mac_threshold": kwargs["mac_threshold"],
            "max_freq_error_ratio": kwargs["max_freq_error_ratio"],
            "rows": [
                {
                    "status": "matched",
                    "recommended": True,
                    "fem_mode_no": 1,
                    "test_mode_no": 3,
                    "mac": 97.5,
                    "freq_fem": 31.8,
                    "freq_test": 32.5,
                    "freq_error_ratio": -0.0215,
                    "flip": False,
                    "title": "FEA 1 - 31.8000Hz, EMA 3 - 32.5000Hz MAC:97.500",
                },
                {
                    "status": "matched",
                    "recommended": True,
                    "fem_mode_no": 2,
                    "test_mode_no": 4,
                    "mac": 95.0,
                    "freq_fem": 64.2,
                    "freq_test": 63.9,
                    "freq_error_ratio": 0.0047,
                    "flip": True,
                    "title": "FEA 2 - 64.2000Hz, EMA 4 - 63.9000Hz MAC:95.000",
                },
            ],
            "summary": {"matched_pair_count": 2},
            "unmatched_fem_modes": [5],
            "unmatched_test_modes": [6],
        },
    )

    payload = get_modal_match_frequency_scatter_payload(
        18,
        mac_threshold=0.7,
        max_freq_error_ratio=0.2,
        method="greedy",
    )

    assert payload["project_id"] == 18
    assert payload["chart_type"] == "scatter"
    assert payload["data"][0]["label"] == "matched_modes"
    assert payload["data"][0]["xaxis"] == ["31.800", "64.200"]
    assert payload["data"][0]["data"] == [["31.800", 32.5], ["64.200", 63.9]]
    assert payload["data"][0]["points"][0]["tooltip"]["fem_mode_no"] == 1
    assert payload["data"][0]["points"][1]["tooltip"]["flip"] is True
    assert payload["summary"]["point_count"] == 2
    assert payload["unmatched_fem_modes"] == [5]


def test_match_modal_modes_treats_fractional_mac_threshold_as_percent(monkeypatch):
    monkeypatch.setattr(
        "services.model_update.analysis.inp_service._load_modal_correlation_rows",
        lambda project_id: [
            {
                "fem_mode_no": 1,
                "test_mode_no": 1,
                "mac": 69.0,
                "freq_fem": 31.8,
                "freq_test": 32.5,
                "freq_error_ratio": -0.0215,
                "flip": False,
            },
            {
                "fem_mode_no": 2,
                "test_mode_no": 2,
                "mac": 97.5,
                "freq_fem": 64.2,
                "freq_test": 63.9,
                "freq_error_ratio": 0.0047,
                "flip": True,
            },
        ],
    )

    payload = get_modal_match_frequency_scatter_payload(
        18,
        mac_threshold=0.7,
        max_freq_error_ratio=0.2,
        method="greedy",
    )

    assert payload["mac_threshold"] == 70.0
    assert payload["summary"]["matched_pair_count"] == 1
    assert payload["data"][0]["data"] == [["64.200", 63.9]]


def test_modal_frequency_consistency_payload_auto_computes_when_rows_missing(monkeypatch):
    rows_by_call = [
        [],
        [
            {
                "fem_mode_no": 1,
                "test_mode_no": 1,
                "freq_fem": 10.2,
                "freq_test": 10.0,
            },
            {
                "fem_mode_no": 2,
                "test_mode_no": 1,
                "freq_fem": 20.5,
                "freq_test": 10.0,
            },
            {
                "fem_mode_no": 1,
                "test_mode_no": 2,
                "freq_fem": 10.2,
                "freq_test": 19.5,
            },
            {
                "fem_mode_no": 2,
                "test_mode_no": 2,
                "freq_fem": 20.5,
                "freq_test": 19.5,
            },
            {
                "fem_mode_no": 3,
                "test_mode_no": 1,
                "freq_fem": 30.0,
                "freq_test": 10.0,
            },
            {
                "fem_mode_no": 3,
                "test_mode_no": 2,
                "freq_fem": 30.0,
                "freq_test": 19.5,
            },
        ],
    ]
    captured = {}

    monkeypatch.setattr(
        "services.model_update.analysis.inp_service._load_modal_correlation_rows",
        lambda project_id: rows_by_call.pop(0),
    )
    monkeypatch.setattr(
        "services.model_update.analysis.inp_service.compute_modal_correlation",
        lambda project_id, overwrite=True, mac_threshold=None: captured.update({
            "project_id": project_id,
            "overwrite": overwrite,
            "mac_threshold": mac_threshold,
        }),
    )

    payload = get_modal_frequency_consistency_payload(18)

    assert captured == {"project_id": 18, "overwrite": True, "mac_threshold": None}
    assert payload["project_type"] == "MTXZ"
    assert payload["chart_type"] == "line"
    assert payload["summary"]["fem_mode_count"] == 3
    assert payload["summary"]["test_mode_count"] == 2
    assert payload["summary"]["compared_mode_count"] == 2
    assert payload["summary"]["point_count"] == 2
    assert payload["data"][0]["label"] == "frequency_consistency_error"
    assert payload["data"][0]["xaxis"] == ["1", "2"]
    assert round(payload["data"][0]["data"][0], 10) == 2.0
    assert round(payload["rows"][0]["freq_error_ratio"], 10) == 0.02
    assert round(payload["rows"][0]["freq_error_percent"], 10) == 2.0
    assert round(payload["rows"][1]["freq_error_ratio"], 10) == round((20.5 - 19.5) / 19.5, 10)
    assert payload["rows"][0]["fem_mode_no"] == 1
    assert payload["rows"][1]["fem_mode_no"] == 2


def test_modal_correlation_all_scatter_payload_returns_only_matched_pairs(monkeypatch):
    monkeypatch.setattr(
        "services.model_update.analysis.inp_service.match_modal_modes",
        lambda project_id, **kwargs: {
            "project_id": int(project_id),
            "method": kwargs["method"],
            "mac_threshold": 70.0,
            "max_freq_error_ratio": kwargs["max_freq_error_ratio"],
            "rows": [
                {
                    "status": "matched",
                    "recommended": True,
                    "fem_mode_no": 1,
                    "test_mode_no": 1,
                    "dof_pair_count": 8,
                    "mac": 97.5,
                    "freq_fem": 31.8,
                    "freq_test": 32.5,
                    "freq_error_ratio": -0.0215,
                    "flip": False,
                },
                {
                    "status": "matched",
                    "recommended": True,
                    "fem_mode_no": 2,
                    "test_mode_no": 2,
                    "dof_pair_count": 8,
                    "mac": 95.0,
                    "freq_fem": 64.2,
                    "freq_test": 63.9,
                    "freq_error_ratio": 0.0047,
                    "flip": True,
                },
            ],
            "summary": {"matched_pair_count": 2},
            "unmatched_fem_modes": [3],
            "unmatched_test_modes": [4],
        },
    )

    payload = get_modal_correlation_all_scatter_payload(18, mac_threshold=0.7, max_freq_error_ratio=0.2, method="greedy")

    assert payload["project_id"] == 18
    assert payload["chart_type"] == "scatter"
    assert payload["value_label"] == "mac"
    assert payload["data"][0]["label"] == "matched_modes"
    assert payload["data"][0]["data"] == [["31.800", 32.5], ["64.200", 63.9]]
    assert payload["data"][0]["points"][0]["tooltip"]["mac"] == 97.5
    assert payload["data"][0]["points"][1]["tooltip"]["flip"] is True
    assert payload["summary"]["point_count"] == 2
    assert payload["summary"]["matched_pair_count"] == 2
    assert payload["unmatched_fem_modes"] == [3]
