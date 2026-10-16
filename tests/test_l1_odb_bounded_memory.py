"""Regression coverage for disk-backed ODB extraction and launcher failures.

Load actual function definitions without importing Abaqus or changing stdout.
These tests are intended to run on the developer's machine.
"""
import ast
from collections import deque
import json
import logging
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import h5py
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_functions(relative, names, namespace):
    source = ROOT / relative
    tree = ast.parse(source.read_text(encoding='utf-8'))
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    tree = ast.Module(body=selected, type_ignores=[])
    exec(compile(tree, str(source), 'exec'), namespace)
    return namespace


@pytest.fixture
def exporter():
    return _load_functions('src/l1/abaqus_dump.py', {
        'npsave', '_block_ncomp', '_block_sp_num', '_ip_block_layout',
        '_en_block_layout', '_local_node_indices', '_write_frame_blocks',
        '_compute_invariants_numpy', '_main_checked',
        'mkdirs', 'jdump', 'safe', '_canon_inst', '_fmt_t', '_pos_str',
        'split_region_field', 'dump_results',
    }, {'np': np, 'os': os, 'sys': sys, 'traceback': traceback,
        'json': json, 'time': time, '_RESULT_CHUNK_ROWS': 5,
        'PROCEDURE_MAP': {'STATIC': 'STATIC'}, '_INV_CONSTANTS': {},
        '_ELEM_NODAL_CONST': None})


class _ChunkOnlyData:
    """Reject whole-block numpy conversion and simulate a late allocation error."""
    def __init__(self, values, fail_after=None):
        self.values = np.asarray(values, dtype=np.float32)
        self.fail_after = fail_after

    def __len__(self):
        return len(self.values)

    def __array__(self, *args, **kwargs):
        raise AssertionError('Whole-block data conversion is forbidden')

    def __getitem__(self, key):
        assert isinstance(key, slice)
        assert key.stop - key.start <= 5
        if self.fail_after is not None and key.start >= self.fail_after:
            raise MemoryError('simulated low-memory failure')
        return self.values[key]


def test_ip_chunks_keep_sorted_labels_point_order_and_missing_elements(exporter, tmp_path):
    path = str(tmp_path / 'frame.npy')
    blocks = [
        SimpleNamespace(elementLabels=[20, 10, 20, 10, 40, 40],
                        integrationPoints=[3, 1, 1, 3, 3, 1],
                        data=_ChunkOnlyData([203, 101, 201, 103, 403, 401])),
        SimpleNamespace(elementLabels=[10, 10], integrationPoints=[3, 1],
                        data=_ChunkOnlyData([113, 111])),
    ]
    exporter['_write_frame_blocks'](
        path, np.array([10, 20, 30, 40]), 'INTEGRATION_POINT', blocks,
        (4, 2, 1), ip_labels=np.array([1, 3]))
    np.testing.assert_array_equal(np.load(path)[:, :, 0],
                                  [[111, 113], [201, 203], [0, 0], [401, 403]])
    assert not Path(path + '.partial.npy').exists()


def test_ip_without_point_labels_preserves_cycle_across_chunk_boundary(exporter, tmp_path):
    path = str(tmp_path / 'frame.npy')
    block = SimpleNamespace(elementLabels=[10] * 4 + [20] * 4,
                            data=_ChunkOnlyData(np.arange(8)))
    exporter['_write_frame_blocks'](
        path, np.array([10, 20]), 'INTEGRATION_POINT', [block],
        (2, 4, 1), ip_labels=np.arange(1, 5))
    np.testing.assert_array_equal(np.load(path)[:, :, 0], [[0, 1, 2, 3], [4, 5, 6, 7]])


def test_en_interleaved_elements_keep_local_node_order_across_chunks(exporter, tmp_path):
    path = str(tmp_path / 'frame.npy')
    block = SimpleNamespace(elementLabels=[20, 10, 20, 10, 20, 10, 20, 10],
                            data=_ChunkOnlyData([21, 11, 22, 12, 23, 13, 24, 14]))
    exporter['_write_frame_blocks'](
        path, np.array([10, 20, 30]), 'ELEMENT_NODAL', [block], (3, 4, 1))
    np.testing.assert_array_equal(np.load(path)[:, :, 0],
                                  [[11, 12, 13, 14], [21, 22, 23, 24], [0, 0, 0, 0]])


@pytest.mark.parametrize('position,label_attr', [('NODAL', 'nodeLabels'),
                                               ('WHOLE_ELEMENT', 'elementLabels')])
def test_scalar_chunks_ignore_unknown_labels_without_mapping_them_to_neighbours(
        exporter, tmp_path, position, label_attr):
    path = str(tmp_path / 'frame.npy')
    block = SimpleNamespace(**{label_attr: [1, 10, 15, 20, 90, 10],
                               'data': _ChunkOnlyData([1, 2, 3, 4, 5, 6])})
    exporter['_write_frame_blocks'](path, np.array([10, 20, 30]), position,
                                     [block], (3, 1))
    np.testing.assert_array_equal(np.load(path)[:, 0], [6, 4, 0])


def test_invariant_chunks_preserve_method_b_and_nan_for_missing_elements(exporter, tmp_path):
    path = str(tmp_path / 'mises.npy')
    block = SimpleNamespace(elementLabels=[10] * 4 + [20] * 4,
                            data=_ChunkOnlyData([[100, 0, 0, 0]] * 4 +
                                                [[-50, 0, 0, 0]] * 4))
    exporter['_write_frame_blocks'](
        path, np.array([10, 20, 30]), 'ELEMENT_NODAL', [block], (3, 4, 1),
        invariant='MISES', fill_value=np.nan)
    actual = np.load(path)[:, :, 0]
    np.testing.assert_array_equal(actual[:2], [[100] * 4, [50] * 4])
    assert np.isnan(actual[2]).all()


def test_strain_shear_conversion_is_preserved_in_chunks(exporter, tmp_path):
    path = str(tmp_path / 'strain.npy')
    block = SimpleNamespace(nodeLabels=[10], data=_ChunkOnlyData([[0, 0, 0, 2]]))
    exporter['_write_frame_blocks'](
        path, np.array([10]), 'NODAL', [block], (1, 1),
        invariant='MAX_INPLANE_PRINCIPAL', is_strain=True, fill_value=np.nan)
    np.testing.assert_array_equal(np.load(path), [[1]])


def test_structure_discovery_does_not_read_tensor_values(exporter):
    class LabelsOnly:
        elementLabels = [20, 20, 10, 10]
        integrationPoints = [3, 1, 3, 1]
        sectionPoint = SimpleNamespace(number=5)
        width = 4

        @property
        def data(self):
            raise AssertionError('Metadata discovery must not read tensor values')

    block = LabelsOnly()
    elems, ips, sp = exporter['_ip_block_layout'](block)
    np.testing.assert_array_equal(elems, [10, 20])
    np.testing.assert_array_equal(ips, [1, 3])
    assert sp == 5
    assert exporter['_block_ncomp'](block) == 4
    elems, local_nodes = exporter['_en_block_layout'](block)
    np.testing.assert_array_equal(elems, [10, 20])
    assert local_nodes == 2


def test_real_dump_results_preserves_section_points_and_deleted_element_alignment(
        exporter, tmp_path):
    instance = SimpleNamespace(name='part-1-1')
    def block(labels, values, sp):
        return SimpleNamespace(instance=instance, position='INTEGRATION_POINT',
                               elementType='S4', sectionPoint=SimpleNamespace(number=sp),
                               elementLabels=labels, integrationPoints=[1, 3] * (len(labels) // 2),
                               width=1, componentLabels=['LE11'], data=_ChunkOnlyData(values))
    def frame(blocks):
        field = SimpleNamespace(bulkDataBlocks=blocks, componentLabels=['LE11'],
                                validInvariants=[])
        return SimpleNamespace(fieldOutputs={'LE': field}, frameValue=1, description='')
    frames = [frame([block([20, 20, 10, 10], [21, 23, 11, 13], 1),
                     block([10, 10, 20, 20], [51, 53, 61, 63], 5)]),
              frame([block([20, 20], [121, 123], 1),
                     block([10, 10], [151, 153], 5)])]
    odb = SimpleNamespace(steps={'Step-1': SimpleNamespace(frames=frames, procedure='STATIC')})
    meta = {}
    exporter['dump_results'](odb, str(tmp_path), meta)
    parent = next((tmp_path / 'results').iterdir()) / 'PART-1-1/INTEGRATION_POINT/S4'
    np.testing.assert_array_equal(np.load(parent / 'sp1/labels.npy'), [10, 20])
    np.testing.assert_array_equal(np.load(parent / 'sp1/f0001.npy')[:, :, 0],
                                  [[0, 0], [121, 123]])
    np.testing.assert_array_equal(np.load(parent / 'sp5/f0001.npy')[:, :, 0],
                                  [[151, 153], [0, 0]])
    assert meta['steps']['Step-1']['num_frames'] == 2


def test_late_memory_error_keeps_old_frame_and_removes_partial_file(exporter, tmp_path):
    path = str(tmp_path / 'frame.npy')
    np.save(path, np.array([[123]], dtype=np.float32))
    original = Path(path).read_bytes()
    block = SimpleNamespace(nodeLabels=list(range(10)),
                            data=_ChunkOnlyData(np.arange(10), fail_after=5))
    with pytest.raises(MemoryError):
        exporter['_write_frame_blocks'](path, np.arange(10), 'NODAL', [block], (10, 1))
    assert Path(path).read_bytes() == original
    assert not Path(path + '.partial.npy').exists()


def test_extrapolation_memory_failure_is_fatal_after_structure_was_discovered(exporter, tmp_path):
    instance = SimpleNamespace(name='PART-1-1')
    ip = SimpleNamespace(instance=instance, position='INTEGRATION_POINT', elementType='S4',
                         elementLabels=[10, 10], integrationPoints=[1, 3], width=1,
                         componentLabels=['S11'], data=_ChunkOnlyData([11, 13]))
    en = SimpleNamespace(instance=instance, position='ELEMENT_NODAL', elementType='S4',
                         elementLabels=[10] * 4, width=1, componentLabels=['S11'])
    calls = []
    def subset(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(bulkDataBlocks=[en])
        raise MemoryError('Abaqus extrapolation ran out of memory')
    field = SimpleNamespace(bulkDataBlocks=[ip], componentLabels=['S11'], validInvariants=[],
                            getSubset=subset)
    frame = SimpleNamespace(fieldOutputs={'S': field}, frameValue=1, description='')
    odb = SimpleNamespace(steps={'Step-1': SimpleNamespace(frames=[frame], procedure='STATIC')})
    exporter['_ELEM_NODAL_CONST'] = 'ELEMENT_NODAL'
    with pytest.raises(MemoryError):
        exporter['dump_results'](odb, str(tmp_path), {})
    assert len(calls) == 2


@pytest.mark.parametrize('error', [MemoryError, RuntimeError, lambda: SystemExit(1)])
def test_failed_main_never_emits_success_marker(exporter, capsys, error):
    def fail():
        raise error()
    exporter['main'] = fail
    with pytest.raises(SystemExit):
        exporter['_main_checked']()
    output = capsys.readouterr().out
    assert 'ODB_DUMP_FAILED:' in output
    assert 'ODB_DUMP_COMPLETE' not in output


def test_success_marker_is_emitted_after_main_returns(exporter, capsys):
    exporter['main'] = lambda: print('files closed')
    exporter['_main_checked']()
    assert capsys.readouterr().out.splitlines() == ['files closed', 'ODB_DUMP_COMPLETE']


def _runner(lines, returncode=0):
    launches, events, statuses = [], [], []
    proc = SimpleNamespace(stdout=iter(lines), returncode=returncode, wait=lambda: None)
    def launch(*args, **kwargs):
        launches.append(args[0])
        return proc
    namespace = {
        'deque': deque, 'subprocess': SimpleNamespace(Popen=launch, PIPE=-1, STDOUT=-2),
        'logger': logging.getLogger(__name__), 'DUMP_SCRIPT': ROOT / 'src/l1/abaqus_dump.py',
        '_log_job': lambda *args, **kwargs: events.append((args, kwargs)),
        '_update_project_geom_status': lambda *args: statuses.append(args),
        'os': os, 'ABAQUS_CMD': 'abaqus', 'INVARIANTS_MODE': 'none',
        '_kw': lambda text: text, '_bad': lambda text: text, '_esc': lambda text: text,
    }
    _load_functions('src/job_runner.py',
                    {'_run_streaming', '_is_odb_version_error', '_run_odb_project'}, namespace)
    return namespace, launches, events, statuses


@pytest.mark.parametrize('lines,expected_rc', [
    (['normal progress', 'ODB_DUMP_COMPLETE'], 0),
    (['normal progress'], 1),
    (['ODB_DUMP_FAILED: failed'], 1),
    (['numpy.core._exceptions._ArrayMemoryError: failed'] + ['progress'] * 60 +
     ['ODB_DUMP_COMPLETE'], 1),
    (['source=MemoryError.odb', 'ODB_DUMP_COMPLETE'], 0),
    (['ODB_VERSION_ERROR: previous release', 'ODB_DUMP_FAILED: failed'], 2),
])
def test_launcher_zero_exit_code_requires_real_completion(lines, expected_rc):
    runner, _, _, _ = _runner(lines)
    rc, tail = runner['_run_streaming']([str(runner['DUMP_SCRIPT'])], '589', 'l1_dump')
    assert rc == expected_rc
    if 'MemoryError:' in lines[0]:
        assert '内存不足' in tail
    if expected_rc == 2:
        assert runner['_is_odb_version_error'](rc, tail)


def test_project_does_not_launch_pack_after_dump_memory_failure(tmp_path):
    runner, launches, _, statuses = _runner(['MemoryError: insufficient memory'])
    odb = tmp_path / 'source.odb'
    odb.touch()
    assert runner['_run_odb_project']('589', str(odb), str(tmp_path)) is False
    assert len(launches) == 1
    assert statuses[0][1] == 'error'
    assert '内存不足' in statuses[0][2]


def test_non_odb_subprocess_does_not_need_dump_marker():
    runner, _, _, _ = _runner(['ordinary pack output'])
    assert runner['_run_streaming'](['python', 'l1_pack.py'], '589', 'l1_pack')[0] == 0


def test_hdf5_pack_preserves_nonfinite_values_and_reduces_range(tmp_path):
    packer = _load_functions('src/l1/l1_pack.py', {'_pack_result_frame'}, {'np': np})
    values = np.ones((65540, 2, 1), dtype=np.float32)
    values[0, 0, 0] = np.nan
    values[0, 1, 0] = np.inf
    values[-1, :, 0] = [-3, 7]
    path = tmp_path / 'frame.npy'
    np.save(path, values)
    with h5py.File(tmp_path / 'result.h5', 'w') as h5:
        ds = h5.create_dataset('data', shape=(2,) + values.shape, dtype='float32')
        assert packer['_pack_result_frame'](ds, 1, str(path)) == (-3, 7)
        np.testing.assert_array_equal(ds[1], values)
        assert not ds[0].any()
    # Windows can rename only after the input mapping was closed.
    path.rename(tmp_path / 'closed.npy')
