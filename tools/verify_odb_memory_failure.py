"""Inject ODB memory errors in Abaqus and verify the real runner/HTTP responses.

Uses a fresh registry/workspace and a temporary extraction driver. Never opens an
ODB or allocates a huge array. Production extraction and API code stay unchanged.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]

CHILD_SOURCE = '''import json
import os
import runpy
import sys

config = json.loads(os.environ['ODB_OOM_VERIFY_CONFIG'])
namespace = runpy.run_path(config['dump_script'], run_name='odb_oom_verification')
entry = namespace['_main_checked']

def fail():
    import numpy as np
    print('SIMULATED_ODB_MEMORY_FAILURE: ' + config['kind'])
    print('NUMPY_VERSION: ' + np.__version__)
    if config['kind'] == 'numpy':
        from numpy.core._exceptions import _ArrayMemoryError
        raise _ArrayMemoryError((9674696,), np.dtype('int64'))
    raise MemoryError('simulated insufficient memory during ODB extraction')

entry.__globals__['main'] = fail
try:
    entry()
except SystemExit:
    if config['force_zero']:
        print('SIMULATED_LAUNCHER_EXIT_ZERO')
        sys.exit(0)
    raise
'''


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New, isolated output directory')
    parser.add_argument('--abaqus', default=r'C:\SIMULIA\Commands\abaqus.bat')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry_path = output / 'registry.db'
    # Set these before importing either the runner or API settings singleton.
    os.environ.update({
        'APP_REGISTRY_DB_PATH': str(registry_path), 'APP_DATA_ROOT': str(output),
        'APP_PRELOAD_PROJECTS': '0', 'APP_EMBEDDED_RUNNER': '0', 'APP_WARMUP': '0',
    })
    sys.path.insert(0, str(ROOT))
    import requests
    import uvicorn
    from fastapi import FastAPI
    from src import job_runner as runner
    from src.l3.api.routes import projects
    from src.l3.core.state import ModelIndex
    from src.l3.infra.registry_repo import RegistryRepo

    repo = RegistryRepo(str(registry_path))
    assert Path(runner.REGISTRY_DB).resolve() == registry_path
    assert Path(projects.settings.registry_db_path).resolve() == registry_path
    log_handler = logging.FileHandler(output / 'runner.log', encoding='utf-8')
    runner.logger.addHandler(log_handler)
    driver = output / 'fault_driver.py'
    driver.write_text(CHILD_SOURCE, encoding='utf-8')
    runner.DUMP_SCRIPT = driver
    runner.ABAQUS_CMD = args.abaqus
    launches = []
    real_popen = subprocess.Popen

    def tracked_popen(command, **kwargs):
        process = real_popen(command, **kwargs)
        launches.append({'command': command, 'process': process})
        return process

    # Observe real child processes; no subprocess output or status is mocked.
    runner.subprocess = SimpleNamespace(Popen=tracked_popen, PIPE=subprocess.PIPE,
                                       STDOUT=subprocess.STDOUT)
    app = FastAPI()
    app.include_router(projects.router)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', lifespan='off',
                                          loop='asyncio', http='h11', ws='none'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    print('Starting isolated HTTP server on 127.0.0.1:' + str(port), flush=True)
    client = requests.Session()
    client.trust_env = False
    results = []
    try:
        deadline = time.monotonic() + 40
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                frame = sys._current_frames().get(thread.ident)
                if frame is not None:
                    (output / 'startup_stack.txt').write_text(
                        ''.join(traceback.format_stack(frame)), encoding='utf-8')
                raise RuntimeError('Isolated HTTP server did not start')
            time.sleep(.05)
        cases = [
            ('project_builtin', 'builtin', False, False),
            ('project_numpy', 'numpy', False, False),
            ('project_numpy_zero', 'numpy', True, False),
            ('append_numpy_zero', 'numpy', True, True),
        ]
        for name, kind, force_zero, append in cases:
            started = time.perf_counter()
            workspace = output / name
            workspace.mkdir()
            source = workspace / 'simulated.odb'
            source.touch()  # Exists for the runner preflight; never opened as ODB.
            repo.create_project(name, str(workspace), inp_path=str(source),
                                source_file=source.name, source_type='odb')
            repo.create_result_group(name, 'default_result', '模拟内存不足', str(source),
                                     source.name, str(source), source.name, '{}')
            repo.update_project_geom_status(name, 'ready' if append else 'running')
            if append:
                # Represent an already resident geometry; this case tests only
                # appending results and keeps the geometry state ready.
                index = ModelIndex(name, str(workspace))
                index.is_render_ready = True
                projects.registry.loaded[name] = index
                with repo._connect() as conn:
                    conn.execute("UPDATE result_groups SET status='running' WHERE project_id=?",
                                 (name,))
            os.environ['ODB_OOM_VERIFY_CONFIG'] = json.dumps({
                'dump_script': str(ROOT / 'src/l1/abaqus_dump.py'),
                'kind': kind, 'force_zero': force_zero,
            })
            before = len(launches)
            if append:
                ok = runner._run_result_group(name, 'default_result', str(source),
                                              '{}', str(workspace))
            else:
                ok = runner._run_odb_project(name, str(source), str(workspace))
            stage_launches = launches[before:]
            assert ok is False
            assert len(stage_launches) == 1, 'Packing or another downstream stage was launched'
            if force_zero:
                assert stage_launches[0]['process'].returncode == 0
            responses = {}
            for endpoint in [f'/api/projects/{name}', '/api/projects',
                             f'/api/projects/{name}/result-catalog']:
                response = client.get(f'http://127.0.0.1:{port}{endpoint}', timeout=10)
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload['code'] == 200
                responses[endpoint] = payload
                data = payload['data']
                if endpoint == '/api/projects':
                    data = next(p for p in data if p['project_id'] == name)
                assert data['geom_status'] == ('ready' if append else 'error')
                group = data['result_groups'][0]
                assert group['status'] == 'error'
                message = group['error_message']
                assert 'ODB 提取内存不足' in message and '已停止后续打包' in message
                assert 'dump_meta.json not found' not in message
                assert 'consistency check failed' not in message
            assert not (workspace / 'manifest.db').exists()
            with repo._connect() as conn:
                rows = [dict(row) for row in conn.execute(
                    'SELECT * FROM job_logs WHERE odb_id=? ORDER BY id', (name,))]
            assert not any(row['stage'] in ('l1_pack', 'rg_l1_pack') for row in rows)
            assert not any(row['message'] == 'ODB_DUMP_COMPLETE' for row in rows)
            assert any('ODB_DUMP_FAILED:' in row['message'] for row in rows)
            case = {
                'case': name, 'kind': kind, 'force_launcher_zero': force_zero,
                'elapsed_seconds': time.perf_counter() - started,
                'subprocess_count': len(stage_launches),
                'actual_launcher_returncode': stage_launches[0]['process'].returncode,
                'downstream_pack_started': False, 'responses': responses,
                'logs': rows, 'ok': True,
            }
            save(output / (name + '.json'), case)
            results.append(case)
            print(json.dumps({key: value for key, value in case.items()
                              if key not in ('responses', 'logs')}, ensure_ascii=False), flush=True)
        save(output / 'summary.json', {
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                              text=True).strip(),
            'method': 'Injected exceptions in real Abaqus Python; actual runner, SQLite and HTTP API',
            'original_odb_opened': False, 'large_array_allocated': False,
            'http_port': port, 'registry': str(registry_path), 'cases': results, 'ok': True,
        })
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        client.close()
        runner.logger.removeHandler(log_handler)
        log_handler.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
