import json
from pathlib import Path

from db import ensure_tables_exist, get_connection
from services.model_update.analysis.inp_service import _SUPPORTED_CORRECTION_QUANTITIES
from services.model_update.analysis.project_config_service import save_fem_model_dimensions, upsert_project_config


BUNDLE_JSON = r"D:\temp\bdf_bundle\model.octree_bundle.json"
PROJECT_ID = None
CLEAR_BEFORE_INSERT = True


if __name__ == "__main__":
    ensure_tables_exist()

    bundle_path = Path(BUNDLE_JSON).expanduser().resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    pid = int(PROJECT_ID if PROJECT_ID is not None else bundle["project_id"])
    source_file_path = str(Path(bundle["source_file_path"]).expanduser().resolve())
    cache_file_path = str(Path(bundle["cache_file_path"]).expanduser().resolve())
    detail_file = str(Path(bundle.get("capability_detail_file") or "").expanduser().resolve()) if bundle.get("capability_detail_file") else ""
    bbox_min = list(bundle["bbox_min"])
    bbox_max = list(bundle["bbox_max"])
    capabilities = [dict(x or {}) for x in list(bundle.get("quantity_set_capabilities") or [])]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if CLEAR_BEFORE_INSERT:
            cursor.execute("DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = %s", (pid,))
            cursor.execute("DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = %s", (pid,))

        sql1 = """
        INSERT INTO t_mt_py_fem_supported_quantity
        (quantity_code, quantity_name, unit, enabled, sort_no)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            quantity_name = VALUES(quantity_name),
            unit = VALUES(unit),
            enabled = VALUES(enabled),
            sort_no = VALUES(sort_no)
        """
        for x in _SUPPORTED_CORRECTION_QUANTITIES:
            cursor.execute(sql1, (x["quantity_code"], x["quantity_name"], x.get("unit"), int(x.get("enabled", 1)), int(x.get("sort_no", 0))))

        sql2 = """
        INSERT INTO t_mt_py_fem_quantity_set_capability
        (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name,
         set_role, element_family, section_type, material_name, member_count,
         supports_global, supports_local, current_value, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            set_role = VALUES(set_role),
            element_family = VALUES(element_family),
            section_type = VALUES(section_type),
            material_name = VALUES(material_name),
            member_count = VALUES(member_count),
            supports_global = VALUES(supports_global),
            supports_local = VALUES(supports_local),
            current_value = VALUES(current_value),
            extra_json = VALUES(extra_json)
        """
        for x in capabilities:
            cursor.execute(
                sql2,
                (
                    pid, x["quantity_code"], x["set_name"], x["set_type"], x["set_scope"],
                    x.get("instance_name"), x.get("part_name"), x["set_role"], x.get("element_family"),
                    x.get("section_type"), x.get("material_name"), int(x.get("member_count", 0)),
                    1 if x.get("supports_global") else 0, 1 if x.get("supports_local") else 0,
                    x.get("current_value"), json.dumps(x.get("extra_json") or {}, ensure_ascii=False),
                ),
            )

        sql3 = """
        INSERT INTO t_mt_py_fem_node_octree_cache
        (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
            cache_file_path = VALUES(cache_file_path),
            node_count = VALUES(node_count),
            instance_count = VALUES(instance_count),
            bbox_min = VALUES(bbox_min),
            bbox_max = VALUES(bbox_max),
            updated_at = CURRENT_TIMESTAMP
        """
        cursor.execute(
            sql3,
            (
                pid, source_file_path, cache_file_path, int(bundle["node_count"]),
                int(bundle.get("instance_count", 1)), str(bbox_min), str(bbox_max),
            ),
        )

        save_fem_model_dimensions(project_id=pid, bbox_min=bbox_min, bbox_max=bbox_max, cursor=cursor)
        upsert_project_config(
            pid,
            extra_json={
                "fem_data_source": "bdf_octree_bundle",
                "fem_octree_source_file": source_file_path,
                "fem_octree_bundle_json": str(bundle_path),
                "fem_octree_capability_detail_json": detail_file,
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

    print("project_id:", pid)
    print("cache_file_path:", cache_file_path)
    print("node_count:", int(bundle["node_count"]))
    print("capability_count:", len(capabilities))
