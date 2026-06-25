"""导出"我们这边" L1 HDF5 里某个 NODAL 矢量场(U / UR / RF ...)的原始值，
逐 instance、逐分量、外加幅值(MAGNITUDE)统计 min/max，用于和 Abaqus 对照。

U / UR / RF 这类是节点场(NODAL)，不走积分点外推，所以不能用 dump_ours_s.py
(那个只导 INTEGRATION_POINT / ELEMENT_NODAL)。本脚本专门读 /NODAL。

用法:
    python tests/dump_ours_nodal.py <workspace> [field=U] [frame=1]

输出:
  - 控制台: 每个 instance 各分量 + 幅值的 min/max，最后再打印全装配(global)汇总
  - CSV: tests/ours_<field>_nodal.csv (instance, nodeLabel, comp..., MAG)

判读要点:
  - 若"每个 instance 各自的分量都对、只是 global 合并后不对" → 全局并集漏了节点
  - 若"只有某些 instance 的分量对不上、幅值仍对" → 多半是该 instance 的坐标系/旋转
  - 若 UR 各 instance 原始值本身就全 0 → L1 存进来就是 0(看是否纯实体单元，物理上应为 0)
"""
import sys
import os
import sqlite3
import csv
import numpy as np
import h5py


def _stat_update(acc, name, arr):
    """acc[name] = [min, max, count]"""
    a = np.asarray(arr, dtype=np.float64).ravel()
    a = a[np.isfinite(a)]
    if a.size == 0:
        return
    lo, hi = float(a.min()), float(a.max())
    if name not in acc:
        acc[name] = [lo, hi, a.size]
    else:
        acc[name][0] = min(acc[name][0], lo)
        acc[name][1] = max(acc[name][1], hi)
        acc[name][2] += a.size


def _report(title, acc, cols):
    print(title)
    for c in list(cols) + ["MAG"]:
        if c in acc:
            lo, hi, n = acc[c]
            print("  %-8s min=%-13.6g max=%-13.6g (n=%d)" % (c, lo, hi, n))
        else:
            print("  %-8s 无数据" % c)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ws = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else "U"
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
        print("[!] result_files 里没有 field =", repr(field), " 可用:", ", ".join(avail))
        sys.exit(1)

    h5p = os.path.join(ws, row["file_path"].replace("\\", os.sep).replace("/", os.sep))
    if not os.path.exists(h5p):
        print("[!] H5 不存在:", h5p)
        sys.exit(1)

    here = os.path.dirname(os.path.abspath(__file__))
    out_csv = os.path.join(here, "ours_%s_nodal.csv" % field)

    g_acc = {}   # 全装配
    with h5py.File(h5p, "r") as f:
        comps = [c.decode() if isinstance(c, bytes) else c
                 for c in f["meta/components"][:]] if "meta/components" in f else []
        if "/NODAL" not in f:
            print("[!] 该场没有 /NODAL 位置数据，它可能不是节点场。顶层:", list(f.keys()))
            sys.exit(1)
        with open(out_csv, "w", newline="") as fout:
            w = csv.writer(fout)
            header_written = False
            for inst in f["/NODAL"]:
                g = f["/NODAL/" + inst]
                if "data" not in g:
                    continue
                labels = g["labels"][:] if "labels" in g else None
                nf = g["data"].shape[0]
                fi = min(frame, nf - 1)
                data = g["data"][fi]            # [N, ncomp]
                if data.ndim == 1:
                    data = data[:, None]
                ncomp = data.shape[1]
                cols = list(comps) if len(comps) == ncomp else \
                    (["VAL"] if ncomp == 1 else ["c%d" % i for i in range(ncomp)])
                mag = np.linalg.norm(data, axis=1)

                if not header_written:
                    w.writerow(["instance", "nodeLabel"] + cols + ["MAG"])
                    header_written = True

                # 逐 instance 统计
                i_acc = {}
                for ci, cl in enumerate(cols):
                    _stat_update(i_acc, cl, data[:, ci])
                    _stat_update(g_acc, cl, data[:, ci])
                _stat_update(i_acc, "MAG", mag)
                _stat_update(g_acc, "MAG", mag)
                _report("[instance %s] n_node=%d" % (inst, data.shape[0]), i_acc, cols)

                # 写 CSV(全节点)
                for ni in range(data.shape[0]):
                    lab = int(labels[ni]) if labels is not None else ni
                    rowvals = [float(x) for x in data[ni]]
                    w.writerow([inst, lab] + rowvals + [float(mag[ni])])

        print("\n已写出:", out_csv)
        _report("=== 全装配(global) field=%s frame=%d ===" % (field, frame), g_acc,
                cols if 'cols' in dir() else [])


if __name__ == "__main__":
    main()
