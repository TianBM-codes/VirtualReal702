"""重新解析(--invariants full)后, 检查我们自己生成的"面内/面外/Abs 合成字段"。

逐 (位置 / instance / 单元类型) 报 finite/NaN 计数与 min/max, 用来确认:
  - 实体单元块 全是 NaN(→前端置灰), 壳/膜块 有值;
  - 值域合理。

用法:
    python tests/dump_ours_inplane_check.py <workspace> [field=S_MAX_INPLANE_PRINCIPAL] [frame=1]

可查的合成字段例:
    S_MAX_INPLANE_PRINCIPAL  S_MIN_INPLANE_PRINCIPAL  S_OUTOFPLANE_PRINCIPAL
    S_MAX_PRINCIPAL_ABS      S_MAX_INPLANE_PRINCIPAL_ABS
    (应变同理: E_MAX_INPLANE_PRINCIPAL ...)

它对 H5 做通用递归遍历, 不假设具体分组层级。
"""
import sys
import os
import sqlite3
import numpy as np
import h5py


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ws = sys.argv[1]
    field = sys.argv[2] if len(sys.argv) > 2 else "S_MAX_INPLANE_PRINCIPAL"
    frame = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    db = os.path.join(ws, "manifest.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT file_path FROM result_files WHERE field_name=? LIMIT 1", (field,)
    ).fetchone()
    if row is None or not row["file_path"]:
        avail = [r["field_name"] for r in con.execute(
            "SELECT DISTINCT field_name FROM result_files ORDER BY field_name")]
        print("[!] result_files 里没有 field =", repr(field))
        print("    可用字段:", ", ".join(avail))
        sys.exit(1)

    h5p = os.path.join(ws, row["file_path"].replace("\\", os.sep).replace("/", os.sep))
    if not os.path.exists(h5p):
        print("[!] H5 不存在:", h5p)
        sys.exit(1)

    print("field =", field, " H5 =", h5p)
    rows = []

    def visit(name, obj):
        if isinstance(obj, h5py.Dataset) and name.endswith("data"):
            d = obj
            nf = d.shape[0] if d.ndim >= 1 else 1
            fi = min(frame, nf - 1)
            arr = np.asarray(d[fi]).ravel()
            fin = arr[np.isfinite(arr)]
            n_total = arr.size
            n_fin = fin.size
            n_nan = n_total - n_fin
            if n_fin:
                lo, hi = float(fin.min()), float(fin.max())
            else:
                lo = hi = float("nan")
            rows.append((name, n_total, n_fin, n_nan, lo, hi))

    with h5py.File(h5p, "r") as f:
        f.visititems(visit)

    if not rows:
        print("[!] H5 里没找到 data 数据集")
        return

    print("%-58s %9s %9s %9s   %s" % ("group/data", "total", "finite", "nan", "min..max(finite)"))
    for name, nt, nfin, nnan, lo, hi in rows:
        flag = "  ← 全NaN(置灰)" if nfin == 0 else ("  ← 有值" )
        print("%-58s %9d %9d %9d   %.5g .. %.5g%s"
              % (name, nt, nfin, nnan, lo, hi, flag))

    print("\n判读: 实体单元(C3D*)对应的 group 应 全NaN(置灰); 壳(S3R/S4R)应有值。")


if __name__ == "__main__":
    main()
