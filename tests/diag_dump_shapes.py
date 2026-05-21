#!/usr/bin/env python3
"""
诊断 abaqus_dump.py 输出目录中各 block 的 npy shape 一致性。

用法：
    python3 tests/diag_dump_shapes.py <raw_dir>

raw_dir = abaqus_dump.py --out 指定的工作目录
（里面有 results/ 子目录和 meta.json）

只打印存在 shape 不一致的 block，输出可直接贴给 Claude 诊断。
"""
import os
import sys
import json

import numpy as np


def safe_load(path):
    return np.load(path, allow_pickle=False)


def scan_field(field_dir):
    meta_path = os.path.join(field_dir, 'meta.json')
    if not os.path.isfile(meta_path):
        return

    with open(meta_path) as fh:
        fm = json.load(fh)

    step  = fm['step_name']
    field = fm['field_name']

    for binfo in fm.get('blocks', []):
        inst    = binfo['inst_name']
        pos     = binfo['position']
        etype   = binfo.get('elem_type') or ''
        sp_num  = binfo.get('sp_num')
        N_meta  = binfo['n_entities']
        n_ip    = binfo.get('n_ip')
        ncomp   = binfo.get('ncomp')

        # 重建 block 目录（与 abaqus_dump.py get_block_dir 逻辑相同）
        parts = [field_dir, inst.replace('/', '__'), pos]
        if etype:
            parts.append(etype.replace('/', '__'))
        if sp_num is not None:
            parts.append('sp{}'.format(sp_num))
        bd = os.path.join(*parts)

        if not os.path.isdir(bd):
            continue

        # labels.npy 长度
        labels_path = os.path.join(bd, 'labels.npy')
        labels_n = len(safe_load(labels_path)) if os.path.isfile(labels_path) else -1

        # 扫描帧文件 f0000.npy, f0001.npy, ...
        frame_shapes = {}   # shape → [frame_idx, ...]
        fi = 0
        consecutive_miss = 0
        while consecutive_miss < 20:
            fp = os.path.join(bd, 'f{:04d}.npy'.format(fi))
            if not os.path.isfile(fp):
                consecutive_miss += 1
                fi += 1
                continue
            consecutive_miss = 0
            arr = safe_load(fp)
            frame_shapes.setdefault(arr.shape, []).append(fi)
            fi += 1

        if not frame_shapes:
            continue

        # 判断是否有 shape 不一致（以 N_meta 为基准）
        mismatched = [s for s in frame_shapes if s[0] != N_meta]
        if not mismatched:
            continue  # 全部一致，不输出

        print("=== {}/{} | inst={} | pos={} | etype={} | sp={}".format(
            step, field, inst, pos, etype or '(none)', sp_num))
        print("    meta.json  : N_ent={} n_ip={} ncomp={}".format(N_meta, n_ip, ncomp))
        print("    labels.npy : N={}".format(labels_n))
        total_frames = sum(len(v) for v in frame_shapes.values())
        print("    帧文件总数 : {}".format(total_frames))
        for shape, frames in sorted(frame_shapes.items(), key=lambda x: x[0][0]):
            mark = "  ← MISMATCH" if shape[0] != N_meta else ""
            sample = frames[:5]
            print("    shape={} : {}帧 (fi 示例: {}){}" .format(
                shape, len(frames), sample, mark))
        print()


def main():
    if len(sys.argv) < 2:
        print("用法: python3 diag_dump_shapes.py <raw_dir>")
        sys.exit(1)

    raw_dir = sys.argv[1]
    results_dir = os.path.join(raw_dir, 'results')

    if not os.path.isdir(results_dir):
        print("错误: 找不到 results/ 目录:", results_dir)
        sys.exit(1)

    found_any = False
    for name in sorted(os.listdir(results_dir)):
        field_dir = os.path.join(results_dir, name)
        if os.path.isdir(field_dir):
            before = found_any
            # 调用扫描（通过捕获输出判断是否有内容）
            scan_field(field_dir)
            found_any = True

    print("扫描完成。无输出 = 所有 block 的帧 shape 全部一致。")


if __name__ == '__main__':
    main()
