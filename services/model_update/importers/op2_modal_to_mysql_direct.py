from pathlib import Path

import numpy as np

from db import ensure_tables_exist, get_connection
from services.model_update.analysis.fem_modal_bundle_service import (
    clear_fem_modal_bundle,
    save_fem_modal_manifest,
)
from services.model_update.analysis.project_path_service import resolve_project_cal_subdir
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
    cursor = conn.cursor(dictionary=True)
    try:
        bundle_dir = clear_fem_modal_bundle(int(PROJECT_ID)) if OVERWRITE else resolve_project_cal_subdir(int(PROJECT_ID), "fem_modal_bundle")
        if OVERWRITE:
            cursor.execute("DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s", (int(PROJECT_ID),))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (int(PROJECT_ID),))
            conn.commit()
        manifest_modes = []
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
                node_labels = []
                vectors = []

                for node_id in active_node_ids:
                    result_idx = result_node_index.get(int(node_id))
                    if result_idx is None:
                        continue
                    values = mode_data[result_idx]
                    node_labels.append(int(node_id))
                    vectors.append([
                        float(values[0]) if len(values) > 0 else 0.0,
                        float(values[1]) if len(values) > 1 else 0.0,
                        float(values[2]) if len(values) > 2 else 0.0,
                    ])

                file_path = str(Path(bundle_dir) / f"mode_{int(import_mode_no):04d}.npz")
                np.savez_compressed(
                    file_path,
                    node_labels=np.asarray(node_labels, dtype=np.int32),
                    vectors=np.asarray(vectors, dtype=np.float32),
                )
                manifest_modes.append(
                    {
                        "mode_no": int(import_mode_no),
                        "frequency": None if frequency is None else float(frequency),
                        "subcase_id": int(subcase_id),
                        "source_mode_no": int(source_mode_no),
                        "eigenvalue": None if eigenvalue is None else float(eigenvalue),
                        "instance_name": str(instance_name or "BDF_MODEL"),
                        "part_name": str(part_name or instance_name or "BDF_MODEL"),
                        "file_path": file_path,
                        "node_count": int(len(node_labels)),
                    }
                )
                total_rows += len(node_labels)

                total_modes += 1
                import_mode_no += 1

        save_fem_modal_manifest(
            int(PROJECT_ID),
            {
                "source_file_path": op2_path,
                "bdf_file_path": bdf_path,
                "instance_name": str(instance_name or "BDF_MODEL"),
                "part_name": str(part_name or instance_name or "BDF_MODEL"),
                "modes": manifest_modes,
            },
            cursor=cursor,
        )
        conn.commit()

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
