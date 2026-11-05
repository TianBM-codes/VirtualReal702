"""Regression coverage for ODB failure reporting (run on the developer machine)."""
import ast
from collections import deque
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback
from types import SimpleNamespace

import numpy as np
import pytest

from src.l3.infra.manifest_repo import ManifestRepo

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def exporter():
    return _load_functions('src/l1/abaqus_dump.py', {
        '_main_checked', 'dump_results', 'reshape_ip_block',
        'reshape_element_nodal_block', '_block_data_2d', '_block_sp_num',
        'mkdirs', 'npsave', 'jdump', 'safe', '_canon_inst', '_fmt_t',
        '_pos_str', 'split_region_field',
    }, {'np': np, 'os': os, 'sys': sys, 'traceback': traceback,
        'json': json, 'time': time, 'PROCEDURE_MAP': {'STATIC': 'STATIC'},
        '_INV_CONSTANTS': {}, '_ELEM_NODAL_CONST': None})


def _load_functions(relative, names, namespace):
    source = ROOT / relative
    tree = ast.parse(source.read_text(encoding='utf-8'))
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    tree = ast.Module(body=selected, type_ignores=[])
    exec(compile(tree, str(source), 'exec'), namespace)
    return namespace


def test_extrapolation_memory_failure_is_fatal_after_structure_was_discovered(exporter, tmp_path):
    instance = SimpleNamespace(name='PART-1-1')
    ip = SimpleNamespace(instance=instance, position='INTEGRATION_POINT', elementType='S4',
                         elementLabels=[10, 10], integrationPoints=[1, 3], width=1,
                         componentLabels=['S11'], data=np.array([[11], [13]], dtype=np.float32))
    en = SimpleNamespace(instance=instance, position='ELEMENT_NODAL', elementType='S4',
                         elementLabels=[10] * 4, width=1, componentLabels=['S11'],
                         data=np.zeros((4, 1), dtype=np.float32))
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


def _result_group_runner(tmp_path, outputs, upgrade_ok=True, use_temp=True):
    source = tmp_path / 'source.odb'
    source.touch()
    upgraded = tmp_path / 'source_upgraded_tmp.odb' if use_temp else source
    launches, statuses, upgrades = [], [], []
    outputs = iter(outputs)

    def run(cmd, project_id, stage):
        launches.append((list(cmd), stage))
        return next(outputs)

    def upgrade(path, project_id):
        upgrades.append((path, project_id))
        return upgrade_ok, str(upgraded), 'upgrade diagnostic'

    namespace = {
        'os': os, 'sys': sys, 'json': json, 'logger': logging.getLogger(__name__),
        'ABAQUS_CMD': 'abaqus', 'DUMP_SCRIPT': ROOT / 'src/l1/abaqus_dump.py',
        'PACK_SCRIPT': ROOT / 'src/l1/l1_pack.py', 'INVARIANTS_MODE': 'none',
        'ODB_FIELDS': 'U,S', '_run_streaming': run, '_upgrade_odb': upgrade,
        '_project_used_inp_geom': lambda _: True,
        '_update_result_group_status': lambda *args: statuses.append(args),
        '_log_job': lambda *args, **kwargs: None,
        **{name: (lambda text: text) for name in ('_kw', '_bad', '_good', '_esc')},
    }
    _load_functions('src/job_runner.py', {
        '_run_result_group', '_is_odb_version_error', '_odb_field_list',
        '_append_extract_filters',
    }, namespace)
    return namespace, source, upgraded, launches, statuses, upgrades


@pytest.mark.parametrize('use_temp', [False, True])
def test_result_group_upgrades_once_and_preserves_extract_options(tmp_path, use_temp):
    runner, source, upgraded, launches, statuses, upgrades = _result_group_runner(
        tmp_path, [(2, 'ODB_VERSION_ERROR: previous release'), (0, ''), (0, '')],
        use_temp=use_temp)
    options = json.dumps({'consistency_check': 'strict', 'invariants': 'full',
                          'fields': ['U', 'S'], 'steps': ['Step-1'], 'frames': [0, 2],
                          'field_prefix': 'DSA_', 'display_name': 'Result'})
    assert runner['_run_result_group']('589', 'stress', str(source), options, str(tmp_path))
    assert upgrades == [(str(source), '589')]
    assert [stage for _, stage in launches] == ['rg_extract', 'rg_extract', 'rg_l1_pack']
    original, retry = launches[0][0], launches[1][0]
    expected = list(original)
    expected[expected.index('--odb') + 1] = str(upgraded)
    assert retry == expected
    for flag, value in {'--geom-source': 'inp', '--invariants': 'full',
                        '--field-list': 'U,S', '--steps': 'Step-1', '--frames': '0,2',
                        '--field-prefix': 'DSA_', '--result-group': 'stress',
                        '--check-mode': 'strict', '--mode': 'extract'}.items():
        assert retry[retry.index(flag) + 1] == value
    pack = launches[2][0]
    assert pack[pack.index('--source-file') + 1] == 'source.odb'
    assert statuses == [('589', 'stress', 'ready')]


@pytest.mark.parametrize('upgrade_ok,retry_output,error', [
    (False, None, 'ODB upgrade failed: upgrade diagnostic'),
    (True, (2, 'ODB_VERSION_ERROR: still old'), 'extract failed:'),
    (True, (1, 'MemoryError: failed'), 'extract failed:'),
    (True, (0, 'ODB_CONSISTENCY_FAIL: mismatch'), 'consistency check failed:'),
])
def test_result_group_upgrade_failures_never_pack(tmp_path, upgrade_ok, retry_output, error):
    outputs = [(2, 'ODB_VERSION_ERROR: previous release')]
    if retry_output is not None:
        outputs.append(retry_output)
    runner, source, _, launches, statuses, upgrades = _result_group_runner(
        tmp_path, outputs, upgrade_ok=upgrade_ok)
    assert not runner['_run_result_group']('589', 'stress', str(source), None, str(tmp_path))
    assert len(upgrades) == 1
    assert len(launches) == (2 if upgrade_ok else 1)
    assert all(stage == 'rg_extract' for _, stage in launches)
    assert len(statuses) == 1
    assert statuses[0][:3] == ('589', 'stress', 'error')
    assert statuses[0][3].startswith(error)


@pytest.mark.parametrize('output,success', [((0, ''), True), ((1, 'unrelated failure'), False)])
def test_result_group_without_version_error_does_not_upgrade(tmp_path, output, success):
    runner, source, _, launches, statuses, upgrades = _result_group_runner(
        tmp_path, [output, (0, '')])
    assert runner['_run_result_group']('589', 'stress', str(source), None, str(tmp_path)) is success
    assert upgrades == []
    assert len(launches) == (2 if success else 1)
    assert statuses[0][2] == ('ready' if success else 'error')


def test_memory_failure_reaches_project_response_without_manifest(tmp_path):
    """A failed extraction has no manifest, but polling must still expose why."""
    runner, launches, _, _ = _runner([
        'numpy.core._exceptions._ArrayMemoryError: Unable to allocate 73.8 MiB'])
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript('''
            CREATE TABLE projects (project_id TEXT, geom_status TEXT, updated_at TEXT, error_message TEXT);
            CREATE TABLE result_groups (
                project_id TEXT, result_group TEXT, display_name TEXT, status TEXT,
                error_message TEXT, updated_at TEXT, parse_options TEXT,
                original_source_file TEXT, source_file TEXT,
                original_source_path TEXT, source_path TEXT
            );
            INSERT INTO projects VALUES ('589', 'running', '', NULL);
            INSERT INTO result_groups VALUES (
                '589', 'default_result', 'default', 'pending', NULL, '', NULL,
                NULL, 'source.odb', NULL, 'source.odb'
            );
        ''')
        runner.update({'_connect': lambda: conn, '_now_iso': lambda: 'now'})
        _load_functions('src/job_runner.py', {'_update_project_geom_status'}, runner)
        odb = tmp_path / 'source.odb'
        odb.touch()
        assert runner['_run_odb_project']('589', str(odb), str(tmp_path)) is False
        assert len(launches) == 1
        project = dict(conn.execute('SELECT * FROM projects').fetchone())
        project.update({'workspace': str(tmp_path), 'source_type': 'odb',
                        'original_source_file': None, 'source_file': 'source.odb',
                        'original_inp_path': None, 'inp_path': str(odb)})
        repo = SimpleNamespace(list_result_groups=lambda _: conn.execute(
            'SELECT * FROM result_groups').fetchall())
        api = _load_functions('src/l3/api/routes/projects.py',
                              {'_build_project_response'}, {
            'os': os, 'json': json, '_resolve_workspace': lambda path, _: path,
            'ManifestRepo': ManifestRepo,
        })
        response = api['_build_project_response'](project, repo)
        assert response['geom_status'] == 'error'
        assert '内存不足' in response['error_message']
        group = response['result_groups'][0]
        assert group['status'] == 'error'
        assert '内存不足' in group['error_message']
        assert '已停止后续打包' in group['error_message']
        assert 'dump_meta.json not found' not in group['error_message']
        assert not (tmp_path / 'manifest.db').exists()
    finally:
        conn.close()
