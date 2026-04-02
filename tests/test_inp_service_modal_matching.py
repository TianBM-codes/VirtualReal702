import numpy as np

from services.model_update.analysis.inp_service import (
    _apply_transform,
    _best_fit_rigid_transform,
    _compute_dac_dsf,
    _rotation_to_matrix,
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
