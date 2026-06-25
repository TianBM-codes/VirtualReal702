"""检查某个场的 ELEMENT_NODAL / INTEGRATION_POINT 数据是不是全 NaN。

用法:
    python tests/check_en_nan.py <workspace> [field=S_MISES] [frame=1]

合成不变量场(S_MISES/S_PRESS/...)若由早于 commit f5e6ebf 的 L1 提取，其
ELEMENT_NODAL 块会整块写成 NaN —— 此时 L3 会退回 INTEGRATION_POINT_FLAT，
云图不外推/不平均、也吃不到中节点细分，于是和 Abaqus 的平滑场对不上。
本脚本逐 instance/etype 打印两种位置的有限值比例，一眼看出 EN 是否为空。
"""
import sys
import os
import sqlite3
import numpy as np
import h5py


def _scan(f, root):
    if root not in f:
        print("  [{}] 不存在".format(root))
        return
    total = 0
    finite = 0
    for inst in f[root]:
        base = f[root + "/" + inst]
        for et in base:
            g = base[et]
            if "data" not in g:
                continue
            d = g["data"][:]
            fin = int(np.isfinite(d).sum())
            tot = int(d.size)
            total += tot
            finite += fin
            flag = "  <-- 全 NaN" if fin == 0 else ""
            print("  [{}] {}/{}  shape={}  有限值={}/{}{}".format(
                root.strip("/"), inst, et, d.shape, fin, tot, flag))
    if total:
        print("  >>> {} 合计有限值 {}/{} ({:.1f}%)".format(
            root.strip("/"), finite, total, 100.0 * finite / total))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ws = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else "S_MISES"

    db = os.path.join(ws, "manifest.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT file_path FROM result_files WHERE field_name=? LIMIT 1", (field,)
    ).fetchone()
    if row is None or not row["file_path"]:
        avail = [r["field_name"] for r in con.execute(
            "SELECT DISTINCT field_name FROM result_files ORDER BY field_name")]
        print("[!] 没有 field =", repr(field), " 可用:", ", ".join(avail))
        sys.exit(1)

    h5p = os.path.join(ws, row["file_path"].replace("\\", os.sep).replace("/", os.sep))
    print("field =", field, " file =", h5p)
    with h5py.File(h5p, "r") as f:
        _scan(f, "/ELEMENT_NODAL")
        _scan(f, "/INTEGRATION_POINT")


if __name__ == "__main__":
    main()
