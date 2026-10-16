"""Run two pinned ODB pipelines and sample their process-tree memory on Windows.

Uses private input copies and snapshots of tracked scripts; never upgrades the
source ODB or changes the working checkout. Requires psutil, h5py and NumPy in
the host interpreter, plus an installed Abaqus command.
"""
import argparse
from collections import deque
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time

import h5py
import numpy as np
import psutil


REPO = Path(__file__).resolve().parents[1]
LOCAL_TZ = timezone(timedelta(hours=8))


def now():
    return datetime.now(LOCAL_TZ).isoformat(timespec='seconds')


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(ref, target):
    commit = subprocess.check_output(['git', 'rev-parse', ref], cwd=REPO, text=True).strip()
    files = ['src/l1/abaqus_dump.py', 'src/l1/l1_pack.py',
             'src/l1/manifest_schema.py', 'src/l2/ingest.py']
    blobs = {}
    for name in files:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(['git', 'show', commit + ':' + name], cwd=REPO))
        blobs[name] = subprocess.check_output(
            ['git', 'rev-parse', commit + ':' + name], cwd=REPO, text=True).strip()
    return {'commit': commit, 'files': blobs, 'snapshot': str(target)}


def stop_tree(process):
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.Error:
            pass
    if process.poll() is None:
        process.kill()


def run_stage(root, version, name, command, cwd, interval, timeout):
    """Working-set sums may double count shared pages; private bytes don't."""
    folder = root / version
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / (name + '.log')
    samples_path = folder / (name + '_memory.csv')
    started = time.perf_counter()
    record = {'version': version, 'stage': name, 'command': list(map(str, command)),
              'cwd': str(cwd), 'started_at': now(), 'log': str(log_path),
              'samples': str(samples_path), 'peak_tree_rss_bytes': 0,
              'peak_tree_private_bytes': 0, 'peak_process_os_wset_bytes': 0,
              'peak_process_os_pagefile_bytes': 0, 'min_host_available_bytes': None,
              'sample_interval_seconds': interval}
    print(json.dumps({'event': 'stage_start', **record}, ensure_ascii=False), flush=True)
    tail, latest = deque(maxlen=60), ['']
    signals = {'error': False, 'complete': False, 'memory_error': False}
    with log_path.open('w', encoding='utf-8', buffering=1) as log, samples_path.open(
            'w', encoding='utf-8', newline='', buffering=1) as sample_file:
        process = subprocess.Popen(list(map(str, command)), cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                   errors='replace', bufsize=1,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        tracked = {}
        def read_output():
            for line in process.stdout:
                log.write(line)
                clean = line.rstrip()
                if clean:
                    tail.append(clean)
                    latest[0] = clean
                    signals['error'] |= clean.startswith(('ODB_DUMP_FAILED:', '!!! ERROR', 'Traceback'))
                    signals['memory_error'] |= 'MemoryError:' in clean or clean == 'MemoryError'
                    signals['complete'] |= clean == 'ODB_DUMP_COMPLETE'
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        writer = csv.writer(sample_file)
        writer.writerow(['timestamp', 'elapsed_seconds', 'tree_rss_bytes', 'tree_private_bytes',
                         'host_available_bytes', 'live_pids', 'latest_log'])
        last_status = last_print = -10.0
        timed_out = False
        while True:
            elapsed = time.perf_counter() - started
            try:
                parent = psutil.Process(process.pid)
                for child in [parent] + parent.children(recursive=True):
                    tracked.setdefault(child.pid, child)
            except psutil.Error:
                pass
            rss = private = 0
            live = []
            for pid, child in list(tracked.items()):
                try:
                    if not child.is_running():
                        continue
                    memory = child.memory_info()
                    rss += memory.rss
                    private += getattr(memory, 'private', memory.vms)
                    record['peak_process_os_wset_bytes'] = max(
                        record['peak_process_os_wset_bytes'], getattr(memory, 'peak_wset', memory.rss))
                    record['peak_process_os_pagefile_bytes'] = max(
                        record['peak_process_os_pagefile_bytes'], getattr(memory, 'peak_pagefile', memory.vms))
                    live.append(pid)
                except psutil.Error:
                    pass
            available = psutil.virtual_memory().available
            record['peak_tree_rss_bytes'] = max(record['peak_tree_rss_bytes'], rss)
            record['peak_tree_private_bytes'] = max(record['peak_tree_private_bytes'], private)
            record['min_host_available_bytes'] = min(
                available, record['min_host_available_bytes'] or available)
            writer.writerow([now(), round(elapsed, 3), rss, private, available,
                             ';'.join(map(str, live)), latest[0]])
            progress = {**record, 'elapsed_seconds': round(elapsed, 3), 'latest_log': latest[0],
                        'current_tree_rss_bytes': rss, 'current_tree_private_bytes': private,
                        'host_available_bytes': available, 'live_pids': live}
            if elapsed - last_status >= 5:
                save_json(root / 'status.json', progress)
                last_status = elapsed
            if elapsed - last_print >= 45:
                print(json.dumps({'event': 'progress', **progress}, ensure_ascii=False), flush=True)
                last_print = elapsed
            if process.poll() is not None and not reader.is_alive():
                break
            if elapsed >= timeout:
                timed_out = True
                stop_tree(process)
                break
            time.sleep(interval)
        reader.join(timeout=5)
        process.wait(timeout=10)
    record.update({'elapsed_seconds': time.perf_counter() - started, 'finished_at': now(),
                   'returncode': process.returncode, 'timed_out': timed_out,
                   'tail': list(tail), 'signals': signals})
    record['ok'] = process.returncode == 0 and not timed_out and not signals['error']
    save_json(folder / (name + '_summary.json'), record)
    save_json(root / 'status.json', record)
    print(json.dumps({'event': 'stage_end', **record}, ensure_ascii=False), flush=True)
    return record


def digest_h5(path):
    datasets = {}
    with h5py.File(path, 'r') as h5:
        def visit(name, item):
            if not isinstance(item, h5py.Dataset):
                return
            digest = hashlib.sha256()
            if item.dtype.kind in 'OSU':
                digest.update(repr(item[()]).encode('utf-8'))
            elif not item.shape:
                digest.update(np.asarray(item[()]).tobytes())
            else:
                row_bytes = max(1, int(np.prod(item.shape[1:])) * item.dtype.itemsize)
                rows = max(1, (8 * 1024 * 1024) // row_bytes)
                for start in range(0, item.shape[0], rows):
                    digest.update(np.ascontiguousarray(item[start:start + rows]).tobytes())
            datasets[name] = {'shape': list(item.shape), 'dtype': str(item.dtype),
                              'sha256': digest.hexdigest()}
        h5.visititems(visit)
    return datasets


def compare_outputs(before, after, root):
    left = {p.relative_to(before).as_posix(): p for p in before.rglob('*.h5')}
    right = {p.relative_to(after).as_posix(): p for p in after.rglob('*.h5')}
    report = {'files_before': len(left), 'files_after': len(right), 'files': {}, 'equal': True}
    for name in sorted(left.keys() | right.keys()):
        if name not in left or name not in right:
            info = {'equal': False, 'missing': 'before' if name not in left else 'after'}
        else:
            first, second = digest_h5(left[name]), digest_h5(right[name])
            info = {'equal': first == second, 'datasets_before': len(first),
                    'datasets_after': len(second), 'different_datasets': [
                        key for key in sorted(first.keys() | second.keys())
                        if first.get(key) != second.get(key)]}
            if not info['equal']:
                info.update({'before': first, 'after': second})
        report['files'][name] = info
        report['equal'] &= info['equal']
        save_json(root / 'output_comparison.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--before', default='982b82a')
    parser.add_argument('--after', default='HEAD')
    parser.add_argument('--abaqus', default=r'C:\SIMULIA\Commands\abaqus.bat')
    parser.add_argument('--output', required=True)
    parser.add_argument('--interval', type=float, default=0.25)
    parser.add_argument('--stage-timeout', type=int, default=10800)
    args = parser.parse_args()
    source, root = Path(args.source).resolve(), Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    run = {'started_at': now(), 'source': str(source), 'source_bytes': source.stat().st_size,
           'source_mtime_ns': source.stat().st_mtime_ns, 'source_sha256': sha256(source),
           'host': {'platform': platform.platform(), 'cpu': platform.processor(),
                    'memory': psutil.virtual_memory()._asdict(),
                    'disk': shutil.disk_usage(root)._asdict(), 'python': sys.executable,
                    'psutil': psutil.__version__, 'numpy': np.__version__, 'h5py': h5py.__version__,
                    'thread_env': {k: os.environ.get(k) for k in
                                   ['OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS']}},
           'method': {'sampling_interval_seconds': args.interval, 'order': ['before', 'after'],
                      'invariants': 'full', 'all_steps_and_frames': True,
                      'upgrade_and_copy_excluded_from_pipeline_time': True,
                      'api_warmup_and_model_preloading': 'not started',
                      'os_file_cache_flushed': False, 'mysql_and_http_queue_excluded': True},
           'versions': {}}
    for label, ref in [('before', args.before), ('after', args.after)]:
        run['versions'][label] = snapshot(ref, root / label / 'source')
    save_json(root / 'run.json', run)
    prepare = root / 'prepare'
    prepare.mkdir()
    private_source = prepare / 'original_copy.odb'
    shutil.copy2(source, private_source)
    upgrade = run_stage(root, 'prepare', 'upgrade',
                        [args.abaqus, '-upgrade', '-job', 'upgraded_model', '-odb', private_source],
                        prepare, args.interval, args.stage_timeout)
    upgraded = prepare / 'upgraded_model.odb'
    run['preparation'] = upgrade
    if not upgrade['ok'] or not upgraded.exists():
        run['error'] = 'Private ODB upgrade failed'
        save_json(root / 'run.json', run)
        raise SystemExit(1)
    input_hash = sha256(upgraded)
    run['upgraded_sha256'] = input_hash
    for label in ['before', 'after']:
        version = run['versions'][label]
        folder = root / label
        odb = folder / 'input.odb'
        copy_start = time.perf_counter()
        shutil.copy2(upgraded, odb)
        version['input_copy_seconds'] = time.perf_counter() - copy_start
        version['input_sha256'] = sha256(odb)
        if version['input_sha256'] != input_hash:
            raise RuntimeError('Private input copy hash mismatch')
        workspace = folder / 'workspace'
        workspace.mkdir()
        version['workspace'] = str(workspace)
        snapshot_root = Path(version['snapshot'])
        commands = [
            ('dump', [args.abaqus, 'python', snapshot_root / 'src/l1/abaqus_dump.py',
                      '--odb', odb, '--out', workspace, '--invariants', 'full']),
            ('pack', [sys.executable, snapshot_root / 'src/l1/l1_pack.py', '--workspace', workspace]),
            ('l2', [sys.executable, snapshot_root / 'src/l2/ingest.py', '--workspace', workspace]),
        ]
        version['stages'] = []
        for stage, command in commands:
            result = run_stage(root, label, stage, command, folder, args.interval, args.stage_timeout)
            if stage == 'dump':
                result['fresh_metadata_present'] = (workspace / 'l1_raw/dump_meta.json').is_file()
                result['ok'] &= result['fresh_metadata_present']
                if label == 'after':
                    result['ok'] &= result['signals']['complete']
            version['stages'].append(result)
            save_json(root / 'run.json', run)
            if not result['ok']:
                break
        version['ok'] = len(version['stages']) == 3 and all(s['ok'] for s in version['stages'])
        version['pipeline_seconds'] = sum(s['elapsed_seconds'] for s in version['stages'])
        version['peak_tree_rss_bytes'] = max(s['peak_tree_rss_bytes'] for s in version['stages'])
        version['peak_tree_private_bytes'] = max(s['peak_tree_private_bytes'] for s in version['stages'])
        version['input_unchanged'] = sha256(odb) == input_hash
        save_json(root / 'run.json', run)
    run['source_unchanged'] = (source.stat().st_size == run['source_bytes'] and
                               source.stat().st_mtime_ns == run['source_mtime_ns'] and
                               sha256(source) == run['source_sha256'])
    if all(v['ok'] for v in run['versions'].values()):
        save_json(root / 'status.json', {'stage': 'comparing_outputs', 'started_at': now()})
        run['comparison'] = compare_outputs(
            Path(run['versions']['before']['workspace']),
            Path(run['versions']['after']['workspace']), root)
    run['finished_at'] = now()
    run['ok'] = (all(v['ok'] and v['input_unchanged'] for v in run['versions'].values())
                 and run['source_unchanged'] and run.get('comparison', {}).get('equal', False))
    save_json(root / 'run.json', run)
    save_json(root / 'status.json', {'stage': 'finished', 'ok': run['ok'], 'finished_at': now()})
    print(json.dumps({'event': 'finished', 'ok': run['ok'], 'output': str(root)}, ensure_ascii=False), flush=True)
    return 0 if run['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
