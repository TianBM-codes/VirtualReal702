# -*- coding: utf-8 -*-
"""
修复已存在 workspace 的 result_blocks 表：

1. 迁移到含 sp_num 的新 schema（壳截面点块不再是"重复行"）
2. 可选：把 result_group 为 NULL 的行打上指定标签
   （对应 job_runner._adopt_odb_result_group 当年因重复行静默失败的场景）

用法:
    python tools/fix_result_blocks.py <workspace目录> [--tag-null <result_group>]

例如:
    python tools/fix_result_blocks.py "E:\\...\\model_v2\\202607161656" --tag-null default_result
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.l1.manifest_schema import migrate_result_blocks  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace", help="workspace 目录（含 manifest.db）")
    ap.add_argument("--tag-null", metavar="RESULT_GROUP", default=None,
                    help="把 result_group 为 NULL 的行打上该标签")
    args = ap.parse_args()

    path = os.path.join(args.workspace, "manifest.db") \
        if os.path.isdir(args.workspace) else args.workspace
    if not os.path.exists(path):
        print("manifest.db not found:", path)
        sys.exit(1)

    conn = sqlite3.connect(path, timeout=10.0)
    try:
        before = conn.execute("SELECT COUNT(*) FROM result_blocks").fetchone()[0]
        migrated = migrate_result_blocks(conn)
        after = conn.execute("SELECT COUNT(*) FROM result_blocks").fetchone()[0]
        print("迁移: {}（{} 行 → {} 行）".format(
            "已执行" if migrated else "无需执行（schema 已是新版）", before, after))

        if args.tag_null:
            cur = conn.execute(
                "UPDATE result_blocks SET result_group=? WHERE result_group IS NULL",
                (args.tag_null,))
            print("打标: {} 行 result_group NULL → {!r}".format(
                cur.rowcount, args.tag_null))

        conn.commit()
        for rg, n in conn.execute(
                "SELECT result_group, COUNT(*) FROM result_blocks GROUP BY result_group"):
            print("  result_group={!r}: {} 行".format(rg, n))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
