"""导出"我们这边" L1 HDF5 里某个场的积分点(未平均)原始值到 CSV，用于和 Abaqus 对比。

用法:
    python tests/dump_ours_s.py <workspace> [field=S] [frame=1]

例:
    python tests/dump_ours_s.py E:\\code\\...\\202606251121
    python tests/dump_ours_s.py E:\\code\\...\\202606251121 S 1

说明:
- 第 2 个参数是"场名"(field)，不是 step。默认 S。
- 第 3 个参数是帧号(0 起算)，默认 1，对应前端 URL 里的 frame=1。
- 导出的是 INTEGRATION_POINT 位置的原始值(未平均)，跨全部 instance，
  便于和 Abaqus 在 Integration Point / Unaveraged 口径下逐单元对照。
"""
import sys
import os
import sqlite3
import csv
import numpy as np
import h5py


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

    out_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ours_%s_ip.csv" % field)
    allvals = {}  # comp_label -> list

    with h5py.File(h5p, "r") as f:
        comps = [c.decode() if isinstance(c, bytes) else c
                 for c in f["meta/components"][:]] if "meta/components" in f else []
        print("components =", comps)

        if "/INTEGRATION_POINT" not in f:
            print("[!] 该场没有 INTEGRATION_POINT 位置数据，positions=",
                  row.keys() if hasattr(row, "keys") else "")
            sys.exit(1)

        with open(out_csv, "w", newline="") as out:
            w = csv.writer(out)
            w.writerow(["instance", "elemLabel", "ip"] + list(comps))
            for inst in f["/INTEGRATION_POINT"]:
                base = f["/INTEGRATION_POINT/" + inst]
                for et in base:
                    g = base[et]
                    if "data" not in g:
                        continue
                    labels = g["labels"][:]
                    nf = g["data"].shape[0]
                    fi = min(frame, nf - 1)
                    data = g["data"][fi]                     # [N_elem, n_ip, ncomp]
                    ips = g["ip_labels"][:] if "ip_labels" in g else [1]
                    for ei, lab in enumerate(labels):
                        for k in range(data.shape[1]):
                            vals = [float(x) for x in data[ei, k]]
                            w.writerow([inst, int(lab), int(ips[k] if k < len(ips) else k + 1)] + vals)
                            for ci, cl in enumerate(comps):
                                if ci < len(vals) and np.isfinite(vals[ci]):
                                    allvals.setdefault(cl, []).append(vals[ci])

    print("已写出:", out_csv)
    for cl, lst in allvals.items():
        a = np.array(lst)
        print("  %-6s 全装配积分点范围: min=%.5g  max=%.5g  (n=%d)" % (cl, a.min(), a.max(), a.size))


if __name__ == "__main__":
    main()
