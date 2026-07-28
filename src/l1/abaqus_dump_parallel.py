#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
abaqus_dump_parallel.py — Layer 1 Phase 1 的并行启动器

用法（在 abaqus python 里运行，不是普通 python）：
    abaqus python src/l1/abaqus_dump_parallel.py \
        --odb /path/to/model.odb \
        --out /path/to/workspace \
        --workers 4

工作流程：
  1. 用 sys.executable（当前 Abaqus Python）调用 abaqus_dump.py --mode preflight
     → dump assembly/geom/sets + 扫描所有 step/field 名，写 fields_manifest.json
  2. 按 step 将 field 列表分配到 N 个 worker，并行启动
     每个 worker: sys.executable abaqus_dump.py --mode results-worker --step X --fields ...
  3. 等待所有 worker 完成，打印汇总

注意：
  - 必须在 abaqus python 里运行（子进程会继承相同的解释器和 odbAccess 路径）
  - 每个 worker 占一个 Abaqus license token；--workers 不要超过可用 token 数
  - Worker 日志写到 <workspace>/l1_raw/worker_<id>.log
"""

from __future__ import print_function

import argparse
import json
import math
import os
import subprocess
import sys
import time


def parse_args():
    p = argparse.ArgumentParser(
        description='Parallel launcher for abaqus_dump.py Phase 1')
    p.add_argument('--odb',      required=True, help='Path to .odb file')
    p.add_argument('--out',      required=True, help='Workspace directory')
    p.add_argument('--workers',  type=int, default=4,
                   help='Max parallel workers (default: 4)')
    p.add_argument('--script',   default=None,
                   help='Path to abaqus_dump.py (default: same directory as this script)')
    p.add_argument('--no-preflight', action='store_true',
                   help='Skip preflight step (assembly/geom already dumped); '
                        'fields_manifest.json must already exist')
    return p.parse_args()


def fmt_t(secs):
    if secs >= 60:
        return '{:d}m {:.1f}s'.format(int(secs) // 60, secs % 60)
    return '{:.1f}s'.format(secs)


def chunk_list(lst, n):
    """Split lst into up to n roughly equal chunks."""
    if not lst:
        return []
    size = int(math.ceil(len(lst) / float(n)))
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def group_fields_for_workers(fields):
    """Group region-qualified fields by base name so one logical field never
    splits across workers.

    Contact outputs are stored per contact pair, e.g.
    'CPRESS   ASSEMBLY_S_SET-3_CNS_/ASSEMBLY_M_SURF-1'; abaqus_dump.py merges
    all pairs sharing a base name into ONE field directory (see
    split_region_field there). If two workers each got one pair, both would
    write the same directory and corrupt it — keep the group on one worker.
    """
    units = {}
    order = []
    for fname in fields:
        parts = fname.split(None, 1)
        base = fname
        if len(parts) == 2 and ('/' in parts[1] or parts[1].startswith('ASSEMBLY')):
            base = parts[0]
        if base not in units:
            units[base] = []
            order.append(base)
        units[base].append(fname)
    return [units[b] for b in order]


def main():
    args = parse_args()

    odb_path  = os.path.abspath(args.odb)
    workspace = os.path.abspath(args.out)
    raw_dir   = os.path.join(workspace, 'l1_raw')
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dump_script = args.script or os.path.join(script_dir, 'abaqus_dump.py')

    if not os.path.exists(odb_path):
        print('ERROR: ODB not found: {}'.format(odb_path))
        sys.exit(1)
    if not os.path.exists(dump_script):
        print('ERROR: abaqus_dump.py not found: {}'.format(dump_script))
        sys.exit(1)

    t_start = time.time()
    manifest_path = os.path.join(raw_dir, 'fields_manifest.json')

    # ── Step 1: Preflight ────────────────────────────────────────────────────
    if not args.no_preflight:
        print('[parallel] Step 1: Preflight (assembly + geom + sets + field scan)...')
        cmd = [sys.executable, dump_script,
               '--mode', 'preflight',
               '--odb',  odb_path,
               '--out',  workspace]
        t0 = time.time()
        rc = subprocess.call(cmd)
        if rc != 0:
            print('[parallel] ERROR: preflight failed (exit code {})'.format(rc))
            sys.exit(1)
        print('[parallel] Preflight done in {}.'.format(fmt_t(time.time() - t0)))
    else:
        print('[parallel] Skipping preflight (--no-preflight).')

    # ── Step 2: Read fields manifest ─────────────────────────────────────────
    if not os.path.exists(manifest_path):
        print('[parallel] ERROR: fields_manifest.json not found: {}'.format(manifest_path))
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    fields_by_step = manifest.get('fields_by_step', {})
    total_fields = sum(len(v) for v in fields_by_step.values())
    print('[parallel] Fields to dump: {} across {} step(s).'.format(
        total_fields, len(fields_by_step)))

    if total_fields == 0:
        print('[parallel] Nothing to dump. Done.')
        return

    # ── Step 3: Launch workers ────────────────────────────────────────────────
    print('[parallel] Step 2: Launching up to {} parallel workers...'.format(args.workers))

    os.makedirs(raw_dir) if not os.path.exists(raw_dir) else None

    procs      = []   # list of (worker_id, Popen, log_file_handle)
    worker_id  = 0

    for step_name, fields in sorted(fields_by_step.items()):
        if not fields:
            continue
        # Chunk by logical field group (contact pairs with the same base name
        # must land on the same worker), then flatten back to raw field names.
        unit_chunks = chunk_list(group_fields_for_workers(fields), args.workers)
        chunks = [[f for unit in uc for f in unit] for uc in unit_chunks]
        for chunk in chunks:
            wid = worker_id
            log_path = os.path.join(raw_dir, 'worker_{}.log'.format(wid))
            cmd = [sys.executable, dump_script,
                   '--mode',      'results-worker',
                   '--odb',       odb_path,
                   '--out',       workspace,
                   '--step',      step_name,
                   '--fields',    ','.join(chunk),
                   '--worker-id', str(wid)]
            print('[parallel]   Worker {:2d}: step={!r}, {} field(s): {}'.format(
                wid, step_name, len(chunk), chunk[:5]) +
                (' ...' if len(chunk) > 5 else ''))
            log_fh = open(log_path, 'w')
            proc   = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT)
            procs.append((wid, proc, log_fh))
            worker_id += 1

    print('[parallel] {} workers launched. Waiting for completion...'.format(len(procs)))
    t1 = time.time()

    # ── Step 4: Wait and report ───────────────────────────────────────────────
    failed = []
    for wid, proc, log_fh in procs:
        rc = proc.wait()
        log_fh.close()
        log_path = os.path.join(raw_dir, 'worker_{}.log'.format(wid))
        if rc != 0:
            failed.append(wid)
            print('[parallel] Worker {:2d} FAILED (exit {}). Log: {}'.format(
                wid, rc, log_path))
        else:
            print('[parallel] Worker {:2d} done.'.format(wid))

    elapsed_workers = fmt_t(time.time() - t1)
    elapsed_total   = fmt_t(time.time() - t_start)

    if failed:
        print('\n[parallel] ERROR: {} worker(s) failed: {}'.format(
            len(failed), failed))
        print('[parallel] Check logs in: {}'.format(raw_dir))
        sys.exit(1)

    print('\n[parallel] All {} workers succeeded.'.format(len(procs)))
    print('[parallel] Workers elapsed: {}'.format(elapsed_workers))
    print('[parallel] Total elapsed:   {}'.format(elapsed_total))
    print('[parallel] Next step:')
    print('[parallel]   python src/l1/l1_pack.py --workspace {}'.format(workspace))


if __name__ == '__main__':
    main()
