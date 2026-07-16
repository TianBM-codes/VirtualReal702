# -*- coding: utf-8 -*-
"""
诊断 manifest.db 的 result_blocks / result_files / steps 状态。

用法:
    python tools/diag_manifest.py <workspace目录或manifest.db路径>

例如:
    python tools/diag_manifest.py E:\code\odb-service-design\odb-service-design\model_v2\202607161656
"""
import os
import sqlite3
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    if os.path.isdir(path):
        path = os.path.join(path, "manifest.db")
    if not os.path.exists(path):
        print("manifest.db not found:", path)
        sys.exit(1)
    print("== manifest:", path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("== tables:", ", ".join(tables))

    for tbl in ("steps", "frames", "result_files", "result_blocks"):
        if tbl not in tables:
            print("\n== {}: <表不存在>".format(tbl))
            continue
        cols = [r[1] for r in conn.execute("PRAGMA table_info({})".format(tbl))]
        print("\n== {} 列: {}".format(tbl, ", ".join(cols)))
        if "result_group" in cols:
            rows = conn.execute(
                "SELECT result_group, COUNT(*) AS n FROM {} GROUP BY result_group".format(tbl)
            ).fetchall()
            for r in rows:
                print("   result_group={!r}: {} 行".format(r["result_group"], r["n"]))
            if not rows:
                print("   <空表>")
        else:
            n = conn.execute("SELECT COUNT(*) FROM {}".format(tbl)).fetchone()[0]
            print("   共 {} 行 (无 result_group 列!)".format(n))

    if "result_blocks" in tables:
        print("\n== result_blocks 前 15 行:")
        for r in conn.execute(
                "SELECT result_group, step_name, field_name, instance_name,"
                "       position, elem_type FROM result_blocks LIMIT 15"):
            print("   ", dict(r))
        print("\n== result_blocks 重复检查 (同 step/field/instance/position/elem_type 多行):")
        dups = conn.execute(
            "SELECT step_name, field_name, instance_name, position, elem_type,"
            "       COUNT(*) AS n FROM result_blocks"
            " GROUP BY step_name, field_name, instance_name, position, elem_type"
            " HAVING n > 1 LIMIT 10").fetchall()
        for r in dups:
            print("   ", dict(r))
        if not dups:
            print("    无重复")

    if "steps" in tables:
        print("\n== steps 全部行:")
        for r in conn.execute("SELECT * FROM steps"):
            print("   ", dict(r))

    conn.close()


if __name__ == "__main__":
    main()
