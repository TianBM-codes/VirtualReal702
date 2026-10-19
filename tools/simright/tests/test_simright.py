import copy
import json
import math
from pathlib import Path

import pytest
import requests

from tools.simright import (SimRightClient, SimRightError, match_sensors, project_node_history,
                           project_strain, project_stress, sensor_history, sensor_values)
from tools.simright.__main__ import main
from tools.simright.generate_artifacts import (DEFAULT_OUT, EXAMPLE_ARGS, build_openapi,
                                               build_postman, build_reference)


class Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class Session:
    def __init__(self, bodies):
        self.bodies = iter(bodies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        body = next(self.bodies)
        if isinstance(body, Exception):
            raise body
        return Response(body)

    def close(self):
        pass


@pytest.mark.parametrize('profile,path,key', [
    ('markdown', '/applications/3dlite/api/v1/query', 'filename'),
    ('excel', '/applications/3dlite/api/v1/query/query', 'fileName'),
])
def test_wire_profiles(profile, path, key):
    session = Session([{'code': 0, 'data': [{'nodeId': 7}], 'message': 'ok'}])
    client = SimRightClient('http://localhost:8080/', profile=profile, session=session,
                            headers={'Authorization': 'test'}, timeout=13)
    assert client.node_ids('1-0', [[1, 2, 3]]) == [{'nodeId': 7}]
    method, url, kwargs = session.calls[0]
    assert method == 'POST'
    assert url == 'http://localhost:8080' + path
    assert kwargs == {'timeout': 13, 'headers': {'Authorization': 'test'},
                      'json': {'name': 'nodeId', 'args': {key: '1-0', 'nodePosList': [[1, 2, 3]]}}}


@pytest.mark.parametrize('body', [
    {'code': 12, 'data': None, 'message': 'failure'},
    {'code': 200, 'data': {}}, {'code': False, 'data': {}},
    {'code': 0}, {'data': {}}, [],
])
def test_native_envelope_errors(body):
    with pytest.raises(SimRightError):
        SimRightClient('http://localhost', session=Session([body])).loadcases('x')


def test_local_code_requires_explicit_configuration_and_override():
    session = Session([{'code': 200, 'data': 4}])
    api = SimRightClient('http://localhost', profile='excel', query_path='/custom',
                         filename_key='filename', success_codes=(200,), session=session)
    assert api.query('loadcases', fileName='x') == 4
    assert session.calls[0][1] == 'http://localhost/custom'
    assert session.calls[0][2]['json']['args'] == {'filename': 'x'}
    with pytest.raises(ValueError, match='disagree'):
        api.query('loadcases', filename='x', fileName='y')


def test_model_diff_no_filename_and_conversion_raw_response():
    session = Session([{'code': 0, 'data': []}, {'job': 3}, {'status': 'unknown'},
                       {'code': 1, 'id': 'session-id'}])
    api = SimRightClient('http://localhost', session=session)
    assert api.query('modelDiff', rdb1={'rdbPath': 'a'}, rdb2={'rdbPath': 'b'}) == []
    assert api.submit_conversion('/home/a.odb', '1-0') == {'job': 3}
    assert session.calls[1][2]['json'] == {'modelPath': '/home/a.odb', 'uuid': '1-0'}
    assert api.conversion_status('1-0') == {'status': 'unknown'}
    assert session.calls[2][0] == 'GET'
    assert session.calls[2][2]['params'] == {'uuid': '1-0'}
    assert api.create_session([{'id': '1', 'path': 'x'}])['code'] == 1


def test_http_error_not_retried():
    session = Session([requests.Timeout('timeout')])
    with pytest.raises(SimRightError, match='timeout'):
        SimRightClient('http://localhost', session=session).loadcases('x')
    assert len(session.calls) == 1


def test_stress_analytic_traction_and_sign():
    s = {'11': 100, '22': 40, '33': 10, '12': 10, '13': 0, '23': 0}
    result = project_stress(s, [1, 1, 0])
    assert result['normal_stress'] == pytest.approx(80)
    assert result['shear_stress'] == pytest.approx(30)
    assert result['traction'] == pytest.approx([110 / math.sqrt(2), 50 / math.sqrt(2), 0])
    n = result['direction']
    assert sum(x*y for x, y in zip(n, result['shear_traction'])) == pytest.approx(0, abs=1e-12)
    opposite = project_stress(s, [-1, -1, 0])
    assert opposite['normal_stress'] == pytest.approx(result['normal_stress'])
    assert opposite['traction'] == pytest.approx([-x for x in result['traction']])
    compression = project_stress({'11': -100, '22': -100, '33': -100, '12': 0, '13': 0, '23': 0}, [1, 2, 3])
    assert compression['normal_stress'] == pytest.approx(-100)
    assert compression['shear_stress'] == pytest.approx(0, abs=1e-12)


def test_all_shear_components_contribute_in_3d():
    s = {'11': 1, '22': 2, '33': 3, '12': 4, '13': 5, '23': 6}
    assert project_stress(s, [1, 1, 1])['normal_stress'] == pytest.approx(12)


def test_strain_engineering_vs_tensor():
    e = {'11': .001, '22': .0002, '33': 0, '12': .0008, '13': 0, '23': 0}
    assert project_strain(e, [1, 1, 0], shear_convention='engineering')['axial_strain'] == pytest.approx(.001)
    assert project_strain(e, [1, 1, 0], shear_convention='tensor')['axial_strain'] == pytest.approx(.0014)
    with pytest.raises(ValueError):
        project_strain(e, [1, 0, 0], shear_convention='auto')


def test_rotation_columns_and_invalid_frame():
    s = {'11': 100, '22': 40, '33': 10, '12': 0, '13': 0, '23': 0}
    r = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    assert project_stress(s, [0, 1, 0], local_to_global=r)['normal_stress'] == pytest.approx(100)
    for invalid in ([[2, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 1, 0], [0, 0, 1]]):
        with pytest.raises(ValueError):
            project_stress(s, [1, 0, 0], local_to_global=invalid)


@pytest.mark.parametrize('direction', [[0, 0, 0], [1, 2], [float('nan'), 0, 0]])
def test_bad_direction_and_missing_component(direction):
    components = dict.fromkeys(('11', '22', '33', '12', '13', '23'), 0)
    with pytest.raises(ValueError):
        project_stress(components, direction)
    with pytest.raises(ValueError, match='Missing'):
        project_stress({'11': 1}, [1, 0, 0])


class MatchingAPI:
    def __init__(self, candidates):
        self.candidates = iter(candidates)
        self.positions = []

    def node_ids(self, filename, positions, **options):
        self.positions.append(positions)
        return next(self.candidates)


def test_sensor_matches_keep_order_duplicates_rejections_and_tolerance():
    api = MatchingAPI([[{'nodeId': 7, 'nodePos': [0, 0, 0]}],
                       [{'nodeId': 7, 'nodePos': [0, 0, 0]}],
                       [{'nodeId': 8, 'nodePos': [9, 9, 9]}], [],
                       [{'nodeId': 9, 'nodePos': [0, 0, 0]}, {'nodeId': 10, 'nodePos': [0, 0, 0]}]])
    sensors = [{'sensorId': i, 'position': [0, 0, 0]} for i in range(5)]
    matches = match_sensors(api, 'x', sensors, max_distance=0, stateId=1)
    assert [r['matched'] for r in matches] == [True, True, False, False, False]
    assert [r['sensorId'] for r in matches] == list(range(5))
    assert matches[0]['partId'] is None
    assert matches[2]['reason'] == 'distance_exceeded'
    assert matches[3]['reason'] == 'no_candidate'
    assert matches[4]['reason'] == 'ambiguous_candidates'
    assert api.positions == [[[0., 0., 0.]]] * 5
    assert matches[0]['matchOptions'] == {'stateId': 1}
    with pytest.raises(ValueError, match='unique'):
        match_sensors(api, 'x', [sensors[0], sensors[0]], max_distance=1)


def test_node_values_use_original_id_and_preserve_corner_values():
    class API:
        def node_info(self, filename, ids, var, **opts):
            assert opts['entitiesIds'] == [42]  # oriId, not internal partId=3
            return [{'nodeId': 7, 'partId': 3, 'stateId': 1, 'values': [1, 2, 3]}]
    matches = [{'sensorId': 'a', 'matched': True, 'nodeId': 7, 'partId': 3},
               {'sensorId': 'b', 'matched': False, 'reason': 'distance_exceeded'}]
    result = sensor_values(API(), 'x', matches, 'S.EL.11', part_ori_ids={3: 42})
    assert result[0]['results'][0]['values'] == [1, 2, 3]
    assert result[1] == matches[1]
    with pytest.raises(ValueError, match='oriId'):
        sensor_values(API(), 'x', matches, 'S.EL.11')
    with pytest.raises(ValueError, match='does not document'):
        sensor_history(API(), 'x', matches, 'S.EL.11', part_ori_ids={3: 42})


def test_unknown_part_ambiguity_rejected():
    class API:
        def node_info(self, *a, **k):
            return [{'nodeId': 7, 'partId': 1}, {'nodeId': 7, 'partId': 2}]
    with pytest.raises(SimRightError, match='ambiguous'):
        sensor_values(API(), 'x', [{'sensorId': 'a', 'matched': True, 'nodeId': 7}], 'S.EL.11')


class HistoryAPI:
    def __init__(self, modify=None):
        self.calls = []
        self.modify = modify

    def xy_curves(self, filename, node_ids, var_name, **options):
        self.calls.append((filename, node_ids, var_name, options))
        values = {'11': 100, '22': 40, '33': 10, '12': 10, '13': 0, '23': 0}
        data = [{'nodeId': n, 'partId': 3, 'xVarName': 'var_time',
                 'data': [[0, values[var_name.split('.')[-1]]], [1, 2*values[var_name.split('.')[-1]]]]}
                for n in reversed(node_ids)]
        return self.modify(var_name, data) if self.modify else data


def test_history_aligns_by_identity_not_response_order():
    def modify(var, data):
        return list(reversed(data)) if var.endswith('12') else data
    api = HistoryAPI(modify)
    result = project_node_history(api, 'x', [7, 8], [1, 1, 0],
                                  stateIds=[1, 2], loadcaseName='Step-1', coordSystemType=1)
    assert {r['nodeId'] for r in result} == {7, 8}
    for row in result:
        assert [s['normal_stress'] for s in row['samples']] == pytest.approx([80, 160])
    assert len(api.calls) == 6
    assert all(c[3] == {'stateIds': [1, 2], 'loadcaseName': 'Step-1', 'coordSystemType': 1} for c in api.calls)


@pytest.mark.parametrize('failure', ['shift', 'short', 'empty', 'duplicate', 'nan', 'missing-node', 'different-part', 'repeat-x', 'different-axis'])
def test_history_fails_instead_of_mixing_states(failure):
    def modify(var, data):
        if not var.endswith('23'):
            return data
        if failure == 'shift':
            data[0]['data'][1][0] = 1.1
        elif failure == 'short':
            data[0]['data'].pop()
        elif failure == 'empty':
            data[0]['data'] = []
        elif failure == 'duplicate':
            data.append(copy.deepcopy(data[0]))
        elif failure == 'nan':
            data[0]['data'][0][1] = float('nan')
        elif failure == 'missing-node':
            data.pop()
        elif failure == 'different-part':
            data[0]['partId'] = 999
        elif failure == 'repeat-x':
            data[0]['data'][1][0] = 0
        else:
            data[0]['xVarName'] = 'var_frequency'
        return data
    with pytest.raises(SimRightError):
        project_node_history(HistoryAPI(modify), 'x', [7], [1, 0, 0])


def test_explicit_2d_zero_assumption_and_strain_validation():
    api = HistoryAPI()
    result = project_node_history(api, 'x', [7], [1, 1, 0], zero_components=['33', '13', '23'])
    assert len(api.calls) == 3
    assert result[0]['assumedZeroComponents'] == ['13', '23', '33']
    assert result[0]['samples'][0]['normal_stress'] == pytest.approx(80)
    with pytest.raises(ValueError, match='shear_convention'):
        project_node_history(api, 'x', [7], [1, 0, 0], kind='strain')


def test_strain_history_and_custom_variable_mapping():
    api = HistoryAPI()
    variables = {c: 'custom.' + c for c in ('11', '22', '33', '12', '13', '23')}
    result = project_node_history(api, 'x', [7], [1, 1, 0], kind='strain',
                                  component_vars=variables, shear_convention='engineering')
    assert {c[2] for c in api.calls} == set(variables.values())
    assert result[0]['samples'][0]['axial_strain'] == pytest.approx(75)
    assert result[0]['samples'][1]['axial_strain'] == pytest.approx(150)


def test_generated_artifacts_are_reproducible_and_cover_all_operations():
    catalog = json.loads((DEFAULT_OUT / 'source-catalog.json').read_text(encoding='utf-8'))
    spec = build_openapi(catalog)
    assert spec == json.loads((DEFAULT_OUT / 'simright.openapi.json').read_text(encoding='utf-8'))
    assert build_postman(catalog) == json.loads((DEFAULT_OUT / 'simright.postman_collection.json').read_text(encoding='utf-8'))
    assert build_reference(catalog) == (DEFAULT_OUT / 'API-reference.md').read_text(encoding='utf-8')
    assert {o['name'] for o in catalog['operations']} == set(EXAMPLE_ARGS)
    assert len(catalog['excelRows']) == 13
    assert len(spec['paths']) == 5
    for operation in catalog['operations']:
        assert not {'code', 'data', 'message'} & {f['name'] for f in operation['fields']}
    assert [f['name'] for f in next(o for o in catalog['operations']
                                  if o['name'] == 'deleteModelFile')['fields']] == ['filename']
    # JSON Pointer references must resolve within the document.
    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                target = spec
                for key in value['$ref'][2:].split('/'):
                    target = target[key]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(spec)


def test_openapi_is_valid_when_validator_installed():
    validator = pytest.importorskip('openapi_spec_validator')
    spec = json.loads((DEFAULT_OUT / 'simright.openapi.json').read_text(encoding='utf-8'))
    validator.validate_spec(spec)


def test_offline_cli_and_json_output(tmp_path):
    output = tmp_path / 'projection.json'
    assert main(['--output', str(output), 'project', '--input',
                 str(DEFAULT_OUT / 'examples/stress-projection.json')]) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['normal_stress'] == pytest.approx(80)
