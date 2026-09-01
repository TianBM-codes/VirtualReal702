from types import SimpleNamespace

import h5py
import numpy as np

from src.l1 import op2_pack


class _StressResult:
    def __init__(self):
        # Two shell layers for element 10 and one centroid sample for element 20.
        self.element_node = np.array([[10, 0], [10, 0], [20, 0]], dtype=np.int32)
        self.data = np.array([[
            [-1.0, 10.0, 5.0, 2.0, 0.0, 0.0, 0.0, 11.0],
            [1.0, 20.0, 5.0, 3.0, 0.0, 0.0, 0.0, 21.0],
            [0.0, 4.0, 2.0, 1.0, 0.0, 0.0, 0.0, 5.0],
        ]], dtype=np.float32)

    @staticmethod
    def get_headers():
        return ['fiber_distance', 'oxx', 'oyy', 'txy', 'angle', 'major', 'minor', 'ovm']


def test_collect_subcase_stress_selects_max_mises_shell_layer():
    op2 = SimpleNamespace(
        op2_results=SimpleNamespace(
            stress=SimpleNamespace(cquad4_stress={1: _StressResult()})
        )
    )

    samples = op2_pack._collect_subcase_stress(op2, 1, 1)

    assert samples['node_labels'].tolist() == []
    assert samples['element_labels'].tolist() == [10, 20]
    np.testing.assert_allclose(samples['element_data'][0, :, 0], [21.0, 5.0])


def test_write_subcase_stress_writes_element_nodal_blocks(tmp_path):
    path = tmp_path / 'SUBCASE_1__S.h5'
    samples = {
        'node_labels': np.array([100, 101], dtype=np.int32),
        'node_data': np.array([[[30.0], [10.0]]], dtype=np.float32),
        'element_labels': np.array([20], dtype=np.int32),
        'element_data': np.array([[[5.0]]], dtype=np.float32),
    }

    blocks, value_min, value_max = op2_pack._write_subcase_s(
        str(path), 'BDF_MODEL', 'SUBCASE_1', [1.0], ['frame 0'],
        np.array([100, 101, 102, 103], dtype=np.int32),
        {
            'CQUAD4': {
                'labels': np.array([10, 20], dtype=np.int32),
                'conn': np.array([[0, 1, -1], [1, 2, 3]], dtype=np.int32),
            },
        },
        samples,
    )

    assert blocks == [(None, '/NODAL/BDF_MODEL', 4)]
    assert value_min == 5.0
    assert value_max == 30.0
    with h5py.File(path, 'r') as h5:
        assert h5['meta/field_name'][()] == b'S'
        assert h5['NODAL/BDF_MODEL/data'].shape == (1, 4, 1)
        np.testing.assert_allclose(
            h5['NODAL/BDF_MODEL/data'][0, :, 0], [30.0, 7.5, 5.0, 5.0],
        )
