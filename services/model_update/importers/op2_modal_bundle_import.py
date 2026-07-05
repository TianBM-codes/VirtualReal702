import json
from pathlib import Path

from db import ensure_tables_exist, get_connection


BUNDLE_JSON = r"D:\temp\op2_modal_bundle.json"
PROJECT_ID = 25
OVERWRITE = True
CHUNK_SIZE = 5000


if __name__ == "__main__":
    ensure_tables_exist()

    bundle_path = Path(BUNDLE_JSON).expanduser().resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    modes = [dict(x or {}) for x in list(bundle.get("modes") or [])]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if OVERWRITE:
            cursor.execute("DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s", (int(PROJECT_ID),))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (int(PROJECT_ID),))
            conn.commit()

        sql = """
        INSERT INTO t_mt_py_fem_modal_result
        (pid, mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            frequency = VALUES(frequency),
            part_name = VALUES(part_name),
            u1 = VALUES(u1),
            u2 = VALUES(u2),
            u3 = VALUES(u3),
            extra_json = VALUES(extra_json),
            created_at = CURRENT_TIMESTAMP
        """

        rows = []
        total = 0
        for mode in modes:
            mode_no = int(mode["mode_no"])
            frequency = float(mode["frequency"]) if mode.get("frequency") is not None else None
            for node in list(mode.get("nodes") or []):
                vector = list(node.get("vector") or [])
                u1 = node.get("u1")
                u2 = node.get("u2")
                u3 = node.get("u3")
                if vector:
                    if u1 is None and len(vector) > 0:
                        u1 = vector[0]
                    if u2 is None and len(vector) > 1:
                        u2 = vector[1]
                    if u3 is None and len(vector) > 2:
                        u3 = vector[2]
                rows.append((
                    int(PROJECT_ID),
                    mode_no,
                    frequency,
                    str(node.get("instance_name") or "BDF_MODEL"),
                    str(node.get("part_name") or node.get("instance_name") or "BDF_MODEL"),
                    int(node["fem_node_label"]),
                    float(u1) if u1 is not None else None,
                    float(u2) if u2 is not None else None,
                    float(u3) if u3 is not None else None,
                    json.dumps(node.get("extra_json") or {}, ensure_ascii=False),
                ))
                if len(rows) >= int(CHUNK_SIZE):
                    cursor.executemany(sql, rows)
                    conn.commit()
                    total += len(rows)
                    rows = []

        if rows:
            cursor.executemany(sql, rows)
            conn.commit()
            total += len(rows)

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    print("project_id:", int(PROJECT_ID))
    print("mode_count:", len(modes))
    print("row_count:", total)
