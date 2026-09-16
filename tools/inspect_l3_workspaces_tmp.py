import glob
import os
import sqlite3


ROOT = r"D:\WorkSpace\OtherProjects\VirtualReal702\model"


def main():
    for ws in sorted(glob.glob(os.path.join(ROOT, "*"))):
        db = os.path.join(ws, "manifest.db")
        rd = os.path.join(ws, "l2", "render")
        if not os.path.exists(db) or not os.path.isdir(rd):
            continue
        try:
            con = sqlite3.connect(db)
            con.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in con.execute(
                    "select name from sqlite_master where type='table'"
                ).fetchall()
            }
            results = []
            if "result_files" in tables:
                results = [
                    dict(row)
                    for row in con.execute(
                        "select result_group, step_name, field_name, source, file_path "
                        "from result_files order by result_group, step_name, field_name limit 8"
                    ).fetchall()
                ]
            instances = []
            if "instances" in tables:
                instances = [
                    row[0]
                    for row in con.execute(
                        "select instance_name from instances limit 8"
                    ).fetchall()
                ]
            con.close()
            render_h5 = [
                os.path.basename(path)
                for path in glob.glob(os.path.join(rd, "*_render.h5"))[:8]
            ]
            print("\nWS", ws)
            print("render_h5", render_h5)
            print("instances", instances)
            print("results", results)
        except Exception as exc:
            print("ERR", ws, repr(exc))


if __name__ == "__main__":
    main()
