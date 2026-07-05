from pathlib import Path

import numpy as np

from db import ensure_tables_exist, get_connection
from services.model_update.importers.op2_service import (
    _abs_file,
    _extract_mode_frequency,
    _extract_requested_mode_indices,
    _load_bdf_model,
    _read_op2,
    _resolve_modal_identity,
    _resolve_subcases,
)


OP2_PATH = r"D:\your\model.op2"
BDF_PATH = r"D:\your\model.bdf"
PROJECT_ID = 25
SUBCASE_ID = None
MODE_NUMBERS = None      # e.g. [1, 2, 3]
OVERWRITE = True
CHUNK_SIZE = 5000
INSTANCE_NAME = None
PART_NAME = None
ALL_SUBCASES = False


if __name__ == "__main__":
    ensure_tables_exist()

    op2_path = _abs_file(OP2_PATH, "op2_path")
    bdf_path = _abs_file(BDF_PATH, "bdf_path") if str(BDF_PATH).strip() else None
    instance_name, part_name = _resolve_modal_identity(INSTANCE_NAME, PART_NAME, bdf_path)

    op2 = _read_op2(op2_path)
    subcases = _resolve_subcases(op2, SUBCASE_ID, all_subcases=ALL_SUBCASES)

    ordered_node_ids = None
    if bdf_path:
        bdf_model = _load_bdf_model(bdf_path)
        ordered_node_ids = sorted(int(node_id) for node_id in bdf_model.nodes.keys())

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
        total_rows = 0
        total_modes = 0
        import_mode_no = 1

        for subcase_id in subcases:
            eigen_data = op2.eigenvectors[subcase_id]
            modes_array = np.asarray(getattr(eigen_data, "modes", []), dtype=np.int64)
            mode_indices = _extract_requested_mode_indices(modes_array, MODE_NUMBERS)

            result_node_ids = np.asarray(eigen_data.node_gridtype[:, 0], dtype=np.int64)
            result_node_index = {int(node_id): idx for idx, node_id in enumerate(result_node_ids.tolist())}
            active_node_ids = ordered_node_ids or result_node_ids.tolist()

            for mode_index in mode_indices:
                source_mode_no = int(modes_array[mode_index])
                frequency, eigenvalue, _warnings = _extract_mode_frequency(eigen_data, mode_index)
                mode_data = np.asarray(eigen_data.data[mode_index], dtype=np.float64)

                for node_id in active_node_ids:
                    result_idx = result_node_index.get(int(node_id))
                    if result_idx is None:
                        continue
                    values = mode_data[result_idx]
                    rows.append((
                        int(PROJECT_ID),
                        int(import_mode_no),
                        float(frequency) if frequency is not None else None,
                        str(instance_name or "BDF_MODEL"),
                        str(part_name or instance_name or "BDF_MODEL"),
                        int(node_id),
                        float(values[0]) if len(values) > 0 else 0.0,
                        float(values[1]) if len(values) > 1 else 0.0,
                        float(values[2]) if len(values) > 2 else 0.0,
                        (
                            "{"
                            f"\"node_id\": {int(node_id)}, "
                            f"\"subcase_id\": {int(subcase_id)}, "
                            f"\"source_mode_no\": {int(source_mode_no)}, "
                            f"\"eigenvalue\": {('null' if eigenvalue is None else float(eigenvalue))}, "
                            f"\"ur1\": {float(values[3]) if len(values) > 3 else 0.0}, "
                            f"\"ur2\": {float(values[4]) if len(values) > 4 else 0.0}, "
                            f"\"ur3\": {float(values[5]) if len(values) > 5 else 0.0}"
                            "}"
                        ),
                    ))
                    if len(rows) >= int(CHUNK_SIZE):
                        cursor.executemany(sql, rows)
                        conn.commit()
                        total_rows += len(rows)
                        rows = []

                total_modes += 1
                import_mode_no += 1

        if rows:
            cursor.executemany(sql, rows)
            conn.commit()
            total_rows += len(rows)

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    print("project_id:", int(PROJECT_ID))
    print("subcase_count:", len(subcases))
    print("mode_count:", total_modes)
    print("row_count:", total_rows)
