import numpy as np

from services.model_update.analysis.inp_service import (
    _apply_transform,
    _best_fit_rigid_transform,
    _build_modal_match_row,
    _compute_dac_dsf,
    _rotation_to_matrix,
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
    assert payload["data"][0]["xaxis"] == ["31.8", "64.2"]
    assert payload["data"][0]["data"] == [[31.8, 32.5], [64.2, 63.9]]
    assert payload["data"][0]["points"][0]["tooltip"]["fem_mode_no"] == 1
    assert payload["data"][0]["points"][1]["tooltip"]["flip"] is True
    assert payload["summary"]["point_count"] == 2
    assert payload["unmatched_fem_modes"] == [5]
