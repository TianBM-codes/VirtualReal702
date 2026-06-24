"""FEM response catalog helpers."""

import json

from .fem_matching_service import *
from .fem_catalog_service import (
    _ALLOWED_MODAL_RESPONSE_TYPES,
    _DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE,
    _DEFAULT_PARAMETER_USAGE_SCOPE,
    _DEFAULT_RESPONSE_SCATTER,
    _json_dumps,
    _normalize_modal_response_types,
    _normalize_parameter_usage_scope,
    _normalize_response_solver_scope,
    _parse_json_list,
    _safe_float,
    _scope_contains,
)
from services.model_update.analysis.sensitivity_service import _parse_optional_json_object


def _ensure_modal_correlation_rows_for_response(project_id: int):
    from .fem_correlation_service import _ensure_modal_correlation_rows

    return _ensure_modal_correlation_rows(project_id)


def _match_modal_modes_for_response(project_id: int, **kwargs):
    from .fem_correlation_service import match_modal_modes

    return match_modal_modes(project_id, **kwargs)


def _load_work_condition_project_config(project_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT project_config
            FROM t_mt_work_condition_project
            WHERE project_id = %s
            LIMIT 1
            """,
            (int(project_id),),
        )
        row = cursor.fetchone() or {}
    finally:
        cursor.close()
        conn.close()

    raw_config = row.get("project_config")
    if not raw_config:
        return {}
    if isinstance(raw_config, dict):
        project_config = raw_config
    else:
        try:
            project_config = json.loads(raw_config)
        except Exception as exc:
            raise ValidationError(
                "project_config is not valid JSON",
                {"project_id": int(project_id), "error": str(exc)},
            )
    return dict(project_config) if isinstance(project_config, dict) else {}


def _resolve_modal_frequency_options_mac_threshold(project_id: int) -> Optional[float]:
    project_config = _load_work_condition_project_config(int(project_id))
    mode_pair = project_config.get("mode_pair")
    if not isinstance(mode_pair, dict):
        return None
    raw_threshold = mode_pair.get("mac_threshold")
    if raw_threshold is None:
        return None
    return _normalize_modal_mac_threshold_value(raw_threshold)


def build_fe_response_catalog(project_id, overwrite=True, include_test_modes=True, include_node_dofs=True):
    # Build a normalized response directory that mixes modal frequencies and
    # matched nodal DOFs into one table for optimization/correlation consumers.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_dynamic_response_catalog WHERE pid = %s", (project_id,))

        rows = []
        seq_no = 1

        if include_test_modes:
            # Modal frequencies are treated as scalar responses alongside
            # displacement-based responses even though they do not map to nodes.
            cursor.execute("""
                SELECT mode_no, frequency
                FROM t_mt_py_test_modal_frequency
                WHERE pid = %s
                ORDER BY mode_no
            """, (project_id,))
            for row in cursor.fetchall():
                rows.append({
                    "response_code": f"MODE_FREQ:{int(row['mode_no'])}",
                    "response_name": f"Mode {int(row['mode_no'])} frequency",
                    "response_type": "MODAL_FREQUENCY",
                    "entity_type": "MODE",
                    "test_mode_no": int(row["mode_no"]),
                    "test_node_id": None,
                    "instance_name": None,
                    "part_name": None,
                    "fem_node_label": None,
                    "component": "FREQ",
                    "unit": "Hz",
                    "scatter": _DEFAULT_RESPONSE_SCATTER,
                    "seq_no": seq_no,
                    "source_table": "t_mt_py_test_modal_frequency",
                    "extra_json": {"test_frequency": _safe_float(row["frequency"])},
                })
                seq_no += 1

        if include_node_dofs:
            # DOF-based responses come from the FE/test DOF matching table and
            # preserve the resolved projection direction in extra_json.
            cursor.execute("""
                SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof,
                       direction_x, direction_y, direction_z, match_score
                FROM t_mt_py_fem_dof_match
                WHERE pid = %s
                ORDER BY test_node_id, test_dof
            """, (project_id,))
            dof_rows = cursor.fetchall()
            if not dof_rows and not rows:
                raise ValueError("未找到试验模态或自由度匹配结果，无法构建响应目录")

            for row in dof_rows:
                response_code = (
                    f"NODE_DOF:{row['instance_name'] or '_'}:{int(row['fem_node_label'])}:"
                    f"{row['fem_dof']}:{row['test_node_id']}:{row['test_dof']}"
                )
                rows.append({
                    "response_code": response_code,
                    "response_name": f"{row['instance_name'] or 'GLOBAL'}:{int(row['fem_node_label'])} {row['fem_dof']}",
                    "response_type": "NODAL_DISPLACEMENT",
                    "entity_type": "NODE_DOF",
                    "test_mode_no": None,
                    "test_node_id": str(row["test_node_id"]),
                    "instance_name": row["instance_name"],
                    "part_name": row["part_name"],
                    "fem_node_label": int(row["fem_node_label"]),
                    "component": row["fem_dof"],
                    "unit": None,
                    "scatter": _DEFAULT_RESPONSE_SCATTER,
                    "seq_no": seq_no,
                    "source_table": "t_mt_py_fem_dof_match",
                    "extra_json": {
                        "test_dof": row["test_dof"],
                        "direction": [float(row["direction_x"]), float(row["direction_y"]), float(row["direction_z"])],
                        "match_score": _safe_float(row["match_score"]),
                    },
                })
                seq_no += 1

        insert_sql = """
        INSERT INTO t_mt_py_fem_dynamic_response_catalog
        (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id,
         instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, source_table, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            response_name = VALUES(response_name),
            response_type = VALUES(response_type),
            entity_type = VALUES(entity_type),
            test_mode_no = VALUES(test_mode_no),
            test_node_id = VALUES(test_node_id),
            instance_name = VALUES(instance_name),
            part_name = VALUES(part_name),
            fem_node_label = VALUES(fem_node_label),
            component = VALUES(component),
            unit = VALUES(unit),
            scatter = VALUES(scatter),
            seq_no = VALUES(seq_no),
            source_table = VALUES(source_table),
            extra_json = VALUES(extra_json),
            created_at = CURRENT_TIMESTAMP
        """
        for item in rows:
            cursor.execute(insert_sql, (
                project_id,
                item["response_code"],
                item["response_name"],
                item["response_type"],
                item["entity_type"],
                item["test_mode_no"],
                item["test_node_id"],
                item["instance_name"],
                item["part_name"],
                item["fem_node_label"],
                item["component"],
                item["unit"],
                item.get("scatter", _DEFAULT_RESPONSE_SCATTER),
                item["seq_no"],
                item["source_table"],
                _json_dumps(item["extra_json"]),
            ))
        conn.commit()

        return {
            "project_id": project_id,
            "response_count": len(rows),
            "modal_frequency_count": sum(1 for row in rows if row["response_type"] == "MODAL_FREQUENCY"),
            "nodal_response_count": sum(1 for row in rows if row["response_type"] == "NODAL_DISPLACEMENT"),
            "responses_preview": rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_fe_response_catalog(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    write_cursor = None
    try:
        cursor.execute("""
            SELECT response_code, response_name, response_type, entity_type, test_mode_no, test_node_id,
                   instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, enabled,
                   selection_source, solver_scope, source_table, extra_json, created_at, updated_at
            FROM t_mt_py_fem_dynamic_response_catalog
            WHERE pid = %s
            ORDER BY seq_no, response_code
        """, (project_id,))
        responses = []
        for row in cursor.fetchall() or []:
            response_type = str(row.get("response_type") or "").upper()
            default_scope = _DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE if response_type.startswith("MODAL_") else ("DSA",)
            responses.append({
                **dict(row),
                "scatter": _safe_float(row.get("scatter")),
                "enabled": bool(row.get("enabled", 1)),
                "solver_scope": _parse_json_list(row.get("solver_scope"), default_values=default_scope),
                "extra_json": _parse_optional_json_object(row.get("extra_json")),
            })
        return {
            "project_id": project_id,
            "responses": responses,
        }
    finally:
        cursor.close()
        conn.close()


def list_optimization_parameters(project_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT parameter_group_name, parameter_name, quantity_code, selection_mode, set_name, set_type, set_scope,
                   instance_name, part_name, element_label, current_value, lower, upper, prob_id, scatter,
                   description, usage_scope, extra_json, created_at
            FROM t_mt_py_fem_selected_parameter
            WHERE pid = %s
            ORDER BY created_at DESC, parameter_name ASC
            """,
            (int(project_id),),
        )
        parameters = []
        for row in cursor.fetchall() or []:
            extra_json = _parse_optional_json_object(row.get("extra_json"))
            parameters.append({
                "parameter_group_name": str(row.get("parameter_group_name") or ""),
                "parameter_name": str(row.get("parameter_name") or ""),
                "quantity_code": str(row.get("quantity_code") or "").upper(),
                "selection_mode": str(row.get("selection_mode") or "").upper(),
                "set_name": str(row.get("set_name") or ""),
                "set_type": str(row.get("set_type") or ""),
                "set_scope": str(row.get("set_scope") or ""),
                "instance_name": row.get("instance_name"),
                "part_name": row.get("part_name"),
                "element_label": int(row["element_label"]) if row.get("element_label") is not None else None,
                "current_value": _safe_float(row.get("current_value")),
                "lower": _safe_float(row.get("lower")),
                "upper": _safe_float(row.get("upper")),
                "prob_id": int(row.get("prob_id") or 0),
                "scatter": _safe_float(row.get("scatter")),
                "description": str(row.get("description") or ""),
                "usage_scope": _parse_json_list(row.get("usage_scope"), default_values=_DEFAULT_PARAMETER_USAGE_SCOPE),
                "extra_json": extra_json,
                "created_at": row.get("created_at"),
            })
        return {
            "project_id": int(project_id),
            "parameter_count": len(parameters),
            "parameters": parameters,
        }
    finally:
        cursor.close()
        conn.close()


def update_optimization_parameter_usage(project_id: int, parameters: Sequence[dict]) -> dict:
    ensure_tables_exist()
    updates = []
    seen = set()
    for item in list(parameters or []):
        parameter_name = str((item or {}).get("parameter_name") or "").strip()
        if not parameter_name:
            raise ValidationError("parameter_name is required", {"item": item})
        if parameter_name in seen:
            raise ValidationError("duplicate parameter_name in request", {"parameter_name": parameter_name})
        seen.add(parameter_name)
        updates.append({
            "parameter_name": parameter_name,
            "usage_scope": _normalize_parameter_usage_scope((item or {}).get("usage_scope")),
        })
    if not updates:
        raise ValidationError("parameters cannot be empty", {"project_id": int(project_id)})

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        parameter_names = [item["parameter_name"] for item in updates]
        cursor.execute(
            f"""
            SELECT parameter_name
            FROM t_mt_py_fem_selected_parameter
            WHERE pid = %s AND parameter_name IN ({", ".join(["%s"] * len(parameter_names))})
            """,
            (int(project_id), *parameter_names),
        )
        existing_names = {str(row.get("parameter_name") or "") for row in (cursor.fetchall() or [])}
        missing = [name for name in parameter_names if name not in existing_names]
        if missing:
            raise ValidationError(
                "some parameters were not found",
                {"project_id": int(project_id), "missing_parameter_names": missing[:20]},
            )

        for item in updates:
            cursor.execute(
                """
                UPDATE t_mt_py_fem_selected_parameter
                SET usage_scope = %s
                WHERE pid = %s AND parameter_name = %s
                """,
                (_json_dumps(item["usage_scope"]), int(project_id), item["parameter_name"]),
            )
        conn.commit()
        return {
            "project_id": int(project_id),
            "updated_parameter_count": len(updates),
            "updated_parameters": updates,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def select_optimization_parameters_for_update(project_id: int, parameter_names: Sequence[str], *, replace_update_set: bool = False) -> dict:
    payload = list_optimization_parameters(int(project_id))
    rows = list(payload.get("parameters") or [])
    resolved_names = []
    seen = set()
    for item in list(parameter_names or []):
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        resolved_names.append(token)
    if not resolved_names:
        raise ValidationError("parameter_names cannot be empty", {"project_id": int(project_id)})

    row_by_name = {str(row.get("parameter_name") or ""): dict(row) for row in rows}
    missing = [name for name in resolved_names if name not in row_by_name]
    if missing:
        raise ValidationError("some parameters were not found", {"missing_parameter_names": missing[:20]})

    updates = []
    for name, row in row_by_name.items():
        current_scope = _normalize_parameter_usage_scope(row.get("usage_scope"))
        if name in resolved_names:
            new_scope = sorted(set(current_scope) | {"UPDATE"})
            if "SENSITIVITY" not in new_scope:
                new_scope.append("SENSITIVITY")
            updates.append({"parameter_name": name, "usage_scope": new_scope})
        elif replace_update_set and _scope_contains(current_scope, "UPDATE"):
            new_scope = [item for item in current_scope if item != "UPDATE"]
            if not new_scope:
                new_scope = ["SENSITIVITY"]
            updates.append({"parameter_name": name, "usage_scope": new_scope})
    result = update_optimization_parameter_usage(int(project_id), updates)
    result["replace_update_set"] = bool(replace_update_set)
    result["selected_parameter_names"] = resolved_names
    return result


def remove_optimization_parameters_from_update(project_id: int, parameter_names: Sequence[str]) -> dict:
    payload = list_optimization_parameters(int(project_id))
    rows = list(payload.get("parameters") or [])
    row_by_name = {str(row.get("parameter_name") or ""): dict(row) for row in rows}
    resolved_names = []
    seen = set()
    for item in list(parameter_names or []):
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        resolved_names.append(token)
    if not resolved_names:
        raise ValidationError("parameter_names cannot be empty", {"project_id": int(project_id)})
    missing = [name for name in resolved_names if name not in row_by_name]
    if missing:
        raise ValidationError("some parameters were not found", {"missing_parameter_names": missing[:20]})

    updates = []
    for name in resolved_names:
        current_scope = _normalize_parameter_usage_scope(row_by_name[name].get("usage_scope"))
        new_scope = [item for item in current_scope if item != "UPDATE"]
        if not new_scope:
            new_scope = ["SENSITIVITY"]
        updates.append({"parameter_name": name, "usage_scope": new_scope})
    result = update_optimization_parameter_usage(int(project_id), updates)
    result["removed_parameter_names"] = resolved_names
    return result


def _normalize_modal_mac_threshold_value(mac_threshold: Optional[float]) -> Optional[float]:
    if mac_threshold is None:
        return None
    resolved = float(mac_threshold)
    if resolved < 0:
        raise ValidationError("mac_threshold must be >= 0", {"mac_threshold": mac_threshold})
    if resolved <= 1.0:
        return resolved * 100.0
    if resolved <= 100.0:
        return resolved
    raise ValidationError("mac_threshold must be <= 100", {"mac_threshold": mac_threshold})


def _resolve_response_scatter_value(scatter: Optional[float]) -> float:
    resolved = float(_DEFAULT_RESPONSE_SCATTER if scatter is None else scatter)
    if resolved <= 0:
        raise ValidationError("scatter must be > 0", {"scatter": scatter})
    return resolved


def _delete_response_catalog_entries_by_types(cursor, project_id: int, response_types: Sequence[str]) -> None:
    resolved_types = [str(item).strip().upper() for item in list(response_types or []) if str(item).strip()]
    if not resolved_types:
        return
    cursor.execute(
        f"""
        DELETE FROM t_mt_py_fem_dynamic_response_catalog
        WHERE pid = %s AND response_type IN ({", ".join(["%s"] * len(resolved_types))})
        """,
        (int(project_id), *resolved_types),
    )


def _build_modal_response_catalog_row(
        row: dict,
        *,
        response_type: str,
        seq_no: int,
        matching_method: str,
        solver_scope: Sequence[str],
        selection_source: str,
        scatter: Optional[float] = None,
) -> dict:
    fem_mode_no = int(row["fem_mode_no"])
    test_mode_no = int(row["test_mode_no"])
    resolved_type = str(response_type or "").strip().upper()
    if resolved_type == "MODAL_FREQUENCY":
        response_code = f"MODE_FREQ:FE{fem_mode_no}:TEST{test_mode_no}"
        response_name = f"FREQ_MODE_{fem_mode_no}"
        component = "FREQ"
        unit = "Hz"
    elif resolved_type == "MODAL_MAC":
        response_code = f"MODE_MAC:FE{fem_mode_no}:TEST{test_mode_no}"
        response_name = f"MAC_MODE_FE{fem_mode_no}_TEST{test_mode_no}"
        component = "MAC"
        unit = None
    else:
        raise ValidationError(
            "unsupported modal response type",
            {"response_type": response_type, "allowed": sorted(_ALLOWED_MODAL_RESPONSE_TYPES)},
        )
    return {
        "response_code": response_code,
        "response_name": response_name,
        "response_type": resolved_type,
        "entity_type": "MODE",
        "test_mode_no": test_mode_no,
        "component": component,
        "unit": unit,
        "scatter": _resolve_response_scatter_value(scatter),
        "seq_no": int(seq_no),
        "enabled": True,
        "selection_source": str(selection_source or "manual_modal_match"),
        "solver_scope": list(solver_scope or []),
        "source_table": "t_mt_py_fem_modal_correlation",
        "extra_json": {
            "mode_number": fem_mode_no,
            "fem_mode_no": fem_mode_no,
            "test_mode_no": test_mode_no,
            "mac": _safe_float(row.get("mac")),
            "freq_test": _safe_float(row.get("freq_test")),
            "freq_fem": _safe_float(row.get("freq_fem")),
            "freq_error_ratio": _safe_float(row.get("freq_error_ratio")),
            "matching_method": str(matching_method or "greedy"),
            "target_value": 100.0 if resolved_type == "MODAL_MAC" else None,
        },
    }


def get_modal_frequency_response_options(project_id: int, response_source: str) -> dict:
    resolved_source = str(response_source or "").strip().upper()
    if resolved_source not in {"FEM", "TEST"}:
        raise ValidationError(
            "unsupported response_source",
            {"response_source": response_source, "allowed": ["FEM", "TEST"]},
        )

    if resolved_source == "FEM":
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT mode_no, frequency
                FROM t_mt_py_fem_modal_result
                WHERE pid = %s
                GROUP BY mode_no, frequency
                ORDER BY mode_no
            """, (int(project_id),))
            source_rows = [dict(row) for row in (cursor.fetchall() or [])]
        finally:
            cursor.close()
            conn.close()

        table_rows = [
            {
                "order": int(row["mode_no"]),
                "frequency_hz": _safe_float(row.get("frequency")),
            }
            for row in source_rows
        ]
        columns = [
            {"key": "order", "title": "阶次", "type": "number"},
            {"key": "frequency_hz", "title": "频率(Hz)", "type": "number"},
        ]
        return {
            "project_id": int(project_id),
            "response_source": resolved_source,
            "columns": columns,
            "rows": table_rows,
            "summary": {
                "row_count": len(table_rows),
                "column_count": len(columns),
            },
        }

    resolved_mac_threshold = _resolve_modal_frequency_options_mac_threshold(int(project_id))
    rows = _ensure_modal_correlation_rows_for_response(int(project_id))
    candidates = sorted(
        [
            dict(row) for row in rows
            if (
                resolved_mac_threshold is None
                or (
                    row.get("mac") is not None
                    and float(row["mac"]) >= float(resolved_mac_threshold)
                )
            )
        ],
        key=lambda item: (
            -(float(item["mac"]) if item.get("mac") is not None else -1.0),
            abs(float(item["freq_error_ratio"])) if item.get("freq_error_ratio") is not None else math.inf,
            int(item["fem_mode_no"]),
            int(item["test_mode_no"]),
        )
    )
    used_fem = set()
    used_test = set()
    matched_rows = []
    for item in candidates:
        fem_mode_no = int(item["fem_mode_no"])
        test_mode_no = int(item["test_mode_no"])
        if fem_mode_no in used_fem or test_mode_no in used_test:
            continue
        used_fem.add(fem_mode_no)
        used_test.add(test_mode_no)
        matched_rows.append({
            "test_frequency_hz": _safe_float(item.get("freq_test")),
            "test_order": test_mode_no,
            "fem_frequency_hz": _safe_float(item.get("freq_fem")),
            "fem_order": fem_mode_no,
        })
    matched_rows.sort(key=lambda item: item["test_order"])
    columns = [
        {"key": "test_frequency_hz", "title": "试验频率", "type": "number"},
        {"key": "test_order", "title": "试验阶次", "type": "number"},
        {"key": "fem_frequency_hz", "title": "计算频率", "type": "number"},
        {"key": "fem_order", "title": "计算阶次", "type": "number"},
    ]
    return {
        "project_id": int(project_id),
        "response_source": resolved_source,
        "mac_threshold": resolved_mac_threshold,
        "columns": columns,
        "rows": matched_rows,
        "summary": {
            "row_count": len(matched_rows),
            "column_count": len(columns),
        },
    }


def _insert_response_catalog_rows(cursor, project_id: int, rows: Sequence[dict]) -> None:
    insert_sql = """
    INSERT INTO t_mt_py_fem_dynamic_response_catalog
    (pid, response_code, response_name, response_type, entity_type, test_mode_no, test_node_id,
     instance_name, part_name, fem_node_label, component, unit, scatter, seq_no, enabled, selection_source,
     solver_scope, source_table, extra_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        response_name = VALUES(response_name),
        response_type = VALUES(response_type),
        entity_type = VALUES(entity_type),
        test_mode_no = VALUES(test_mode_no),
        component = VALUES(component),
        unit = VALUES(unit),
        scatter = VALUES(scatter),
        seq_no = VALUES(seq_no),
        enabled = VALUES(enabled),
        selection_source = VALUES(selection_source),
        solver_scope = VALUES(solver_scope),
        source_table = VALUES(source_table),
        extra_json = VALUES(extra_json),
        updated_at = CURRENT_TIMESTAMP
    """
    for payload in list(rows or []):
        cursor.execute(
            insert_sql,
            (
                int(project_id),
                payload["response_code"],
                payload["response_name"],
                payload["response_type"],
                payload["entity_type"],
                payload["test_mode_no"],
                None,
                None,
                None,
                None,
                payload["component"],
                payload["unit"],
                payload.get("scatter", _DEFAULT_RESPONSE_SCATTER),
                payload["seq_no"],
                1 if payload.get("enabled", True) else 0,
                payload.get("selection_source"),
                _json_dumps(list(payload.get("solver_scope") or [])),
                payload["source_table"],
                _json_dumps(payload["extra_json"]),
            ),
        )


def create_modal_frequency_response_catalog_from_fem(
        project_id: int,
        *,
        mode_numbers: Optional[Sequence[int]] = None,
        overwrite: bool = True,
        solver_scope: Optional[Sequence[str]] = None,
        scatter: Optional[float] = None,
        response_name_prefix: str = "FREQ_MODE_",
) -> dict:
    ensure_tables_exist()
    resolved_solver_scope = _normalize_response_solver_scope(
        solver_scope,
        default_values=_DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE,
    )
    resolved_scatter = _resolve_response_scatter_value(scatter)
    resolved_prefix = str(response_name_prefix or "FREQ_MODE_").strip() or "FREQ_MODE_"
    requested_mode_numbers = sorted({int(item) for item in list(mode_numbers or [])})

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT mode_no, frequency
            FROM t_mt_py_fem_modal_result
            WHERE pid = %s
            GROUP BY mode_no, frequency
            ORDER BY mode_no
            """,
            (int(project_id),),
        )
        source_rows = [dict(row) for row in (cursor.fetchall() or [])]
        if not source_rows:
            raise ValidationError(
                "no FEM modal results were found; import modal results before creating frequency responses",
                {"project_id": int(project_id)},
            )

        available_by_mode = {
            int(row["mode_no"]): {
                "mode_no": int(row["mode_no"]),
                "frequency": _safe_float(row.get("frequency")),
            }
            for row in source_rows
        }
        if requested_mode_numbers:
            missing_modes = [mode_no for mode_no in requested_mode_numbers if mode_no not in available_by_mode]
            if missing_modes:
                raise ValidationError(
                    "some requested mode_numbers were not found in FEM modal results",
                    {"project_id": int(project_id), "missing_mode_numbers": missing_modes[:20]},
                )
            selected_modes = requested_mode_numbers
        else:
            selected_modes = sorted(available_by_mode)

        if overwrite:
            _delete_response_catalog_entries_by_types(cursor, int(project_id), ["MODAL_FREQUENCY"])

        cursor.execute(
            """
            SELECT COALESCE(MAX(seq_no), 0) AS max_seq_no
            FROM t_mt_py_fem_dynamic_response_catalog
            WHERE pid = %s
            """,
            (int(project_id),),
        )
        next_seq_no = int((cursor.fetchone() or {}).get("max_seq_no") or 0) + 1

        rows = []
        for offset, mode_no in enumerate(selected_modes):
            modal_row = available_by_mode[int(mode_no)]
            rows.append({
                "response_code": f"MODE_FREQ:FEM:{int(mode_no)}",
                "response_name": f"{resolved_prefix}{int(mode_no)}",
                "response_type": "MODAL_FREQUENCY",
                "entity_type": "MODE",
                "test_mode_no": None,
                "component": "FREQ",
                "unit": "Hz",
                "scatter": resolved_scatter,
                "seq_no": next_seq_no + offset,
                "enabled": True,
                "selection_source": "manual_fem_modal",
                "solver_scope": list(resolved_solver_scope or []),
                "source_table": "t_mt_py_fem_modal_result",
                "extra_json": {
                    "mode_number": int(mode_no),
                    "fem_mode_no": int(mode_no),
                    "freq_fem": modal_row.get("frequency"),
                    "selection_source": "manual_fem_modal",
                },
            })

        _insert_response_catalog_rows(cursor, int(project_id), rows)
        conn.commit()
        return {
            "project_id": int(project_id),
            "overwrite": bool(overwrite),
            "response_count": len(rows),
            "mode_numbers": selected_modes,
            "solver_scope": resolved_solver_scope,
            "scatter": resolved_scatter,
            "responses_preview": rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_modal_frequency_response_catalog_from_match(
        project_id: int,
        *,
        overwrite: bool = True,
        mac_threshold: Optional[float] = None,
        max_freq_error_ratio: Optional[float] = 0.2,
        solver_scope: Optional[Sequence[str]] = None,
        matching_method: str = "greedy",
        scatter: Optional[float] = None,
) -> dict:
    ensure_tables_exist()
    resolved_mac_threshold = _normalize_modal_mac_threshold_value(mac_threshold)
    resolved_scatter = _resolve_response_scatter_value(scatter)
    resolved_solver_scope = _normalize_response_solver_scope(
        solver_scope,
        default_values=_DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE,
    )
    matched = _match_modal_modes_for_response(
        int(project_id),
        mac_threshold=0.0 if resolved_mac_threshold is None else float(resolved_mac_threshold),
        max_freq_error_ratio=max_freq_error_ratio,
        method=str(matching_method or "greedy"),
    )
    matched_rows = list(matched.get("rows") or [])
    if not matched_rows:
        raise ValidationError(
            "no modal matches passed the requested filters",
            {
                "project_id": int(project_id),
                "mac_threshold": resolved_mac_threshold,
                "max_freq_error_ratio": max_freq_error_ratio,
                "matching_method": matching_method,
            },
        )

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if overwrite:
            _delete_response_catalog_entries_by_types(cursor, int(project_id), ["MODAL_FREQUENCY"])
        rows = []
        for seq_no, row in enumerate(matched_rows, start=1):
            rows.append(
                _build_modal_response_catalog_row(
                    row,
                    response_type="MODAL_FREQUENCY",
                    seq_no=seq_no,
                    matching_method=str(matching_method or "greedy"),
                    solver_scope=resolved_solver_scope,
                    selection_source="auto_modal_match",
                    scatter=resolved_scatter,
                )
            )
        _insert_response_catalog_rows(cursor, int(project_id), rows)
        conn.commit()
        return {
            "project_id": int(project_id),
            "response_count": len(rows),
            "mac_threshold": resolved_mac_threshold,
            "max_freq_error_ratio": None if max_freq_error_ratio is None else float(max_freq_error_ratio),
            "matching_method": str(matching_method or "greedy"),
            "solver_scope": resolved_solver_scope,
            "scatter": resolved_scatter,
            "responses_preview": rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_modal_match_response_catalog_entries(
        project_id: int,
        *,
        selected_pairs: Sequence[dict],
        response_types: Sequence[str],
        solver_scope: Optional[Sequence[str]] = None,
        overwrite: bool = False,
        matching_method: str = "manual_select",
        scatter: Optional[float] = None,
) -> dict:
    ensure_tables_exist()
    resolved_scatter = _resolve_response_scatter_value(scatter)
    resolved_pairs = []
    seen_pairs = set()
    for item in list(selected_pairs or []):
        test_mode_no = int((item or {}).get("test_mode_no"))
        fem_mode_no = int((item or {}).get("fem_mode_no"))
        key = (test_mode_no, fem_mode_no)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        resolved_pairs.append({"test_mode_no": test_mode_no, "fem_mode_no": fem_mode_no})
    if not resolved_pairs:
        raise ValidationError("selected_pairs cannot be empty", {"project_id": int(project_id)})

    resolved_response_types = _normalize_modal_response_types(response_types)
    resolved_solver_scope = _normalize_response_solver_scope(
        solver_scope,
        default_values=_DEFAULT_MODAL_RESPONSE_SOLVER_SCOPE,
    )

    conn = get_connection()
    write_cursor = None
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT test_mode_no, fem_mode_no, mac, freq_test, freq_fem, freq_error_ratio
            FROM t_mt_py_fem_modal_correlation
            WHERE pid = %s
              AND (
                {" OR ".join(["(test_mode_no = %s AND fem_mode_no = %s)"] * len(resolved_pairs))}
              )
            ORDER BY fem_mode_no, test_mode_no
            """,
            (int(project_id), *[value for pair in resolved_pairs for value in (pair["test_mode_no"], pair["fem_mode_no"])]),
        )
        matched_rows = [dict(row) for row in (cursor.fetchall() or [])]
        row_by_key = {(int(row["test_mode_no"]), int(row["fem_mode_no"])): dict(row) for row in matched_rows}
        missing = [
            {"test_mode_no": pair["test_mode_no"], "fem_mode_no": pair["fem_mode_no"]}
            for pair in resolved_pairs
            if (pair["test_mode_no"], pair["fem_mode_no"]) not in row_by_key
        ]
        if missing:
            raise ValidationError(
                "some selected modal pairs were not found in modal correlation results",
                {"missing_pairs": missing[:20], "project_id": int(project_id)},
            )

        ordered_rows = []
        seq_no = 1
        for pair in resolved_pairs:
            source_row = row_by_key[(pair["test_mode_no"], pair["fem_mode_no"])]
            for response_type in resolved_response_types:
                ordered_rows.append(
                    _build_modal_response_catalog_row(
                        source_row,
                        response_type=response_type,
                        seq_no=seq_no,
                        matching_method=str(matching_method or "manual_select"),
                        solver_scope=resolved_solver_scope,
                        selection_source="manual_modal_match",
                        scatter=resolved_scatter,
                    )
                )
                seq_no += 1

        write_cursor = conn.cursor()
        if overwrite:
            _delete_response_catalog_entries_by_types(write_cursor, int(project_id), resolved_response_types)
        _insert_response_catalog_rows(write_cursor, int(project_id), ordered_rows)
        conn.commit()
        return {
            "project_id": int(project_id),
            "overwrite": bool(overwrite),
            "response_types": resolved_response_types,
            "solver_scope": resolved_solver_scope,
            "scatter": resolved_scatter,
            "selected_pair_count": len(resolved_pairs),
            "response_count": len(ordered_rows),
            "responses_preview": ordered_rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        if write_cursor is not None:
            write_cursor.close()
        cursor.close()
        conn.close()
