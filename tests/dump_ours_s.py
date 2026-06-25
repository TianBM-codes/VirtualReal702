"""导出"我们这边" L1 HDF5 里某个场的原始值到 CSV，用于和 Abaqus 对比。

同时导出两种位置(都是"未平均"的原始数据)：
  - INTEGRATION_POINT：积分点原值              -> ours_<field>_ip.csv
  - ELEMENT_NODAL    ：积分点外推到单元节点后的值 -> ours_<field>_en.csv

用法:
    python tests/dump_ours_s.py <workspace> [field=S] [frame=1]

例:
    python tests/dump_ours_s.py E:\\code\\...\\202606251121 S 1

说明:
- 第 2 个参数是"场名"(field)，不是 step。默认 S。
- 第 3 个参数是帧号(0 起算)，默认 1，对应前端 URL 里的 frame=1。
- 导出的都是未平均的原始值，跨全部 instance，便于和 Abaqus 在
  Integration Point / Unaveraged 口径下逐单元对照。
"""
import sys
import os
import sqlite3
import csv
import numpy as np
import h5py


def _dump_position(f, pos, comps, frame, out_csv):
    """导出 H5 里某个 position(/INTEGRATION_POINT 或 /ELEMENT_NODAL)的所有值。

    返回 {comp_label: [values...]}，找不到该 position 返回 None。
    """
    root = "/" + pos
    if root not in f:
        return None
    allvals = {}
    with open(out_csv, "w", newline="") as out:
        w = csv.writer(out)
        # IP 第三列是积分点号，EN 第三列是单元内节点序号
        third = "ip" if pos == "INTEGRATION_POINT" else "nodeIdx"
        w.writerow(["instance", "elemLabel", third] + list(comps))
        for inst in f[root]:
            base = f[root + "/" + inst]
            for et in base:
                g = base[et]
                if "data" not in g:
                    continue
                labels = g["labels"][:] if "labels" in g else None
                nf = g["data"].shape[0]
                fi = min(frame, nf - 1)
                data = g["data"][fi]                     # [N_elem, n_pt, ncomp]
                for ei in range(data.shape[0]):
                    lab = int(labels[ei]) if labels is not None else ei
                    for k in range(data.shape[1]):
                        vals = [float(x) for x in data[ei, k]]
                        w.writerow([inst, lab, k + 1] + vals)
                        for ci, cl in enumerate(comps):
                            if ci < len(vals) and np.isfinite(vals[ci]):
                                allvals.setdefault(cl, []).append(vals[ci])
    return allvals


def _report(name, allvals):
    if allvals is None:
        print("[!]", name, "该场没有此 position 数据")
        return
    print("已写出:", name)
    for cl, lst in allvals.items():
        a = np.array(lst)
        print("  %-6s 全装配范围: min=%.5g  max=%.5g  (n=%d)"
              % (cl, a.min(), a.max(), a.size))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ws = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else "S"
    frame = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    db = os.path.join(ws, "manifest.db")
    if not os.path.exists(db):
        print("[!] 找不到 manifest.db:", db)
        sys.exit(1)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    row = con.execute(
        "SELECT file_path FROM result_files WHERE field_name=? LIMIT 1", (field,)
    ).fetchone()
    if row is None or not row["file_path"]:
        avail = [r["field_name"] for r in con.execute(
            "SELECT DISTINCT field_name FROM result_files ORDER BY field_name")]
        print("[!] result_files 里没有 field =", repr(field))
        print("    注意第 2 个参数是'场名'不是 step。可用 field 有:")
        print("   ", ", ".join(avail))
        sys.exit(1)

    h5p = os.path.join(ws, row["file_path"].replace("\\", os.sep).replace("/", os.sep))
    if not os.path.exists(h5p):
        print("[!] H5 不存在:", h5p)
        sys.exit(1)

    here = os.path.dirname(os.path.abspath(__file__))
    with h5py.File(h5p, "r") as f:
        comps = [c.decode() if isinstance(c, bytes) else c
                 for c in f["meta/components"][:]] if "meta/components" in f else []
        print("components =", comps)

        print("--- INTEGRATION_POINT (积分点原值) ---")
        ip = _dump_position(f, "INTEGRATION_POINT", comps, frame,
                            os.path.join(here, "ours_%s_ip.csv" % field))
        _report(os.path.join(here, "ours_%s_ip.csv" % field), ip)

        print("--- ELEMENT_NODAL (外推到节点, 未平均) ---")
        en = _dump_position(f, "ELEMENT_NODAL", comps, frame,
                            os.path.join(here, "ours_%s_en.csv" % field))
        _report(os.path.join(here, "ours_%s_en.csv" % field), en)


if __name__ == "__main__":
    main()
