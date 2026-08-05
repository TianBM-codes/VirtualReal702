"""FEM modal/static result import helpers."""

import os

import numpy as np

from .fem_response_service import *
from .fem_catalog_service import _json_dumps, _json_loads, _safe_float
from . import sensitivity_service as _sens
from .fem_modal_bundle_service import (
    clear_fem_modal_bundle,
    load_fem_modal_manifest,
    save_fem_modal_manifest,
)
from .project_path_service import resolve_project_cal_subdir

def _load_modal_payload(file_path=None, modes=None) -> List[dict]:
    if file_path:
        with open(file_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        modes = payload.get("modes", []) if isinstance(payload, dict) else payload
    if not modes:
        raise ValueError("modes 数据为空")
    return [dict(item) for item in modes]


def _resolve_modal_node_identity(node_item: dict) -> tuple[str, str]:
    instance_name = str(node_item.get("instance_name") or "").strip()
    part_name = str(node_item.get("part_name") or "").strip()
    if not instance_name:
        instance_name = "BDF_MODEL"
    if not part_name:
        part_name = instance_name or "BDF_MODEL"
    return instance_name, part_name


def _normalize_modal_mode(mode_item: dict) -> dict:
    nodes = []
    for node_item in list(mode_item.get("nodes") or []):
        vector = node_item.get("vector")
        u1 = node_item.get("u1")
        u2 = node_item.get("u2")
        u3 = node_item.get("u3")
        if vector is not None:
            vector = list(vector)
            if u1 is None and len(vector) > 0:
                u1 = vector[0]
            if u2 is None and len(vector) > 1:
                u2 = vector[1]
            if u3 is None and len(vector) > 2:
                u3 = vector[2]

        instance_name, part_name = _resolve_modal_node_identity(node_item)
        nodes.append(
            {
                "instance_name": instance_name,
                "part_name": part_name,
                "fem_node_label": int(node_item["fem_node_label"]),
                "u1": _safe_float(u1) or 0.0,
                "u2": _safe_float(u2) or 0.0,
                "u3": _safe_float(u3) or 0.0,
                "extra_json": dict(node_item.get("extra_json") or {}),
            }
        )

    return {
        "mode_no": int(mode_item["mode_no"]),
        "frequency": _safe_float(mode_item.get("frequency")),
        "source_mode_no": None if mode_item.get("source_mode_no") is None else int(mode_item.get("source_mode_no")),
        "eigenvalue": None if mode_item.get("eigenvalue") is None else float(mode_item.get("eigenvalue")),
        "subcase_id": None if mode_item.get("subcase_id") is None else int(mode_item.get("subcase_id")),
        "nodes": nodes,
    }


def _save_modal_modes_to_bundle(project_id: int, modal_modes: List[dict], *, overwrite: bool, cursor=None) -> tuple[dict, int, list]:
    bundle_dir = (
        clear_fem_modal_bundle(int(project_id))
        if overwrite
        else resolve_project_cal_subdir(int(project_id), "fem_modal_bundle")
    )
    manifest_modes = []
    row_count = 0
    preview = []
    bundle_instance_name = None
    bundle_part_name = None

    for mode_item in modal_modes:
        normalized = _normalize_modal_mode(mode_item)
        node_labels = []
        vectors = []
        instance_name = None
        part_name = None
        for node_item in normalized["nodes"]:
            node_labels.append(int(node_item["fem_node_label"]))
            vectors.append([float(node_item["u1"]), float(node_item["u2"]), float(node_item["u3"])])
            instance_name = instance_name or str(node_item["instance_name"] or "BDF_MODEL")
            part_name = part_name or str(node_item["part_name"] or instance_name or "BDF_MODEL")
            if len(preview) < 20:
                preview.append(
                    {
                        "mode_no": int(normalized["mode_no"]),
                        "frequency": normalized["frequency"],
                        "instance_name": str(node_item["instance_name"]),
                        "part_name": str(node_item["part_name"]),
                        "fem_node_label": int(node_item["fem_node_label"]),
                        "u1": float(node_item["u1"]),
                        "u2": float(node_item["u2"]),
                        "u3": float(node_item["u3"]),
                        "extra_json": dict(node_item.get("extra_json") or {}),
                    }
                )

        file_path = os.path.join(bundle_dir, f"mode_{int(normalized['mode_no']):04d}.npz")
        np.savez_compressed(
            file_path,
            node_labels=np.asarray(node_labels, dtype=np.int32),
            vectors=np.asarray(vectors, dtype=np.float32),
        )
        manifest_modes.append(
            {
                "mode_no": int(normalized["mode_no"]),
                "frequency": normalized["frequency"],
                "subcase_id": normalized["subcase_id"],
                "source_mode_no": normalized["source_mode_no"],
                "eigenvalue": normalized["eigenvalue"],
                "instance_name": str(instance_name or "BDF_MODEL"),
                "part_name": str(part_name or instance_name or "BDF_MODEL"),
                "file_path": os.path.abspath(file_path),
                "node_count": int(len(node_labels)),
            }
        )
        bundle_instance_name = bundle_instance_name or str(instance_name or "BDF_MODEL")
        bundle_part_name = bundle_part_name or str(part_name or instance_name or "BDF_MODEL")
        row_count += len(node_labels)

    manifest = save_fem_modal_manifest(
        int(project_id),
        {
            "source_file_path": None,
            "instance_name": str(bundle_instance_name or "BDF_MODEL"),
            "part_name": str(bundle_part_name or bundle_instance_name or "BDF_MODEL"),
            "modes": manifest_modes,
        },
        cursor=cursor,
    )
    return manifest, row_count, preview


def import_fe_modal_results(project_id, overwrite=True, file_path=None, modes=None):
    # Import solver modal results into a flat per-node table so the later
    # correlation pass can stream them mode-by-mode from SQL.
    ensure_tables_exist()
    modal_modes = _load_modal_payload(file_path=file_path, modes=modes)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        log_project_step(
            int(project_id),
            "开始导入 FEM 模态结果",
            stage="modal_store_started",
            percent=0,
        )
        if overwrite:
            log_project_info(
                int(project_id),
                "将覆盖旧的 FEM 模态结果与模态相关性数据",
                stage="modal_store_overwrite",
                percent=10,
            )
            cursor.execute("DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        insert_sql = """
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

        row_count = 0
        preview = []
        for mode_item in modal_modes:
            mode_no = int(mode_item["mode_no"])
            frequency = _safe_float(mode_item.get("frequency"))
            for node_item in mode_item.get("nodes", []):
                vector = node_item.get("vector")
                u1 = node_item.get("u1")
                u2 = node_item.get("u2")
                u3 = node_item.get("u3")
                if vector is not None:
                    # Accept either the explicit u1/u2/u3 schema or a compact
                    # "vector" payload from external conversion scripts.
                    vector = list(vector)
                    if u1 is None:
                        u1 = vector[0]
                    if u2 is None:
                        u2 = vector[1]
                    if u3 is None:
                        u3 = vector[2]

                instance_name, part_name = _resolve_modal_node_identity(node_item)
                payload = {
                    "mode_no": mode_no,
                    "frequency": frequency,
                    "instance_name": instance_name,
                    "part_name": part_name,
                    "fem_node_label": int(node_item["fem_node_label"]),
                    "u1": _safe_float(u1),
                    "u2": _safe_float(u2),
                    "u3": _safe_float(u3),
                    "extra_json": node_item.get("extra_json") or {},
                }
                cursor.execute(insert_sql, (
                    project_id,
                    payload["mode_no"],
                    payload["frequency"],
                    payload["instance_name"],
                    payload["part_name"],
                    payload["fem_node_label"],
                    payload["u1"],
                    payload["u2"],
                    payload["u3"],
                    _json_dumps(payload["extra_json"]),
                ))
                row_count += 1
                if len(preview) < 20:
                    preview.append(payload)

        conn.commit()
        log_project_step(
            int(project_id),
            f"FEM 模态结果入库完成，模态 {len(modal_modes)} 阶，记录 {row_count} 条",
            stage="modal_store_finished",
            percent=100,
        )
        return {
            "project_id": project_id,
            "mode_count": len(modal_modes),
            "row_count": row_count,
            "source_file_path": os.path.abspath(file_path) if file_path else None,
            "rows_preview": preview,
        }
    except Exception as exc:
        conn.rollback()
        log_project_error(
            int(project_id),
            f"FEM 模态结果入库失败: {exc}",
            stage="failed",
        )
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_modal_results(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at
            FROM t_mt_py_fem_modal_result
            WHERE pid = %s
            ORDER BY mode_no, instance_name, fem_node_label
        """, (project_id,))
        return {
            "project_id": project_id,
            "rows": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


def import_fe_modal_results(project_id, overwrite=True, file_path=None, modes=None):
    ensure_tables_exist()
    modal_modes = _load_modal_payload(file_path=file_path, modes=modes)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        log_project_step(int(project_id), "开始导入 FEM 模态结果", stage="modal_store_started", percent=0)
        if overwrite:
            log_project_info(int(project_id), "将覆盖旧的 FEM 模态结果与模态相关性数据", stage="modal_store_overwrite", percent=10)
            cursor.execute("DELETE FROM t_mt_py_fem_modal_result WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        manifest, row_count, preview = _save_modal_modes_to_bundle(
            int(project_id),
            modal_modes,
            overwrite=bool(overwrite),
            cursor=cursor,
        )
        if file_path:
            manifest["source_file_path"] = os.path.abspath(file_path)
            save_fem_modal_manifest(int(project_id), manifest, cursor=cursor)

        conn.commit()
        log_project_step(int(project_id), f"FEM 模态结果导入完成，模态 {len(modal_modes)} 阶，节点向量 {row_count} 条", stage="modal_store_finished", percent=100)
        return {
            "project_id": project_id,
            "mode_count": len(modal_modes),
            "row_count": row_count,
            "source_file_path": os.path.abspath(file_path) if file_path else None,
            "bundle_manifest_path": manifest.get("manifest_path"),
            "rows_preview": preview,
        }
    except Exception as exc:
        conn.rollback()
        log_project_error(int(project_id), f"FEM 模态结果导入失败: {exc}", stage="failed")
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_modal_results(project_id):
    manifest = load_fem_modal_manifest(int(project_id))
    if manifest:
        rows = []
        for mode_item in list(manifest.get("modes") or []):
            file_path = str(mode_item.get("file_path") or "").strip()
            if not file_path or not os.path.isfile(file_path):
                continue
            data = np.load(file_path)
            node_labels = np.asarray(data["node_labels"], dtype=np.int64)
            vectors = np.asarray(data["vectors"], dtype=np.float64)
            instance_name = str(mode_item.get("instance_name") or manifest.get("instance_name") or "BDF_MODEL")
            part_name = str(mode_item.get("part_name") or manifest.get("part_name") or instance_name or "BDF_MODEL")
            for idx, node_label in enumerate(node_labels.tolist()):
                vector = vectors[idx] if idx < len(vectors) else np.zeros(3, dtype=np.float64)
                rows.append(
                    {
                        "mode_no": int(mode_item["mode_no"]),
                        "frequency": _safe_float(mode_item.get("frequency")),
                        "instance_name": instance_name,
                        "part_name": part_name,
                        "fem_node_label": int(node_label),
                        "u1": float(vector[0]) if len(vector) > 0 else 0.0,
                        "u2": float(vector[1]) if len(vector) > 1 else 0.0,
                        "u3": float(vector[2]) if len(vector) > 2 else 0.0,
                        "extra_json": {},
                        "created_at": None,
                    }
                )
        rows.sort(key=lambda item: (int(item["mode_no"]), str(item["instance_name"]), int(item["fem_node_label"])))
        return {
            "project_id": project_id,
            "bundle_manifest_path": manifest.get("manifest_path"),
            "rows": rows,
        }

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT mode_no, frequency, instance_name, part_name, fem_node_label, u1, u2, u3, extra_json, created_at
            FROM t_mt_py_fem_modal_result
            WHERE pid = %s
            ORDER BY mode_no, instance_name, fem_node_label
        """, (project_id,))
        return {
            "project_id": project_id,
            "rows": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


def _manifest_result_group_clause(result_group: Optional[str], prefix: str = "") -> Tuple[str, List[str]]:
    column = f"{prefix}result_group"
    if result_group is None:
        return f"{column} IS NULL", []
    return f"{column} = ?", [str(result_group)]


def _registry_repo() -> RegistryRepo:
    return RegistryRepo(settings.registry_db_path)


def _resolve_project_result_workspace(project_id: int, result_group: str) -> Tuple[str, dict]:
    repo = _registry_repo()
    project_row = repo.get_project(str(project_id))
    if project_row is None:
        raise NotFoundError(f"project '{project_id}' not found", {"project_id": int(project_id)})

    result_group_row = repo.get_result_group(str(project_id), str(result_group))
    if result_group_row is None:
        raise NotFoundError(
            f"result_group '{result_group}' not found for project '{project_id}'",
            {"project_id": int(project_id), "result_group": str(result_group)},
        )
    if str(result_group_row["status"] or "") != "ready":
        raise ValidationError(
            f"result_group '{result_group}' is not ready",
            {
                "project_id": int(project_id),
                "result_group": str(result_group),
                "status": result_group_row["status"],
            },
        )

    workspace = repo.resolve_workspace(str(project_row["workspace"]), settings.data_root)
    workspace_abs = _sens._workspace_path(workspace)
    return workspace_abs, dict(result_group_row)


def _collect_project_result_static_rows(
        *,
        project_id: int,
        result_group: str,
        step: Optional[str],
        frame: Optional[int],
        instances: Optional[List[str]],
) -> dict:
    workspace_abs, _ = _resolve_project_result_workspace(project_id, result_group)
    conn = _sens._manifest_conn(workspace_abs)
    try:
        rg_clause, rg_params = _manifest_result_group_clause(result_group)
        step_rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT step_name, step_number FROM steps WHERE {rg_clause} ORDER BY step_number, step_name",
                rg_params,
            ).fetchall()
        ]
        chosen_step = str(step) if step is not None else _sens._default_step_from_rows(step_rows)
        if not chosen_step:
            raise NotFoundError(
                "no steps found for project result group",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "workspace": workspace_abs,
                },
            )
        if chosen_step not in {str(row.get("step_name")) for row in step_rows}:
            available = [str(row.get("step_name")) for row in step_rows if row.get("step_name") is not None]
            raise NotFoundError(
                f"step '{chosen_step}' not found under result_group '{result_group}'",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                    "available_steps": available,
                },
            )

        frame_rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT frame_idx, frame_value, description FROM frames WHERE step_name = ? AND {rg_clause} ORDER BY frame_idx",
                [chosen_step] + rg_params,
            ).fetchall()
        ]
        if not frame_rows:
            raise NotFoundError(
                "no frames found for project result step",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                },
            )
        available_frames = [int(row["frame_idx"]) for row in frame_rows]
        chosen_frame = int(frame) if frame is not None else int(available_frames[-1])
        if chosen_frame not in set(available_frames):
            raise ValidationError(
                f"frame {chosen_frame} not found under step '{chosen_step}'",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                    "frame": chosen_frame,
                    "available_frames": available_frames,
                },
            )

        available_instances = [
            str(row["instance_name"])
            for row in conn.execute(
                f"""
                SELECT DISTINCT instance_name
                FROM result_blocks
                WHERE step_name = ? AND field_name = 'U' AND position = 'NODAL' AND {rg_clause}
                ORDER BY instance_name
                """,
                [chosen_step] + rg_params,
            ).fetchall()
        ]
        if not available_instances:
            raise NotFoundError(
                "nodal displacement field 'U' not found in project result group",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "step": chosen_step,
                },
            )
        if instances:
            chosen_instances = [str(item) for item in instances]
            missing = [item for item in chosen_instances if item not in available_instances]
            if missing:
                raise NotFoundError(
                    "some instances are not available in the selected project result group",
                    {
                        "project_id": int(project_id),
                        "result_group": str(result_group),
                        "missing_instances": missing,
                        "available_instances": available_instances,
                    },
                )
        else:
            chosen_instances = available_instances

        part_name_map = {}
        try:
            for row in conn.execute("SELECT instance_name, part_name FROM instances ORDER BY instance_name").fetchall():
                part_name_map[str(row["instance_name"])] = row["part_name"]
        except Exception:
            part_name_map = {}
    finally:
        conn.close()

    rows = []
    for instance_name in chosen_instances:
        comp_maps = {
            "U1": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=chosen_frame,
                aggregation="max_abs",
                component="U1",
                result_group=str(result_group),
            ),
            "U2": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=chosen_frame,
                aggregation="max_abs",
                component="U2",
                result_group=str(result_group),
            ),
            "U3": _sens._workspace_result_label_map(
                workspace_abs,
                step=chosen_step,
                field="U",
                instance=instance_name,
                position="NODAL",
                frame=chosen_frame,
                aggregation="max_abs",
                component="U3",
                result_group=str(result_group),
            ),
        }
        scoped_labels = sorted(
            set(comp_maps["U1"].keys()) | set(comp_maps["U2"].keys()) | set(comp_maps["U3"].keys()),
            key=lambda item: int(str(item).split("::", 1)[-1]),
        )
        for scoped_label in scoped_labels:
            label_text = str(scoped_label).split("::", 1)[-1]
            rows.append(
                {
                    "instance_name": instance_name,
                    "part_name": part_name_map.get(instance_name),
                    "fem_node_label": int(label_text),
                    "u1": _safe_float(comp_maps["U1"].get(scoped_label)),
                    "u2": _safe_float(comp_maps["U2"].get(scoped_label)),
                    "u3": _safe_float(comp_maps["U3"].get(scoped_label)),
                    "extra_json": {
                        "source": "project_result_group",
                        "project_id": int(project_id),
                        "result_group": str(result_group),
                        "step_name": chosen_step,
                        "frame_idx": int(chosen_frame),
                    },
                }
            )

    if not rows:
        raise NotFoundError(
            "no nodal displacement rows resolved from project result group",
            {
                "project_id": int(project_id),
                "result_group": str(result_group),
                "step": chosen_step,
                "frame": int(chosen_frame),
                "instances": chosen_instances,
            },
        )

    return {
        "project_id": int(project_id),
        "workspace": workspace_abs,
        "result_group": str(result_group),
        "step_name": chosen_step,
        "frame_idx": int(chosen_frame),
        "instances": chosen_instances,
        "rows": rows,
    }


def list_project_result_steps(*, project_id: int, result_group: str) -> dict:
    workspace_abs, _ = _resolve_project_result_workspace(project_id, result_group)
    conn = _sens._manifest_conn(workspace_abs)
    try:
        rg_clause, rg_params = _manifest_result_group_clause(result_group)
        step_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT step_name, step_number, procedure, num_frames, description
                FROM steps
                WHERE {rg_clause}
                ORDER BY step_number, step_name
                """,
                rg_params,
            ).fetchall()
        ]
        if not step_rows:
            raise NotFoundError(
                "no steps found for project result group",
                {
                    "project_id": int(project_id),
                    "result_group": str(result_group),
                    "workspace": workspace_abs,
                },
            )

        frame_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT step_name, frame_idx, frame_value, description
                FROM frames
                WHERE {rg_clause}
                ORDER BY step_name, frame_idx
                """,
                rg_params,
            ).fetchall()
        ]
        field_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT step_name, field_name, components, invariants, positions
                FROM result_files
                WHERE {rg_clause}
                ORDER BY step_name, field_name
                """,
                rg_params,
            ).fetchall()
        ]
    finally:
        conn.close()

    frames_by_step = {}
    for row in frame_rows:
        step_name = str(row.get("step_name") or "")
        frames_by_step.setdefault(step_name, []).append(
            {
                "frame_idx": int(row.get("frame_idx") or 0),
                "frame_value": _safe_float(row.get("frame_value")),
                "description": row.get("description"),
            }
        )

    fields_by_step = {}
    for row in field_rows:
        step_name = str(row.get("step_name") or "")
        fields_by_step.setdefault(step_name, []).append(
            {
                "field_name": str(row.get("field_name") or ""),
                "components": _json_loads(row.get("components")) or [],
                "invariants": _json_loads(row.get("invariants")) or [],
                "positions": _json_loads(row.get("positions")) or [],
            }
        )

    steps = []
    for row in step_rows:
        step_name = str(row.get("step_name") or "")
        frames = list(frames_by_step.get(step_name) or [])
        steps.append(
            {
                "step_name": step_name,
                "step_number": row.get("step_number"),
                "procedure": row.get("procedure"),
                "num_frames": row.get("num_frames"),
                "description": row.get("description"),
                "frames": frames,
                "fields": list(fields_by_step.get(step_name) or []),
                "default_frame_idx": None if not frames else int(frames[-1]["frame_idx"]),
            }
        )

    return {
        "project_id": int(project_id),
        "result_group": str(result_group),
        "workspace": workspace_abs,
        "steps": steps,
    }


def _load_static_result_rows_from_txt(file_path: str) -> List[dict]:
    # Text imports follow the legacy fixed-column export layout used by current
    # FEMTools comparison files: node label plus 6 displacement/rotation values.
    rows: List[dict] = []
    with open(file_path, "r", encoding="utf-8") as fp:
        lines = fp.readlines()

    if len(lines) <= 3:
        raise ValueError("static result txt does not contain data rows after the first three header lines")

    for line_no, raw_line in enumerate(lines[3:], start=4):
        text = raw_line.strip()
        if not text:
            continue

        parts = text.replace(",", " ").split()
        if len(parts) < 7:
            raise ValueError(f"invalid static result line {line_no}: expected 7 columns, got {len(parts)}")

        rows.append({
            "fem_node_label": int(parts[0]),
            "u1": float(parts[1]),
            "u2": float(parts[2]),
            "u3": float(parts[3]),
            "ur1": float(parts[4]),
            "ur2": float(parts[5]),
            "ur3": float(parts[6]),
        })

    if not rows:
        raise ValueError("static result txt has no valid data rows")
    return rows


def _load_static_result_payload(file_path=None, rows=None) -> List[dict]:
    if file_path:
        return _load_static_result_rows_from_txt(file_path)

    if not rows:
        raise ValueError("static result payload is empty")

    normalized = []
    for item in rows:
        item = dict(item)
        fem_node_label = item.get("fem_node_label", item.get("node_label", item.get("node")))
        if fem_node_label is None:
            raise ValueError("static result row missing fem_node_label/node_label/node")

        normalized.append({
            "fem_node_label": int(fem_node_label),
            "u1": _safe_float(item.get("u1", item.get("ux"))),
            "u2": _safe_float(item.get("u2", item.get("uy"))),
            "u3": _safe_float(item.get("u3", item.get("uz"))),
            "ur1": _safe_float(item.get("ur1", item.get("rx"))),
            "ur2": _safe_float(item.get("ur2", item.get("ry"))),
            "ur3": _safe_float(item.get("ur3", item.get("rz"))),
            "instance_name": item.get("instance_name"),
            "part_name": item.get("part_name"),
            "load_case_no": item.get("load_case_no"),
            "extra_json": item.get("extra_json") or {},
        })
    return normalized


def _persist_fe_static_results(
        cursor,
        *,
        project_id: int,
        static_rows: Sequence[dict],
        load_case_no=1,
        instance_name=None,
        part_name=None,
        source_file_path: Optional[str] = None,
) -> dict:
    insert_sql = """
    INSERT INTO t_mt_py_fem_static_result
    (pid, load_case_no, instance_name, part_name, fem_node_label, u1, u2, u3, ur1, ur2, ur3, extra_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        part_name = VALUES(part_name),
        u1 = VALUES(u1),
        u2 = VALUES(u2),
        u3 = VALUES(u3),
        ur1 = VALUES(ur1),
        ur2 = VALUES(ur2),
        ur3 = VALUES(ur3),
        extra_json = VALUES(extra_json),
        created_at = CURRENT_TIMESTAMP
    """

    row_count = 0
    preview = []
    case_nos = set()
    for item in static_rows:
        item_load_case_no = int(item.get("load_case_no") or load_case_no or 1)
        item_instance_name = item.get("instance_name") if item.get("instance_name") is not None else instance_name
        item_part_name = item.get("part_name") if item.get("part_name") is not None else part_name
        extra_json = dict(item.get("extra_json") or {})
        if source_file_path:
            extra_json["source_file_path"] = source_file_path

        payload = {
            "load_case_no": item_load_case_no,
            "instance_name": item_instance_name,
            "part_name": item_part_name,
            "fem_node_label": int(item["fem_node_label"]),
            "u1": _safe_float(item.get("u1")),
            "u2": _safe_float(item.get("u2")),
            "u3": _safe_float(item.get("u3")),
            "ur1": _safe_float(item.get("ur1")),
            "ur2": _safe_float(item.get("ur2")),
            "ur3": _safe_float(item.get("ur3")),
            "extra_json": extra_json,
        }
        cursor.execute(insert_sql, (
            int(project_id),
            payload["load_case_no"],
            payload["instance_name"],
            payload["part_name"],
            payload["fem_node_label"],
            payload["u1"],
            payload["u2"],
            payload["u3"],
            payload["ur1"],
            payload["ur2"],
            payload["ur3"],
            _json_dumps(payload["extra_json"]),
        ))
        row_count += 1
        case_nos.add(payload["load_case_no"])
        if len(preview) < 20:
            preview.append(payload)

    return {
        "project_id": int(project_id),
        "load_case_nos": sorted(case_nos),
        "row_count": row_count,
        "source_file_path": source_file_path,
        "rows_preview": preview,
    }


def import_fe_static_results(project_id, overwrite=True, file_path=None, rows=None,
                             load_case_no=1, instance_name=None, part_name=None):
    # Static results are stored with both translational and rotational
    # components so UX/UY/UZ and RX/RY/RZ can be correlated independently.
    ensure_tables_exist()
    static_rows = _load_static_result_payload(file_path=file_path, rows=rows)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        log_project_step(
            int(project_id),
            f"开始导入 FEM 静力结果，载荷工况 {int(load_case_no)}",
            stage="static_store_started",
            percent=0,
        )
        if overwrite:
            log_project_info(
                int(project_id),
                "将覆盖旧的 FEM 静力结果",
                stage="static_store_overwrite",
                percent=10,
            )
            cursor.execute("DELETE FROM t_mt_py_fem_static_result WHERE pid = %s", (project_id,))
        source_file_path = os.path.abspath(file_path) if file_path else None
        result = _persist_fe_static_results(
            cursor,
            project_id=int(project_id),
            static_rows=static_rows,
            load_case_no=load_case_no,
            instance_name=instance_name,
            part_name=part_name,
            source_file_path=source_file_path,
        )
        conn.commit()
        log_project_step(
            int(project_id),
            f"FEM 静力结果入库完成，载荷工况 {int(load_case_no)}，记录 {int(result['row_count'])} 条",
            stage="static_store_finished",
            percent=100,
        )
        return result
    except Exception as exc:
        conn.rollback()
        log_project_error(
            int(project_id),
            f"FEM 静力结果入库失败: {exc}",
            stage="failed",
        )
        raise
    finally:
        cursor.close()
        conn.close()


def import_fe_static_results_from_project_result(
        *,
        project_id: int,
        result_group: str,
        load_case_no: int,
        step: Optional[str] = None,
        frame: Optional[int] = None,
        instances: Optional[List[str]] = None,
        overwrite: bool = True,
):
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        log_project_step(
            int(project_id),
            f"开始从结果组导入 FEM 静力结果，结果组 {result_group}，载荷工况 {int(load_case_no)}",
            stage="project_result_store_started",
            percent=0,
        )
        static_payload = _collect_project_result_static_rows(
            project_id=int(project_id),
            result_group=str(result_group),
            step=step,
            frame=frame,
            instances=instances,
        )
        static_rows = _load_static_result_payload(
            rows=[
                {
                    **row,
                    "load_case_no": int(load_case_no),
                }
                for row in static_payload["rows"]
            ]
        )
        if overwrite:
            log_project_info(
                int(project_id),
                f"将覆盖结果组 {result_group} 对应载荷工况 {int(load_case_no)} 的旧静力结果",
                stage="project_result_store_overwrite",
                percent=15,
            )
            cursor.execute(
                "DELETE FROM t_mt_py_fem_static_result WHERE pid = %s AND load_case_no = %s",
                (int(project_id), int(load_case_no)),
            )
        result = _persist_fe_static_results(
            cursor,
            project_id=int(project_id),
            static_rows=static_rows,
            load_case_no=int(load_case_no),
        )
        conn.commit()
        result.update(
            {
                "result_group": str(result_group),
                "workspace": static_payload["workspace"],
                "step_name": static_payload["step_name"],
                "frame_idx": int(static_payload["frame_idx"]),
                "instances": list(static_payload["instances"]),
                "overwrite": bool(overwrite),
            }
        )
        log_project_step(
            int(project_id),
            f"结果组静力结果入库完成，结果组 {result_group}，记录 {int(result['row_count'])} 条",
            stage="project_result_store_finished",
            percent=100,
        )
        return result
    except Exception as exc:
        conn.rollback()
        log_project_error(
            int(project_id),
            f"结果组静力结果入库失败: {exc}",
            stage="failed",
        )
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_static_results(project_id, load_case_no=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if load_case_no is None:
            cursor.execute("""
                SELECT load_case_no, instance_name, part_name, fem_node_label,
                       u1, u2, u3, ur1, ur2, ur3, extra_json, created_at
                FROM t_mt_py_fem_static_result
                WHERE pid = %s
                ORDER BY load_case_no, instance_name, fem_node_label
            """, (project_id,))
        else:
            cursor.execute("""
                SELECT load_case_no, instance_name, part_name, fem_node_label,
                       u1, u2, u3, ur1, ur2, ur3, extra_json, created_at
                FROM t_mt_py_fem_static_result
                WHERE pid = %s AND load_case_no = %s
                ORDER BY load_case_no, instance_name, fem_node_label
            """, (project_id, int(load_case_no)))
        return {
            "project_id": project_id,
            "load_case_no": None if load_case_no is None else int(load_case_no),
            "rows": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()


STATIC_COMPONENT_MAP = {
    "UX": ("ux", "u1"),
    "UY": ("uy", "u2"),
    "UZ": ("uz", "u3"),
    "RX": ("rx", "ur1"),
    "RY": ("ry", "ur2"),
    "RZ": ("rz", "ur3"),
}
_STATIC_TEST_DATA_DISPLACEMENT_TYPES = {"21", "位移", "位移传感器", "displacement", "displacement_sensor"}
