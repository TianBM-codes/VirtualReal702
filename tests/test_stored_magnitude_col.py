"""OP2 位移文件自带 USUM 列时，默认合量不能把它再算一遍（否则是 √2·|U|）。"""
import h5py
import numpy as np

from src.l3.services.result_service import (
    _component_cache_token, _extract_component, _scalar_nodal_by_idx,
    drop_stored_magnitude, stored_magnitude_col,
)


def _write_u(path, components, data):
    dt = h5py.special_dtype(vlen=str)
    with h5py.File(path, "w") as f:
        ds = f.create_dataset("meta/components", shape=(len(components),), dtype=dt)
        for i, c in enumerate(components):
            ds[i] = c
        f.create_dataset("NODAL/PART-1/data", data=data.astype(np.float32))


def _u_with_usum():
    u = np.array([[[3.0, 4.0, 0.0], [1.0, 2.0, 2.0]]])          # [1 帧, 2 节点, 3]
    usum = np.linalg.norm(u, axis=-1, keepdims=True)
    return np.concatenate([u, usum], axis=-1)                    # [1, 2, 4]


def test_op2_default_magnitude_excludes_usum(tmp_path):
    path = tmp_path / "u_op2.h5"
    _write_u(path, ["U1", "U2", "U3", "USUM"], _u_with_usum())
    with h5py.File(path, "r") as f:
        assert stored_magnitude_col(f) == 3
        scalar, num_frames = _scalar_nodal_by_idx(f, "PART-1", 0, None)
        explicit, _ = _scalar_nodal_by_idx(f, "PART-1", 0, 3)
    assert num_frames == 1
    np.testing.assert_allclose(scalar, [5.0, 3.0], rtol=1e-6)
    np.testing.assert_allclose(scalar, explicit, rtol=1e-6)


def test_odb_without_usum_unchanged(tmp_path):
    path = tmp_path / "u_odb.h5"
    _write_u(path, ["U1", "U2", "U3"], _u_with_usum()[..., :3])
    with h5py.File(path, "r") as f:
        assert stored_magnitude_col(f) is None
        scalar, _ = _scalar_nodal_by_idx(f, "PART-1", 0, None)
    np.testing.assert_allclose(scalar, [5.0, 3.0], rtol=1e-6)
    assert _component_cache_token(str(path), (1, 2), None) is None


def test_cache_token_changes_only_for_stored_magnitude(tmp_path):
    path = tmp_path / "u_op2.h5"
    _write_u(path, ["U1", "U2", "U3", "USUM"], _u_with_usum())
    assert _component_cache_token(str(path), (1, 2), None) == "mag-excl-USUM"
    assert _component_cache_token(str(path), (1, 2), 3) == 3


def test_scalar_field_and_explicit_index_untouched():
    data = np.array([[-2.0], [1.0]])
    np.testing.assert_allclose(_extract_component(data, None, mag_col=0), [-2.0, 1.0])
    four = _u_with_usum()[0]
    np.testing.assert_allclose(_extract_component(four, 0, mag_col=3), [3.0, 1.0])
    assert drop_stored_magnitude(four, None) is four or np.array_equal(
        drop_stored_magnitude(four, None), four)
