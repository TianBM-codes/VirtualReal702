#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_section_ids.py — 诊断所有 instance 的 section_id 覆盖情况

用法:
    python tools/diag_section_ids.py --workspace /data/<odb_id>

检查每个 instance:
  - sections 分组是否存在
  - 每个 section 的 element_set 是否能在 H5 里找到（safe 编码匹配）
  - label_to_sid 建立后覆盖了多少单元
  - 最终 section_id=0 的单元数量及原因分类
"""

import argparse
import os
import sqlite3
import sys

import h5py
import numpy as np


def safe(name):
    return name.replace('/', '__').replace('\\', '__').replace(' ', '_')


def parse_args():
    p = argparse.ArgumentParser(description='Section-ID 覆盖诊断')
    p.add_argument('--workspace', required=True)
    return p.parse_args()


def diag_instance(inst_name, geom_path):
    print("\n" + "=" * 60)
    print(f"Instance: {inst_name}")
    print(f"  geom H5: {geom_path}")

    if not os.path.exists(geom_path):
        print("  [ERROR] geom H5 不存在，跳过")
        return

    with h5py.File(geom_path, 'r') as f:

        # ── 基础信息 ───────────────────────────────────────────────
        etypes = sorted(f.get('elements', {}).keys())
        total_elems = sum(len(f[f'elements/{e}/labels']) for e in etypes
                         if 'labels' in f[f'elements/{e}'])
        print(f"  单元类型: {etypes}")
        print(f"  单元总数: {total_elems}")

        # ── 检查 sections 分组 ─────────────────────────────────────
        if 'sections' not in f:
            print("  [CASE A] sections 分组不存在 → 全部 section_id = 0")
            return

        sec_keys = sorted(f['sections'].keys())
        print(f"  sections 数量: {len(sec_keys)}  ({sec_keys})")

        # ── 检查 instance_sets/element_sets ───────────────────────
        isets_grp = f.get('instance_sets/element_sets', {})
        isets_keys = set(isets_grp.keys())
        print(f"  instance_sets/element_sets 数量: {len(isets_keys)}")

        # ── 逐 section 检查 ────────────────────────────────────────
        label_to_sid = {}
        section_names_used = []

        skipped_empty_eset   = []   # Case C: element_set 属性为空
        skipped_not_in_h5    = []   # Case B/D: key 找不到
        skipped_safe_mismatch = []  # Case B 细分: safe() 编码后能找到

        for sec_name in sec_keys:
            grp  = f[f'sections/{sec_name}']
            eset = grp.attrs.get('element_set', '')

            if not eset:
                skipped_empty_eset.append(sec_name)
                continue

            key       = f'instance_sets/element_sets/{eset}'
            key_safe  = f'instance_sets/element_sets/{safe(eset)}'

            if key in f:
                # 正常找到
                sid = len(section_names_used) + 1
                section_names_used.append(sec_name)
                for lbl in f[key][:]:
                    label_to_sid[int(lbl)] = sid
            elif key_safe in f and key_safe != key:
                # safe 编码后能找到 → Case B
                skipped_safe_mismatch.append((sec_name, eset, safe(eset)))
            else:
                # 完全找不到 → Case D (assembly-level set 或其他)
                skipped_not_in_h5.append((sec_name, eset))

        # ── 报告跳过的 sections ────────────────────────────────────
        if skipped_empty_eset:
            print(f"\n  [CASE C] element_set 为空的 sections ({len(skipped_empty_eset)} 个):")
            for s in skipped_empty_eset:
                print(f"    - {s}")

        if skipped_safe_mismatch:
            print(f"\n  [CASE B] element_set 名含特殊字符，safe() 编码不一致 ({len(skipped_safe_mismatch)} 个):")
            for sec_name, orig, encoded in skipped_safe_mismatch:
                print(f"    - section={sec_name!r}  element_set={orig!r}  safe={encoded!r}")

        if skipped_not_in_h5:
            print(f"\n  [CASE D] element_set 在 H5 里完全找不到 ({len(skipped_not_in_h5)} 个):")
            for sec_name, eset in skipped_not_in_h5:
                print(f"    - section={sec_name!r}  element_set={eset!r}")

        if not label_to_sid:
            print("\n  [CASE G] label_to_sid 为空 → 全部 section_id = 0")
            return

        print(f"\n  label_to_sid 覆盖标签数: {len(label_to_sid)}")

        # ── 逐 etype 统计 section_id 覆盖 ─────────────────────────
        print("\n  Per-etype 覆盖统计:")
        total_zero = 0
        total_surf  = 0

        for etype in etypes:
            grp = f.get(f'elements/{etype}')
            if grp is None or 'labels' not in grp:
                continue
            labels = grp['labels'][:]
            n_total = len(labels)

            sid_arr = np.array(
                [label_to_sid.get(int(lbl), 0) for lbl in labels],
                dtype=np.int32
            )
            n_zero    = int((sid_arr == 0).sum())
            n_nonzero = n_total - n_zero

            # 检查 Case E: section_id.npy 里是否有 -1（abaqus_dump 标记的未分配）
            sid_npy_path = f'elements/{etype}/section_id'
            n_minus1 = 0
            if sid_npy_path in f:
                raw_sid = f[sid_npy_path][:]
                n_minus1 = int((raw_sid == -1).sum())

            print(f"    {etype:12s}: 共 {n_total:6d}  已分配 {n_nonzero:6d}  "
                  f"section_id=0: {n_zero:6d}"
                  + (f"  (npy中-1: {n_minus1})" if n_minus1 > 0 else ""))

            total_zero += n_zero
            total_surf  += n_total

        pct = 100.0 * total_zero / total_surf if total_surf else 0
        print(f"\n  汇总: 共 {total_surf} 个单元，section_id=0 共 {total_zero} 个 ({pct:.1f}%)")

        # ── Case E 检测: 同名 section 覆盖（sections_info 里 last-wins）─
        # 用 element_set → [sec_name, ...] 反查，看有没有多个 section 指向同一 eset
        eset_to_secs = {}
        for sec_name in sec_keys:
            eset = f[f'sections/{sec_name}'].attrs.get('element_set', '')
            if eset:
                eset_to_secs.setdefault(eset, []).append(sec_name)
        # 同一 sectionName 如果被分配给多个 eset，sections.json 只保留最后一个，
        # 实际上体现为不同 sectionName 指向不同 eset（这里看 H5 里的最终状态）
        # 但如果两个 sectionAssignment 共用同一 sectionName 指向不同 eset，
        # 只有最后一个会出现在 sections/ 分组里（sections.json 覆盖写入）
        # 这里无法从 H5 还原，提示用户检查原始 sections.json
        dup_esets = {k: v for k, v in eset_to_secs.items() if len(v) > 1}
        if dup_esets:
            print(f"\n  [注意] 多个 section 名指向同一 element_set (不常见，请检查):")
            for eset, secs in dup_esets.items():
                print(f"    element_set={eset!r} ← {secs}")


def main():
    args = parse_args()
    workspace = os.path.abspath(args.workspace)
    db_path   = os.path.join(workspace, 'manifest.db')
    geom_dir  = os.path.join(workspace, 'l1', 'geometry')

    if not os.path.exists(db_path):
        print(f"[ERROR] manifest.db 不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT instance_name, geom_path FROM instances ORDER BY instance_name"
    ).fetchall()
    conn.close()

    if not rows:
        print("[ERROR] manifest.db 中 instances 表为空")
        sys.exit(1)

    print(f"Workspace: {workspace}")
    print(f"共 {len(rows)} 个 instance")

    for row in rows:
        inst_name = row['instance_name']
        geom_rel  = row['geom_path']
        geom_path = os.path.join(workspace, geom_rel) if geom_rel else \
                    os.path.join(geom_dir, f"{inst_name}.h5")
        diag_instance(inst_name, geom_path)

    print("\n" + "=" * 60)
    print("诊断完成")
    print()
    print("Case 说明:")
    print("  [CASE A] sections 分组不存在 → abaqus_dump 未提取截面或全部失败")
    print("  [CASE B] element_set 名含 / \\ 空格 → safe() 编码后不一致，key 查不到")
    print("  [CASE C] element_set 属性为空 → sa.region.name 为空字符串")
    print("  [CASE D] element_set 完全找不到 → 可能是 assembly-level set")
    print("  [CASE E] 同一 sectionName 分配给多个 eset → sections.json last-wins 覆盖")
    print("  [CASE G] 所有 section 都被跳过 → label_to_sid 为空，全域 section_id=0")
    print()
    print("修复建议:")
    print("  CASE B: compute_section_ids() 里 key 改为 safe(eset) 即可")
    print("  CASE D: abaqus_dump 里需要也收集 assembly-level elementSets")
    print("  CASE E: abaqus_dump 里 sections_info 改为 list 而非 dict")


if __name__ == '__main__':
    main()
