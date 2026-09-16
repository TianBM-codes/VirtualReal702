#!/usr/bin/env python
from __future__ import annotations

import os
import sqlite3


BASE = r"D:\WorkSpace\OtherProjects\VirtualReal702\model"


def main() -> int:
    for name in sorted(os.listdir(BASE)):
        ws = os.path.join(BASE, name)
        db = os.path.join(ws, "manifest.db")
        if not os.path.exists(db):
            continue
        try:
            con = sqlite3.connect(db)
            con.row_factory = sqlite3.Row
            tabs = {r[0] for r in con.execute(
                "select name from sqlite_master where type='table'"
            )}
            if "result_fields" not in tabs:
                continue
            rows = con.execute(
                """
                select result_group, step_name, field_name, file_path
                from result_fields
                where field_name = 'U' or field_name like 'U%'
                order by result_group, step_name, field_name
                limit 30
                """
            ).fetchall()
            if not rows:
                continue
            inst = con.execute(
                "select instance_name, node_count, elem_count from instances limit 1"
            ).fetchone()
            print(f"\nWS {ws}")
            print("instance", dict(inst) if inst else None)
            for row in rows:
                print(dict(row))
        except Exception as exc:
            print("ERR", ws, exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
