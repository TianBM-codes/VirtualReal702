"""FEM static/modal correlation helpers."""

from .fem_result_service import *
from .fem_catalog_service import (
    _json_dumps,
    _json_loads,
    _required_operation_error,
    _require_modal_project,
    _require_non_modal_project,
    _safe_float,
)
from .fem_result_service import _STATIC_TEST_DATA_DISPLACEMENT_TYPES

def _normalize_static_test_sensor_type(value) -> str:
    text = str(value or "").strip().lower()
    if text.isdigit():
        return text
    return text.replace(" ", "_")


def _is_displacement_static_test_sensor_type(value) -> bool:
    return _normalize_static_test_sensor_type(value) in _STATIC_TEST_DATA_DISPLACEMENT_TYPES


def _coerce_static_test_float(value):
    if value is None or value == "":
        return None
    return float(value)


def _is_static_test_sensor_entry(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(
        key in payload
        for key in (
            "sensor_label",
            "measuring_point_name",
            "point_no",
            "point",
            "label",
            "name",
            "sensorName",
        )
    )


def _extract_static_test_entry_scalar(value) -> Optional[float]:
    if isinstance(value, dict):
        for key in ("value", "uy", "y", "displacement", "reading", "measurement"):
            if key in value and value.get(key) not in (None, ""):
                return _coerce_static_test_float(value.get(key))
        return None
    return _coerce_static_test_float(value)


def _extract_static_test_entry_components(value) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    if isinstance(value, dict):
        ux = _coerce_static_test_float(value.get("ux", value.get("x")))
        uy = _coerce_static_test_float(value.get("uy", value.get("y")))
        uz = _coerce_static_test_float(value.get("uz", value.get("z")))
        rx = _coerce_static_test_float(value.get("rx", value.get("ur1")))
        ry = _coerce_static_test_float(value.get("ry", value.get("ur2")))
        rz = _coerce_static_test_float(value.get("rz", value.get("ur3")))
        if ux is None and uy is None and uz is None:
            scalar = _extract_static_test_entry_scalar(value)
            if scalar is not None:
                return 0.0, float(scalar), 0.0, rx, ry, rz
        return (
            0.0 if ux is None else float(ux),
            0.0 if uy is None else float(uy),
            0.0 if uz is None else float(uz),
            rx,
            ry,
            rz,
        )

    scalar = _extract_static_test_entry_scalar(value)
    if scalar is None:
        return None, None, None, None, None, None
    return 0.0, float(scalar), 0.0, None, None, None


def _iter_static_test_payload_entries(payload) -> List[dict]:
    if payload is None:
        return []
    if isinstance(payload, str):
        payload = _json_loads(payload)
    if payload is None:
        return []

    if _is_static_test_sensor_entry(payload):
        return [dict(payload)]

    if isinstance(payload, dict):
        for key in ("data", "rows", "items", "results", "sensors", "values", "records"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [dict(item) if isinstance(item, dict) else {"value": item} for item in nested]
            if isinstance(nested, dict):
                payload = nested
                break

    if isinstance(payload, list):
        entries = []
        for item in payload:
            if isinstance(item, dict):
                normalized = dict(item)
                if not _is_static_test_sensor_entry(normalized):
                    candidates = [
                        (str(key), value)
                        for key, value in normalized.items()
                        if str(key or "").strip() not in {
                            "project_id",
                            "sensor_type",
                            "timestamp",
                            "time",
                            "created_at",
                            "updated_at",
                        }
                    ]
                    if len(candidates) == 1:
                        sensor_label, reading = candidates[0]
                        normalized = {"sensor_label": sensor_label, "value": reading}
                entries.append(normalized)
        return entries

    if isinstance(payload, dict):
        entries = []
        for sensor_label, reading in payload.items():
            if str(sensor_label or "").strip() in {"project_id", "sensor_type", "timestamp", "time", "created_at", "updated_at"}:
                continue
            entries.append({"sensor_label": str(sensor_label), "value": reading})
        return entries

    return []


def _load_latest_static_test_data_row(cursor, project_id: int):
    statements = [
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        ORDER BY id DESC
        """,
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        ORDER BY created_at DESC
        """,
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        ORDER BY create_time DESC
        """,
        """
        SELECT sensor_type, data
        FROM t_mt_static_test_data
        WHERE project_id = %s
        """,
    ]
    rows = None
    for sql in statements:
        try:
            cursor.execute(sql, (int(project_id),))
            rows = cursor.fetchall()
        except Exception:
            rows = None
        if rows is not None:
            break
    if not rows:
        return None
    for row in rows:
        if _is_displacement_static_test_sensor_type(row.get("sensor_type")):
            return row
    return dict(rows[0])


def _load_static_test_rows_from_data_table(cursor, project_id: int) -> List[dict]:
    row = _load_latest_static_test_data_row(cursor, project_id)
    if not row:
        return []

    try:
        payload = _json_loads(row.get("data"))
    except Exception as exc:
        raise ValueError(f"failed to parse t_mt_static_test_data.data JSON: {exc}") from exc

    entries = _iter_static_test_payload_entries(payload)
    if not entries:
        raise ValueError("t_mt_static_test_data.data does not contain any readable sensor entries")

    measuring_rows = []
    try:
        cursor.execute(
            """
            SELECT measuring_point_name, sensor_type_id
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id, measuring_point_name
            """,
            (int(project_id),),
        )
        measuring_rows = cursor.fetchall() or []
    except Exception:
        measuring_rows = []
    known_labels = {
        str(item.get("measuring_point_name")): item
        for item in measuring_rows
        if item.get("measuring_point_name") is not None
    }

    rows = []
    for entry in entries:
        sensor_label = None
        for key in ("sensor_label", "measuringPointName", "point_no", "point", "label", "name", "sensorName"):
            value = entry.get(key)
            if value not in (None, ""):
                sensor_label = str(value)
                break
        if not sensor_label:
            continue
        if known_labels and sensor_label not in known_labels:
            continue

        raw_value = entry.get("data", entry.get("value", entry))
        ux, uy, uz, rx, ry, rz = _extract_static_test_entry_components(raw_value)
        if ux is None and uy is None and uz is None:
            continue
        rows.append(
            {
                "point": str(sensor_label),
                "ux": ux,
                "uy": uy,
                "uz": uz,
                "rx": rx,
                "ry": ry,
                "rz": rz,
                "load_factor": None,
                "extra_json": {
                    "source": "t_mt_static_test_data",
                    "sensor_type": row.get("sensor_type"),
                },
            }
        )

    if not rows:
        raise ValueError("no displacement sensor rows from t_mt_static_test_data matched the measuring points")
    return rows


def _load_test_static_rows(cursor, project_id: int, load_case_no: int, result_no: int) -> List[dict]:
    cursor.execute("""
        SELECT point, ux, uy, uz, rx, ry, rz, load_factor, extra_json
        FROM t_mt_py_test_static_result
        WHERE pid = %s AND load_case_no = %s AND result_no = %s
        ORDER BY point
    """, (int(project_id), int(load_case_no), int(result_no)))
    rows = cursor.fetchall() or []
    if rows:
        return rows
    return _load_static_test_rows_from_data_table(cursor, int(project_id))


def _resolve_static_case_selection_for_correlation(cursor, project_id: int, load_case_no=None, result_no=None):
    cursor.execute("""
        SELECT DISTINCT load_case_no
        FROM t_mt_py_fem_static_result
        WHERE pid = %s
        ORDER BY load_case_no
    """, (project_id,))
    fem_cases = [int(row["load_case_no"]) for row in cursor.fetchall()]
    if not fem_cases:
        raise ValueError("未找到 FEM 静态结果")

    if not _load_latest_static_test_data_row(cursor, project_id):
        raise ValueError("未找到 t_mt_static_test_data 中的试验静态数据")

    if load_case_no is None:
        chosen_load_case_no = int(fem_cases[0])
    else:
        chosen_load_case_no = int(load_case_no)
        if chosen_load_case_no not in fem_cases:
            raise ValueError(f"fem static load_case_no not found: {chosen_load_case_no}")

    if result_no is None:
        chosen_result_no = 1
    else:
        chosen_result_no = int(result_no)
        if chosen_result_no != 1:
            raise ValueError("t_mt_static_test_data only supports result_no=1")

    return chosen_load_case_no, chosen_result_no


def _resolve_static_components(components=None, include_rotations=False):
    if components:
        names = [str(comp).upper() for comp in components]
    else:
        names = ["UX", "UY", "UZ"]
        if include_rotations:
            names.extend(["RX", "RY", "RZ"])

    invalid = [name for name in names if name not in STATIC_COMPONENT_MAP]
    if invalid:
        raise ValueError(f"unsupported static components: {invalid}")
    return names


def _resolve_static_case_selection(cursor, project_id: int, load_case_no=None, result_no=None):
    cursor.execute("""
        SELECT DISTINCT load_case_no, result_no
        FROM t_mt_py_test_static_result
        WHERE pid = %s
        ORDER BY load_case_no, result_no
    """, (project_id,))
    test_pairs = cursor.fetchall()

    cursor.execute("""
        SELECT DISTINCT load_case_no
        FROM t_mt_py_fem_static_result
        WHERE pid = %s
        ORDER BY load_case_no
    """, (project_id,))
    fem_cases = [int(row["load_case_no"]) for row in cursor.fetchall()]
    if not fem_cases:
        raise ValueError("未找到 FEM 静态结果")

    if not test_pairs:
        if not _load_latest_static_test_data_row(cursor, project_id):
            raise ValueError("未找到试验静态结果")
        if load_case_no is None:
            chosen_load_case_no = int(fem_cases[0])
        else:
            chosen_load_case_no = int(load_case_no)
            if chosen_load_case_no not in fem_cases:
                raise ValueError(f"fem static load_case_no not found: {chosen_load_case_no}")

        if result_no is None:
            chosen_result_no = 1
        else:
            chosen_result_no = int(result_no)
            if chosen_result_no != 1:
                raise ValueError("t_mt_static_test_data only supports result_no=1")
        return chosen_load_case_no, chosen_result_no

    test_case_to_results = {}
    for row in test_pairs:
        test_case_to_results.setdefault(int(row["load_case_no"]), []).append(int(row["result_no"]))

    common_cases = sorted(set(test_case_to_results.keys()) & set(fem_cases))
    if load_case_no is None:
        if not common_cases:
            raise ValueError("no common static load_case_no between test and fem static tables")
        chosen_load_case_no = int(common_cases[0])
    else:
        chosen_load_case_no = int(load_case_no)
        if chosen_load_case_no not in test_case_to_results:
            raise ValueError(f"test static load_case_no not found: {chosen_load_case_no}")
        if chosen_load_case_no not in fem_cases:
            raise ValueError(f"fem static load_case_no not found: {chosen_load_case_no}")

    result_candidates = sorted(test_case_to_results[chosen_load_case_no])
    if result_no is None:
        chosen_result_no = int(result_candidates[0])
    else:
        chosen_result_no = int(result_no)
        if chosen_result_no not in result_candidates:
            raise ValueError(
                f"test static result_no not found for load_case_no={chosen_load_case_no}: {chosen_result_no}"
            )

    return chosen_load_case_no, chosen_result_no


def _build_static_alignment(test_rows, fem_rows, node_matches):
    # Prefer the persisted node-match table. If it does not exist yet, fall back
    # to direct label matching only when labels are unambiguous.
    fem_by_key = {}
    fem_by_label = {}
    duplicate_labels = set()
    for row in fem_rows:
        key = (str(row["instance_name"] or ""), int(row["fem_node_label"]))
        fem_by_key[key] = row
        label = int(row["fem_node_label"])
        if label in fem_by_label:
            duplicate_labels.add(label)
        fem_by_label[label] = row

    aligned = []
    if node_matches:
        for match in node_matches:
            test_row = test_rows.get(str(match["test_node_id"]))
            if test_row is None:
                continue
            fem_row = fem_by_key.get((str(match["instance_name"] or ""), int(match["fem_node_label"])))
            if fem_row is None:
                continue
            aligned.append((test_row, fem_row, match))
        return aligned

    for point_id, test_row in test_rows.items():
        try:
            label = int(point_id)
        except (TypeError, ValueError):
            continue
        if label in duplicate_labels:
            continue
        fem_row = fem_by_label.get(label)
        if fem_row is None:
            continue
        aligned.append((
            test_row,
            fem_row,
            {
                "test_node_id": point_id,
                "instance_name": fem_row["instance_name"],
                "fem_node_label": fem_row["fem_node_label"],
                "match_mode": "label_fallback",
            },
        ))
    return aligned


def _relative_error_percent(node_value: float, point_value: float) -> float:
    point = float(point_value)
    node = float(node_value)
    if abs(point) <= 1e-12:
        return 0.0
    return float((node - point) / point * 100.0)


def _analysis_error_node_no(fem_row: dict) -> str:
    instance_name = str(fem_row.get("instance_name") or "").strip()
    fem_node_label = int(fem_row["fem_node_label"])
    if instance_name:
        return f"{instance_name}::{fem_node_label}"
    return str(fem_node_label)


def _should_store_static_analysis_error_row(*, sensor_type_id, component_name: str, point_value: float) -> bool:
    resolved_component = str(component_name or "").upper()
    if _is_displacement_static_test_sensor_type(sensor_type_id):
        if resolved_component in {"UX", "UZ"} and abs(float(point_value)) <= 1e-12:
            return False
    return True


def _load_measuring_point_sensor_type_map(cursor, project_id: int) -> Dict[str, Optional[int]]:
    try:
        cursor.execute(
            """
            SELECT measuring_point_name, sensor_type_id
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id, measuring_point_name
            """,
            (int(project_id),),
        )
        rows = cursor.fetchall() or []
    except Exception:
        return {}

    result: Dict[str, Optional[int]] = {}
    for row in rows:
        measuring_point_name = row.get("measuring_point_name")
        if measuring_point_name in (None, ""):
            continue
        sensor_type_id = row.get("sensor_type_id")
        result[str(measuring_point_name)] = None if sensor_type_id is None else int(sensor_type_id)
    return result


def _build_static_analysis_error_rows(
        *,
        aligned_rows,
        component_names,
        load_case_no: int,
        result_no: int,
        value_prefix: str,
        sensor_type_map: Optional[Dict[str, Optional[int]]] = None,
) -> List[dict]:
    rows: List[dict] = []
    for test_row, fem_row, _match in aligned_rows:
        point_no = str(test_row["point"])
        node_no = _analysis_error_node_no(fem_row)
        sensor_type_id = None
        if sensor_type_map:
            sensor_type_id = sensor_type_map.get(point_no)
        for component_name in component_names:
            test_col, fem_col = STATIC_COMPONENT_MAP[component_name]
            point_value = test_row.get(test_col)
            node_value = fem_row.get(fem_col)
            if point_value is None or node_value is None:
                continue
            node_value = float(node_value)
            point_value = float(point_value)
            if not _should_store_static_analysis_error_row(
                    sensor_type_id=sensor_type_id,
                    component_name=str(component_name),
                    point_value=point_value,
            ):
                continue
            rows.append(
                {
                    "load_case_no": int(load_case_no),
                    "result_no": int(result_no),
                    "point_no": point_no,
                    "node_no": node_no,
                    "component_name": str(component_name),
                    "point_value": point_value,
                    f"{value_prefix}_node_value": node_value,
                    f"{value_prefix}_relative_error": _relative_error_percent(node_value, point_value),
                    f"{value_prefix}_abs_error": float(abs(node_value - point_value)),
                    "sensor_type_id": sensor_type_id,
                }
            )
    return rows


def _upsert_analysis_error_rows(cursor, project_id: int, rows: Sequence[dict], *, value_prefix: str) -> None:
    if value_prefix not in {"initial", "updated"}:
        raise ValueError("value_prefix must be initial or updated")
    point_value_update_sql = "point_value = VALUES(point_value)," if value_prefix == "initial" else ""
    insert_sql = f"""
        INSERT INTO t_mt_py_fem_analysis_error
        (pid, load_case_no, result_no, point_no, node_no, component_name, point_value,
         {value_prefix}_node_value, {value_prefix}_relative_error, {value_prefix}_abs_error, sensor_type_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            node_no = VALUES(node_no),
            {point_value_update_sql}
            {value_prefix}_node_value = VALUES({value_prefix}_node_value),
            {value_prefix}_relative_error = VALUES({value_prefix}_relative_error),
            {value_prefix}_abs_error = VALUES({value_prefix}_abs_error),
            sensor_type_id = VALUES(sensor_type_id)
    """
    for row in rows:
        cursor.execute(
            insert_sql,
            (
                int(project_id),
                int(row["load_case_no"]),
                int(row["result_no"]),
                str(row["point_no"]),
                str(row["node_no"]),
                str(row["component_name"]),
                row.get("point_value"),
                row.get(f"{value_prefix}_node_value"),
                row.get(f"{value_prefix}_relative_error"),
                row.get(f"{value_prefix}_abs_error"),
                row.get("sensor_type_id"),
            ),
        )


def store_updated_static_analysis_error(
        *,
        project_id: int,
        fem_rows: Sequence[dict],
        load_case_no=None,
        result_no=None,
        components=None,
        include_rotations: bool = False,
):
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT DISTINCT load_case_no, result_no
            FROM t_mt_py_test_static_result
            WHERE pid = %s
            ORDER BY load_case_no, result_no
        """, (project_id,))
        test_pairs = cursor.fetchall()
        if not test_pairs:
            raise ValueError("未找到试验静态结果")

        test_case_to_results = {}
        for row in test_pairs:
            test_case_to_results.setdefault(int(row["load_case_no"]), []).append(int(row["result_no"]))

        if load_case_no is None:
            chosen_load_case_no = int(sorted(test_case_to_results.keys())[0])
        else:
            chosen_load_case_no = int(load_case_no)
            if chosen_load_case_no not in test_case_to_results:
                raise ValueError(f"test static load_case_no not found: {chosen_load_case_no}")

        result_candidates = sorted(test_case_to_results[chosen_load_case_no])
        if result_no is None:
            chosen_result_no = int(result_candidates[0])
        else:
            chosen_result_no = int(result_no)
            if chosen_result_no not in result_candidates:
                raise ValueError(
                    f"test static result_no not found for load_case_no={chosen_load_case_no}: {chosen_result_no}"
                )

        component_names = _resolve_static_components(
            components=components,
            include_rotations=include_rotations,
        )
        test_rows_raw = _load_static_test_rows_from_data_table(cursor, int(project_id))
        if not test_rows_raw:
            raise ValueError("未找到 t_mt_static_test_data 中的试验静态数据记录")
        test_rows = {str(row["point"]): row for row in test_rows_raw}

        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()

        aligned_rows = _build_static_alignment(test_rows, list(fem_rows or []), node_matches)
        if not aligned_rows:
            raise ValueError("试验静态结果与修正后 FEM 静态结果之间未找到可对齐的数据行")

        sensor_type_map = _load_measuring_point_sensor_type_map(cursor, int(project_id))
        error_rows = _build_static_analysis_error_rows(
            aligned_rows=aligned_rows,
            component_names=component_names,
            load_case_no=chosen_load_case_no,
            result_no=chosen_result_no,
            value_prefix="updated",
            sensor_type_map=sensor_type_map,
        )
        _upsert_analysis_error_rows(cursor, project_id, error_rows, value_prefix="updated")
        conn.commit()
        return {
            "project_id": int(project_id),
            "load_case_no": int(chosen_load_case_no),
            "result_no": int(chosen_result_no),
            "components": component_names,
            "analysis_error_row_count": len(error_rows),
            "analysis_error_preview": error_rows[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def compute_static_correlation(
        project_id,
        load_case_no=None,
        result_no=None,
        components=None,
        include_rotations=False,
):
    # Static correlation compares one chosen test static result against one FE
    # load case after resolving node alignment and the requested components.
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _require_non_modal_project(int(project_id), cursor=cursor)
        chosen_load_case_no, chosen_result_no = _resolve_static_case_selection_for_correlation(
            cursor,
            project_id,
            load_case_no=load_case_no,
            result_no=result_no,
        )
        component_names = _resolve_static_components(
            components=components,
            include_rotations=include_rotations,
        )

        test_rows_raw = _load_static_test_rows_from_data_table(cursor, int(project_id))
        if not test_rows_raw:
            raise ValueError("未找到 t_mt_static_test_data 中的试验静态数据记录")
        test_rows = {str(row["point"]): row for row in test_rows_raw}

        cursor.execute("""
            SELECT load_case_no, instance_name, part_name, fem_node_label,
                   u1, u2, u3, ur1, ur2, ur3, extra_json
            FROM t_mt_py_fem_static_result
            WHERE pid = %s AND load_case_no = %s
            ORDER BY instance_name, fem_node_label
        """, (project_id, chosen_load_case_no))
        fem_rows = cursor.fetchall()
        if not fem_rows:
            raise ValueError("未找到所选 FEM 静态结果记录")

        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()

        aligned_rows = _build_static_alignment(test_rows, fem_rows, node_matches)
        if not aligned_rows:
            raise ValueError("试验静态结果与 FEM 静态结果之间未找到可对齐的数据行")

        sensor_type_map = _load_measuring_point_sensor_type_map(cursor, int(project_id))
        test_values = []
        fem_values = []
        anchors = []
        component_counts = {name: 0 for name in component_names}
        analysis_error_rows = []

        for test_row, fem_row, match in aligned_rows:
            # Each aligned point can contribute multiple scalar channels
            # depending on the requested component list.
            for comp_name in component_names:
                test_col, fem_col = STATIC_COMPONENT_MAP[comp_name]
                test_val = test_row.get(test_col)
                fem_val = fem_row.get(fem_col)
                if test_val is None or fem_val is None:
                    continue
                test_values.append(complex(float(test_val), 0.0))
                fem_values.append(complex(float(fem_val), 0.0))
                component_counts[comp_name] += 1
                sensor_type_id = sensor_type_map.get(str(test_row["point"]))
                if _should_store_static_analysis_error_row(
                        sensor_type_id=sensor_type_id,
                        component_name=str(comp_name),
                        point_value=float(test_val),
                ):
                    analysis_error_rows.append(
                        {
                            "load_case_no": int(chosen_load_case_no),
                            "result_no": int(chosen_result_no),
                            "point_no": str(test_row["point"]),
                            "node_no": _analysis_error_node_no(fem_row),
                            "component_name": comp_name,
                            "point_value": float(test_val),
                            "initial_node_value": float(fem_val),
                            "initial_relative_error": _relative_error_percent(float(fem_val), float(test_val)),
                            "initial_abs_error": float(abs(float(fem_val) - float(test_val))),
                            "sensor_type_id": sensor_type_id,
                        }
                    )
                if len(anchors) < 50:
                    anchors.append({
                        "test_point": str(test_row["point"]),
                        "instance_name": fem_row["instance_name"],
                        "fem_node_label": int(fem_row["fem_node_label"]),
                        "component": comp_name,
                        "test_value": float(test_val),
                        "fem_value": float(fem_val),
                        "match_mode": match.get("match_mode", "node_match"),
                    })

        if len(test_values) < 2:
            raise ValueError("可对齐的静态值数量不足，无法计算 dac/dsf")

        metrics = _compute_dac_dsf(
            np.asarray(test_values, dtype=np.complex128),
            np.asarray(fem_values, dtype=np.complex128),
        )

        return {
            "project_id": project_id,
            "load_case_no": int(chosen_load_case_no),
            "result_no": int(chosen_result_no),
            "components": component_names,
            "aligned_point_count": len(aligned_rows),
            "value_count": len(test_values),
            "component_value_counts": component_counts,
            "analysis_error_row_count": len(analysis_error_rows),
            "analysis_error_preview": analysis_error_rows[:20],
            "dac": metrics["dac"],
            "dsf": metrics["dsf"],
            "_analysis_error_rows": analysis_error_rows,
            "extra": {
                "scale_real": metrics["scale_real"],
                "scale_imag": metrics["scale_imag"],
                "scale_phase_deg": metrics["scale_phase_deg"],
                "test_norm": metrics["test_norm"],
                "fem_norm": metrics["fem_norm"],
                "residual_norm": metrics["residual_norm"],
                "anchors_preview": anchors,
            },
        }
    finally:
        cursor.close()
        conn.close()


def _ensure_node_matches(project_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_node_id
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            LIMIT 1
        """, (int(project_id),))
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if row:
        return False

    match_test_nodes(int(project_id), overwrite=False)
    return True


def _ensure_static_node_matches(project_id: int) -> bool:
    return _ensure_node_matches(project_id)


def evaluate_static_correlation(
        project_id,
        load_case_no=None,
        result_no=None,
        components=None,
        include_rotations=False,
):
    _ensure_static_node_matches(int(project_id))
    result = compute_static_correlation(
        project_id=project_id,
        load_case_no=load_case_no,
        result_no=result_no,
        components=components,
        include_rotations=include_rotations,
    )

    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        analysis_error_rows = list(result.get("_analysis_error_rows") or [])
        cursor.execute("""
            INSERT INTO t_mt_py_fem_dac_dsf (pid, dac, dsf)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dac = VALUES(dac),
                dsf = VALUES(dsf)
        """, (
            int(project_id),
            float(result["dac"]),
            float(result["dsf"]),
        ))
        _upsert_analysis_error_rows(cursor, project_id, analysis_error_rows, value_prefix="initial")
        update_work_condition_project_status(
            int(project_id),
            cursor=cursor,
            consistency_status=1,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    result.pop("_analysis_error_rows", None)
    return result


def _load_test_mode_vectors(cursor, project_id: int) -> Dict[int, Dict[str, np.ndarray]]:
    # Test modal shapes are stored in multiple historical schemas. This loader
    # normalizes them into complex 3-component vectors keyed by test point id.
    modes: Dict[int, Dict[str, np.ndarray]] = {}

    cursor.execute("""
        SELECT mode_no, point, ux, uy, uz
        FROM t_mt_py_test_modal_shape_real
        WHERE pid = %s
        ORDER BY mode_no, point
    """, (project_id,))
    for row in cursor.fetchall():
        modes.setdefault(int(row["mode_no"]), {})[str(row["point"])] = np.array([
            complex(float(row["ux"] or 0.0), 0.0),
            complex(float(row["uy"] or 0.0), 0.0),
            complex(float(row["uz"] or 0.0), 0.0),
        ], dtype=np.complex128)

    cursor.execute("""
        SELECT mode_no, point, re_ux, re_uy, re_uz, im_ux, im_uy, im_uz
        FROM t_mt_py_test_modal_shape_imag
        WHERE pid = %s
        ORDER BY mode_no, point
    """, (project_id,))
    for row in cursor.fetchall():
        modes.setdefault(int(row["mode_no"]), {})[str(row["point"])] = np.array([
            complex(float(row["re_ux"] or 0.0), float(row["im_ux"] or 0.0)),
            complex(float(row["re_uy"] or 0.0), float(row["im_uy"] or 0.0)),
            complex(float(row["re_uz"] or 0.0), float(row["im_uz"] or 0.0)),
        ], dtype=np.complex128)

    if modes:
        return modes

    cursor.execute("""
        SELECT mode_no, modal_shape
        FROM t_mt_py_test_modal_shape
        WHERE pid = %s
        ORDER BY mode_no
    """, (project_id,))
    for row in cursor.fetchall():
        shape_json = _json_loads(row["modal_shape"]) or {}
        for point_id, comp_map in shape_json.items():
            real = comp_map.get("real", [0.0, 0.0, 0.0])
            imag = comp_map.get("imag", [0.0, 0.0, 0.0])
            modes.setdefault(int(row["mode_no"]), {})[str(point_id)] = np.array([
                complex(float(real[0]), float(imag[0])),
                complex(float(real[1]), float(imag[1])),
                complex(float(real[2]), float(imag[2])),
            ], dtype=np.complex128)
    return modes


def _load_fem_mode_vectors(cursor, project_id: int):
    cursor.execute("""
        SELECT mode_no, frequency, instance_name, fem_node_label, u1, u2, u3
        FROM t_mt_py_fem_modal_result
        WHERE pid = %s
        ORDER BY mode_no, instance_name, fem_node_label
    """, (project_id,))
    modes: Dict[int, Dict[Tuple[str, int], np.ndarray]] = {}
    freqs = {}
    for row in cursor.fetchall():
        mode_no = int(row["mode_no"])
        inst_name = str(row["instance_name"] or "")
        freqs.setdefault(mode_no, _safe_float(row["frequency"]))
        modes.setdefault(mode_no, {})[(inst_name, int(row["fem_node_label"]))] = np.array([
            float(row["u1"] or 0.0),
            float(row["u2"] or 0.0),
            float(row["u3"] or 0.0),
        ], dtype=np.float64)
    return modes, freqs


def _load_test_modal_frequencies(cursor, project_id: int) -> Dict[int, float]:
    cursor.execute("""
        SELECT mode_no, frequency
        FROM t_mt_py_test_modal_frequency
        WHERE pid = %s
        ORDER BY mode_no
    """, (project_id,))
    return {int(row["mode_no"]): _safe_float(row["frequency"]) for row in cursor.fetchall()}


def _get_project_test_modal_data_type(cursor, project_id: int) -> Optional[str]:
    cursor.execute("""
        SELECT test_modal_data_type
        FROM t_mt_work_condition_project
        WHERE project_id = %s
        LIMIT 1
    """, (int(project_id),))
    row = cursor.fetchone() or {}
    value = row.get("test_modal_data_type")
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _has_nonzero_imaginary_test_modes(test_modes: Dict[int, Dict[str, np.ndarray]]) -> bool:
    for mode_map in test_modes.values():
        for vec in mode_map.values():
            arr = np.asarray(vec, dtype=np.complex128)
            if np.any(np.abs(arr.imag) > 1e-12):
                return True
    return False


def _resolve_modal_mac_mode(cursor, project_id: int, test_modes: Dict[int, Dict[str, np.ndarray]]) -> str:
    test_modal_data_type = _get_project_test_modal_data_type(cursor, int(project_id))
    if test_modal_data_type == "IMAG":
        return "complex"
    if test_modal_data_type == "REAL":
        return "real"
    return "complex" if _has_nonzero_imaginary_test_modes(test_modes) else "real"


def _compute_dac_dsf(test_vec: np.ndarray, fem_vec: np.ndarray, *, mac_mode: str = "real") -> dict:
    resolved_mac_mode = str(mac_mode or "real").strip().lower()
    if resolved_mac_mode not in {"real", "complex"}:
        raise ValidationError(
            "unsupported modal mac mode",
            {"mac_mode": mac_mode, "allowed": ["real", "complex"]},
        )

    test_energy = float(np.vdot(test_vec, test_vec).real)
    fem_energy = float(np.vdot(fem_vec, fem_vec).real)
    if test_energy <= 1e-18 or fem_energy <= 1e-18:
        raise ValueError("向量能量为 0")

    cross = np.vdot(test_vec, fem_vec)
    cross_t = np.dot(test_vec, fem_vec)
    self_test_h = np.vdot(test_vec, test_vec)
    self_test_t = np.dot(test_vec, test_vec)
    self_fem_h = np.vdot(fem_vec, fem_vec)
    self_fem_t = np.dot(fem_vec, fem_vec)
    scale = np.vdot(fem_vec, test_vec) / np.vdot(test_vec, test_vec)
    msf_scale = np.vdot(fem_vec, test_vec) / np.vdot(fem_vec, fem_vec)
    residual = test_vec - scale * fem_vec
    if resolved_mac_mode == "complex":
        mac_numerator = (abs(cross) + abs(cross_t)) ** 2
        mac_denominator = (abs(self_test_h) + abs(self_test_t)) * (abs(self_fem_h) + abs(self_fem_t))
    else:
        mac_numerator = abs(cross) ** 2
        mac_denominator = test_energy * fem_energy

    mac_value = float(100.0 * mac_numerator / mac_denominator)
    return {
        "dac": mac_value,
        "mac": mac_value,
        "dsf": float(abs(scale)),
        "msf": float(abs(msf_scale)),
        "scale_real": float(scale.real),
        "scale_imag": float(scale.imag),
        "scale_phase_deg": float(math.degrees(math.atan2(scale.imag, scale.real))) if abs(scale) > 1e-18 else 0.0,
        "test_norm": float(math.sqrt(test_energy)),
        "fem_norm": float(math.sqrt(fem_energy)),
        "residual_norm": float(np.sqrt(np.vdot(residual, residual).real)),
        "mac_mode": resolved_mac_mode,
        "cross_h_abs": float(abs(cross)),
        "cross_t_abs": float(abs(cross_t)),
        "self_test_h_abs": float(abs(self_test_h)),
        "self_test_t_abs": float(abs(self_test_t)),
        "self_fem_h_abs": float(abs(self_fem_h)),
        "self_fem_t_abs": float(abs(self_fem_t)),
    }


def compute_modal_correlation(project_id, overwrite=True, mac_threshold: Optional[float] = None):
    # Modal correlation enumerates all test-mode / FE-mode combinations and
    # scores them using the already-resolved DOF correspondence table.
    ensure_tables_exist()
    resolved_mac_threshold = None if mac_threshold is None else float(mac_threshold)
    if resolved_mac_threshold is not None and not (0.0 <= resolved_mac_threshold <= 100.0):
        raise ValidationError(
            "mac_threshold must be between 0 and 100",
            {"mac_threshold": mac_threshold},
        )
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _require_modal_project(int(project_id), cursor=cursor)
        cursor.execute("""
            SELECT test_node_id, test_dof, instance_name, fem_node_label, fem_dof,
                   direction_x, direction_y, direction_z
            FROM t_mt_py_fem_dof_match
            WHERE pid = %s
            ORDER BY test_node_id, test_dof
        """, (project_id,))
        dof_matches = cursor.fetchall()
        if not dof_matches:
            raise _required_operation_error(
                "未找到自由度匹配结果，请先完成试验自由度与有限元自由度的匹配操作",
                operation="完成试验自由度与有限元自由度的匹配",
                interface_key="match_dofs",
            )

        test_modes = _load_test_mode_vectors(cursor, project_id)
        if not test_modes:
            raise ValueError("未找到试验模态振型数据")
        mac_mode = _resolve_modal_mac_mode(cursor, int(project_id), test_modes)

        test_freqs = _load_test_modal_frequencies(cursor, project_id)
        fem_modes, fem_freqs = _load_fem_mode_vectors(cursor, project_id)
        if not fem_modes:
            raise _required_operation_error(
                "未找到有限元模态结果，请先完成有限元模态结果导入操作",
                operation="完成有限元模态结果导入",
                interface_key="import_fem_modal",
            )

        if overwrite:
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_modal_correlation
        (pid, test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, msf, mac, freq_test, freq_fem, freq_error_ratio, flip, extra_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            dof_pair_count = VALUES(dof_pair_count),
            dac = VALUES(dac),
            dsf = VALUES(dsf),
            msf = VALUES(msf),
            mac = VALUES(mac),
            freq_test = VALUES(freq_test),
            freq_fem = VALUES(freq_fem),
            freq_error_ratio = VALUES(freq_error_ratio),
            flip = VALUES(flip),
            extra_json = VALUES(extra_json),
            created_at = CURRENT_TIMESTAMP
        """

        all_results = []
        qualified_results = []
        for test_mode_no, test_mode_map in sorted(test_modes.items()):
            for fem_mode_no, fem_mode_map in sorted(fem_modes.items()):
                test_values = []
                fem_values = []
                anchors = []

                for match in dof_matches:
                    # The FE modal vector is projected onto the matched test DOF
                    # direction before DAC/DSF are evaluated.
                    test_point = test_mode_map.get(str(match["test_node_id"]))
                    if test_point is None:
                        continue

                    comp_idx = DOF_COMPONENT_INDEX[str(match["test_dof"]).upper()]
                    test_scalar = complex(test_point[comp_idx])
                    fe_key = (str(match["instance_name"] or ""), int(match["fem_node_label"]))
                    fem_vector = fem_mode_map.get(fe_key)
                    if fem_vector is None:
                        continue

                    direction = np.array([
                        float(match["direction_x"]),
                        float(match["direction_y"]),
                        float(match["direction_z"]),
                    ], dtype=np.float64)
                    fem_scalar = complex(float(np.dot(fem_vector, direction)), 0.0)
                    test_values.append(test_scalar)
                    fem_values.append(fem_scalar)
                    if len(anchors) < 20:
                        anchors.append({
                            "test_node_id": str(match["test_node_id"]),
                            "test_dof": match["test_dof"],
                            "instance_name": match["instance_name"],
                            "fem_node_label": int(match["fem_node_label"]),
                            "fem_dof": match["fem_dof"],
                        })

                if len(test_values) < 2:
                    continue

                metrics = _compute_dac_dsf(
                    np.asarray(test_values, dtype=np.complex128),
                    np.asarray(fem_values, dtype=np.complex128),
                    mac_mode=mac_mode,
                )
                freq_test = test_freqs.get(test_mode_no)
                freq_fem = fem_freqs.get(fem_mode_no)
                freq_error_ratio = None
                if freq_test is not None and abs(freq_test) > 1e-18 and freq_fem is not None:
                    freq_error_ratio = float((freq_fem - freq_test) / freq_test)

                flip = bool(metrics["scale_real"] < 0.0)
                item = {
                    "test_mode_no": int(test_mode_no),
                    "fem_mode_no": int(fem_mode_no),
                    "dof_pair_count": len(test_values),
                    "dac": metrics["dac"],
                    "mac": metrics["mac"],
                    "dsf": metrics["dsf"],
                    "msf": metrics["msf"],
                    "freq_test": freq_test,
                    "freq_fem": freq_fem,
                    "freq_error_ratio": freq_error_ratio,
                    "flip": flip,
                    "extra_json": {
                        "scale_real": metrics["scale_real"],
                        "scale_imag": metrics["scale_imag"],
                        "scale_phase_deg": metrics["scale_phase_deg"],
                        "test_norm": metrics["test_norm"],
                        "fem_norm": metrics["fem_norm"],
                        "residual_norm": metrics["residual_norm"],
                        "mac_mode": metrics["mac_mode"],
                        "test_mode_kind": "complex" if mac_mode == "complex" else "real",
                        "fem_mode_kind": "real",
                        "cross_h_abs": metrics["cross_h_abs"],
                        "cross_t_abs": metrics["cross_t_abs"],
                        "self_test_h_abs": metrics["self_test_h_abs"],
                        "self_test_t_abs": metrics["self_test_t_abs"],
                        "self_fem_h_abs": metrics["self_fem_h_abs"],
                        "self_fem_t_abs": metrics["self_fem_t_abs"],
                        "anchors_preview": anchors,
                    },
                }
                all_results.append(item)
                if resolved_mac_threshold is None or float(item["mac"]) >= resolved_mac_threshold:
                    qualified_results.append(item)
                cursor.execute(insert_sql, (
                    project_id,
                    item["test_mode_no"],
                    item["fem_mode_no"],
                    item["dof_pair_count"],
                    item["dac"],
                    item["dsf"],
                    item["msf"],
                    item["mac"],
                    item["freq_test"],
                    item["freq_fem"],
                    item["freq_error_ratio"],
                    item["flip"],
                    _json_dumps(item["extra_json"]),
                ))

        if not all_results:
            raise ValueError("未生成有效的模态相关性配对结果")

        best_pair_pool = qualified_results if qualified_results else all_results
        best_pair = max(best_pair_pool, key=lambda row: row["dac"])
        # Keep the single strongest modal pair in the legacy static-shape pair
        # table because some existing consumers still read that summary record.
        cursor.execute("""
            INSERT INTO t_mt_py_fem_static_shape_pairs
            (pid, fem_res, test_res, DAC, DSF)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                fem_res = VALUES(fem_res),
                test_res = VALUES(test_res),
                DAC = VALUES(DAC),
                DSF = VALUES(DSF)
        """, (
            project_id,
            f"MODE_{best_pair['fem_mode_no']}",
            f"MODE_{best_pair['test_mode_no']}",
            best_pair["dac"],
            best_pair["dsf"],
        ))

        conn.commit()
        best_by_test_mode = {}
        for item in best_pair_pool:
            key = item["test_mode_no"]
            best = best_by_test_mode.get(key)
            if best is None or item["dac"] > best["dac"]:
                best_by_test_mode[key] = item

        return {
            "project_id": project_id,
            "mac_mode": mac_mode,
            "mac_threshold": resolved_mac_threshold,
            "comparison_count": len(all_results),
            "qualified_comparison_count": len(qualified_results),
            "best_pairs_by_test_mode": [best_by_test_mode[key] for key in sorted(best_by_test_mode)],
            "results_preview": all_results[:20],
            "qualified_results_preview": qualified_results[:20],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _load_modal_correlation_metric_matrix(project_id: int, metric_name: str) -> dict:
    resolved_metric = str(metric_name or "mac").strip().lower()
    if resolved_metric not in {"mac", "msf"}:
        raise ValidationError(
            "unsupported modal correlation metric",
            {"metric_name": metric_name, "allowed": ["mac", "msf"]},
        )

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_mode_no, fem_mode_no, mac, msf
            FROM t_mt_py_fem_modal_correlation
            WHERE pid = %s
            ORDER BY fem_mode_no, test_mode_no
        """, (int(project_id),))
        rows = cursor.fetchall()
        fem_mode_order = sorted({int(row["fem_mode_no"]) for row in rows})
        test_mode_order = sorted({int(row["test_mode_no"]) for row in rows})
        fem_mode_index = {mode_no: idx for idx, mode_no in enumerate(fem_mode_order)}
        test_mode_index = {mode_no: idx for idx, mode_no in enumerate(test_mode_order)}
        metric_matrix = [[None for _ in test_mode_order] for _ in fem_mode_order]
        for row in rows:
            row_idx = fem_mode_index[int(row["fem_mode_no"])]
            col_idx = test_mode_index[int(row["test_mode_no"])]
            value = row.get(resolved_metric)
            metric_matrix[row_idx][col_idx] = float(value) if value is not None else None
        return {
            "project_id": int(project_id),
            "metric_name": resolved_metric,
            "row_mode_order": fem_mode_order,
            "column_mode_order": test_mode_order,
            "matrix": metric_matrix,
        }
    finally:
        cursor.close()
        conn.close()


def get_modal_correlation(project_id):
    return _load_modal_correlation_metric_matrix(int(project_id), "mac")


def get_modal_scale_factor(project_id):
    return _load_modal_correlation_metric_matrix(int(project_id), "msf")


def get_modal_correlation_matrix_payload(project_id):
    raw = get_modal_correlation(project_id)
    row_mode_order = [str(item) for item in (raw.get("row_mode_order") or [])]
    column_mode_order = [str(item) for item in (raw.get("column_mode_order") or [])]
    matrix = [list(row) for row in (raw.get("matrix") or [])]

    heatmap_points = []
    for row_index, _row_name in enumerate(row_mode_order):
        current_row = matrix[row_index] if row_index < len(matrix) else []
        for col_index, _col_name in enumerate(column_mode_order):
            heatmap_points.append([
                row_index,
                col_index,
                current_row[col_index] if col_index < len(current_row) else None,
            ])

    return {
        "project_id": int(project_id),
        "row_mode_order": row_mode_order,
        "column_mode_order": column_mode_order,
        "data": {
            "rows": row_mode_order,
            "column": column_mode_order,
            "data": heatmap_points,
        },
        "summary": {
            "fem_mode_count": len(row_mode_order),
            "test_mode_count": len(column_mode_order),
            "point_count": len(heatmap_points),
        },
    }


def get_modal_correlation_table_payload(project_id):
    raw = get_modal_correlation(project_id)
    row_mode_order = [str(item) for item in (raw.get("row_mode_order") or [])]
    column_mode_order = [str(item) for item in (raw.get("column_mode_order") or [])]
    matrix = [list(row) for row in (raw.get("matrix") or [])]

    table_rows = []
    for row_index, _row_name in enumerate(row_mode_order):
        current_row = matrix[row_index] if row_index < len(matrix) else []
        row_item = {}
        for col_index, col_name in enumerate(column_mode_order):
            row_item[col_name] = current_row[col_index] if col_index < len(current_row) else None
        table_rows.append(row_item)

    return {
        "project_id": int(project_id),
        "row_mode_order": row_mode_order,
        "column_mode_order": column_mode_order,
        "rows": row_mode_order,
        "column": column_mode_order,
        "data": table_rows,
        "summary": {
            "fem_mode_count": len(row_mode_order),
            "test_mode_count": len(column_mode_order),
            "point_count": len(row_mode_order) * len(column_mode_order),
        },
    }


def get_modal_scale_factor_table_payload(project_id):
    raw = get_modal_scale_factor(project_id)
    row_mode_order = [str(item) for item in (raw.get("row_mode_order") or [])]
    column_mode_order = [str(item) for item in (raw.get("column_mode_order") or [])]
    matrix = [list(row) for row in (raw.get("matrix") or [])]

    table_rows = []
    for row_index, _row_name in enumerate(row_mode_order):
        current_row = matrix[row_index] if row_index < len(matrix) else []
        row_item = {}
        for col_index, col_name in enumerate(column_mode_order):
            row_item[col_name] = current_row[col_index] if col_index < len(current_row) else None
        table_rows.append(row_item)

    return {
        "project_id": int(project_id),
        "row_mode_order": row_mode_order,
        "column_mode_order": column_mode_order,
        "rows": row_mode_order,
        "column": column_mode_order,
        "data": table_rows,
        "summary": {
            "fem_mode_count": len(row_mode_order),
            "test_mode_count": len(column_mode_order),
            "point_count": len(row_mode_order) * len(column_mode_order),
        },
    }


def get_modal_match_frequency_scatter_payload(
        project_id: int,
        *,
        mac_threshold: float = 0.7,
        max_freq_error_ratio: Optional[float] = 0.2,
        method: str = "greedy",
        subcase_name="SUBCASE_1",
) -> dict:
    matched = match_modal_modes(
        int(project_id),
        mac_threshold=mac_threshold,
        max_freq_error_ratio=max_freq_error_ratio,
        method=method,
        subcase_name=subcase_name,
    )
    rows = list(matched.get("rows") or [])

    scatter_points = []
    tooltip_points = []
    xaxis = []
    for row in rows:
        freq_fem = row.get("freq_fem")
        freq_test = row.get("freq_test")
        if freq_fem is None or freq_test is None:
            continue
        x_value = f"{float(freq_fem):.3f}"
        y_value = float(freq_test)
        xaxis.append(x_value)
        scatter_points.append([x_value, y_value])
        tooltip_points.append(
            {
                "x": x_value,
                "y": y_value,
                "tooltip": {
                    "title": row.get("title"),
                    "fem_mode_no": int(row["fem_mode_no"]),
                    "test_mode_no": int(row["test_mode_no"]),
                    "freq_fem": x_value,
                    "freq_test": y_value,
                    "mac": row.get("mac"),
                    "freq_error_ratio": row.get("freq_error_ratio"),
                    "status": row.get("status"),
                    "recommended": bool(row.get("recommended")),
                    "flip": bool(row.get("flip")),
                },
            }
        )

    return {
        "project_id": int(project_id),
        "chart_type": "scatter",
        "method": matched.get("method"),
        "mac_threshold": matched.get("mac_threshold"),
        "max_freq_error_ratio": matched.get("max_freq_error_ratio"),
        "x_label": "calculated_modal_frequency",
        "y_label": "test_modal_frequency",
        "data": [
            {
                "label": "matched_modes",
                "xaxis": xaxis,
                "data": scatter_points,
                "points": tooltip_points,
            }
        ],
        "summary": {
            **dict(matched.get("summary") or {}),
            "point_count": len(scatter_points),
            "tooltip_count": len(tooltip_points),
        },
        "rows": rows,
        "unmatched_fem_modes": list(matched.get("unmatched_fem_modes") or []),
        "unmatched_test_modes": list(matched.get("unmatched_test_modes") or []),
    }


def _normalize_modal_match_mac_threshold(mac_threshold: float) -> float:
    resolved = float(mac_threshold)
    if resolved < 0.0:
        raise ValidationError(
            "mac_threshold must be >= 0",
            {"mac_threshold": mac_threshold},
        )
    if resolved <= 1.0:
        return float(resolved * 100.0)
    if resolved <= 100.0:
        return resolved
    raise ValidationError(
        "mac_threshold must be between 0 and 1 or between 0 and 100",
        {"mac_threshold": mac_threshold},
    )


def _load_modal_correlation_rows(project_id: int) -> List[dict]:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_mode_no, fem_mode_no, dof_pair_count, dac, dsf, msf, mac,
                   freq_test, freq_fem, freq_error_ratio, flip
            FROM t_mt_py_fem_modal_correlation
            WHERE pid = %s
            ORDER BY fem_mode_no, test_mode_no
        """, (int(project_id),))
        rows = cursor.fetchall() or []
        return [dict(row) for row in rows]
    finally:
        cursor.close()
        conn.close()


def _ensure_modal_correlation_rows(project_id: int) -> List[dict]:
    rows = _load_modal_correlation_rows(int(project_id))
    if rows:
        return rows
    compute_modal_correlation(int(project_id), overwrite=True, mac_threshold=None)
    return _load_modal_correlation_rows(int(project_id))


def _build_modal_frequency_order_rows(project_id: int) -> dict:
    rows = _ensure_modal_correlation_rows(int(project_id))
    if not rows:
        raise ValueError("未找到模态相关性结果，请先完成 MAC 计算")

    fem_mode_order = sorted({int(row["fem_mode_no"]) for row in rows})
    test_mode_order = sorted({int(row["test_mode_no"]) for row in rows})
    row_by_key = {
        (int(row["test_mode_no"]), int(row["fem_mode_no"])): dict(row)
        for row in rows
    }

    fem_freq_by_mode: Dict[int, Optional[float]] = {}
    test_freq_by_mode: Dict[int, Optional[float]] = {}
    for row in rows:
        fem_mode_no = int(row["fem_mode_no"])
        test_mode_no = int(row["test_mode_no"])
        if fem_mode_no not in fem_freq_by_mode:
            fem_freq_by_mode[fem_mode_no] = _safe_float(row.get("freq_fem"))
        if test_mode_no not in test_freq_by_mode:
            test_freq_by_mode[test_mode_no] = _safe_float(row.get("freq_test"))

    paired_rows: List[dict] = []
    comparable_error_ratios: List[float] = []
    for index, test_mode_no in enumerate(test_mode_order):
        if index >= len(fem_mode_order):
            break
        fem_mode_no = fem_mode_order[index]
        freq_test = test_freq_by_mode.get(test_mode_no)
        freq_fem = fem_freq_by_mode.get(fem_mode_no)
        pair_row = row_by_key.get((int(test_mode_no), int(fem_mode_no))) or {}
        freq_error = None
        freq_error_ratio = None
        if freq_fem is not None and freq_test is not None:
            freq_error = float(freq_fem - freq_test)
            if abs(freq_test) > 1e-18:
                freq_error_ratio = float(freq_error / freq_test)
                comparable_error_ratios.append(abs(freq_error_ratio))
        freq_error_percent = None if freq_error_ratio is None else float(freq_error_ratio * 100.0)
        paired_rows.append({
            "order_index": index + 1,
            "test_mode_no": int(test_mode_no),
            "fem_mode_no": int(fem_mode_no),
            "freq_test": freq_test,
            "freq_fem": freq_fem,
            "freq_error": freq_error,
            "freq_error_ratio": freq_error_ratio,
            "freq_error_percent": freq_error_percent,
            "mac": _safe_float(pair_row.get("mac")),
            "msf": _safe_float(pair_row.get("msf")),
            "dof_pair_count": int(pair_row["dof_pair_count"]) if pair_row.get("dof_pair_count") is not None else None,
            "flip": bool(pair_row.get("flip", False)) if pair_row else False,
        })

    return {
        "rows": paired_rows,
        "summary": {
            "fem_mode_count": len(fem_mode_order),
            "test_mode_count": len(test_mode_order),
            "compared_mode_count": len(paired_rows),
            "mean_abs_error_ratio": (
                float(sum(comparable_error_ratios) / len(comparable_error_ratios))
                if comparable_error_ratios else None
            ),
            "max_abs_error_ratio": (
                float(max(comparable_error_ratios))
                if comparable_error_ratios else None
            ),
            "mean_abs_error_percent": (
                float((sum(comparable_error_ratios) / len(comparable_error_ratios)) * 100.0)
                if comparable_error_ratios else None
            ),
            "max_abs_error_percent": (
                float(max(comparable_error_ratios) * 100.0)
                if comparable_error_ratios else None
            ),
        },
    }


def get_modal_frequency_consistency_payload(project_id: int) -> dict:
    paired = _build_modal_frequency_order_rows(int(project_id))
    rows = list(paired["rows"])
    xaxis = []
    line_points = []
    tooltip_points = []
    for row in rows:
        order_index = int(row["order_index"])
        y_value = row.get("freq_error_percent")
        if y_value is None:
            continue
        x_value = str(order_index)
        xaxis.append(x_value)
        line_points.append(y_value)
        tooltip_points.append({
            "x": x_value,
            "y": y_value,
            "tooltip": {
                "order_index": order_index,
                "test_mode_no": int(row["test_mode_no"]),
                "fem_mode_no": int(row["fem_mode_no"]),
                "freq_test": row.get("freq_test"),
                "freq_fem": row.get("freq_fem"),
                "freq_error": row.get("freq_error"),
                "freq_error_ratio": row.get("freq_error_ratio"),
                "freq_error_percent": y_value,
                "mac": row.get("mac"),
            },
        })
    result = {
        "project_id": int(project_id),
        "project_type": "MTXZ",
        "chart_type": "line",
        "x_label": "mode_order",
        "y_label": "frequency_error_percent",
        "data": [
            {
                "label": "frequency_consistency_error",
                "xaxis": xaxis,
                "data": line_points,
                "points": tooltip_points,
            }
        ],
        "rows": rows,
        "summary": {
            **dict(paired["summary"]),
            "point_count": len(line_points),
        },
    }
    update_work_condition_project_status(
        int(project_id),
        consistency_status=1,
    )
    return result


def get_modal_correlation_all_scatter_payload(
        project_id: int,
        *,
        mac_threshold: float = 0.7,
        max_freq_error_ratio: Optional[float] = 0.2,
        method: str = "greedy",
        subcase_name="SUBCASE_1",
) -> dict:
    matched = match_modal_modes(
        int(project_id),
        mac_threshold=mac_threshold,
        max_freq_error_ratio=max_freq_error_ratio,
        method=method,
        subcase_name=subcase_name,
    )
    rows = list(matched.get("rows") or [])

    scatter_points = []
    tooltip_points = []
    xaxis = []
    mac_values = []
    for row in rows:
        freq_fem = _safe_float(row.get("freq_fem"))
        freq_test = _safe_float(row.get("freq_test"))
        if freq_fem is None or freq_test is None:
            continue
        x_value = f"{float(freq_fem):.3f}"
        y_value = float(freq_test)
        mac = float(row["mac"]) if row.get("mac") is not None else None
        xaxis.append(x_value)
        scatter_points.append([x_value, y_value])
        tooltip_points.append({
            "x": x_value,
            "y": y_value,
            "value": mac,
            "tooltip": {
                "fem_mode_no": int(row["fem_mode_no"]),
                "test_mode_no": int(row["test_mode_no"]),
                "mac": mac,
                "freq_fem": x_value,
                "freq_test": y_value,
                "freq_error_ratio": _safe_float(row.get("freq_error_ratio")),
                "dof_pair_count": int(row["dof_pair_count"]) if row.get("dof_pair_count") is not None else None,
                "flip": bool(row.get("flip", False)),
            },
        })
        if mac is not None:
            mac_values.append(mac)

    return {
        "project_id": int(project_id),
        "chart_type": "scatter",
        "method": matched.get("method"),
        "mac_threshold": matched.get("mac_threshold"),
        "max_freq_error_ratio": matched.get("max_freq_error_ratio"),
        "x_label": "calculated_modal_frequency",
        "y_label": "test_modal_frequency",
        "value_label": "mac",
        "data": [
            {
                "label": "matched_modes",
                "xaxis": xaxis,
                "data": scatter_points,
                "points": tooltip_points,
            }
        ],
        "summary": {
            "point_count": len(scatter_points),
            **dict(matched.get("summary") or {}),
            "max_mac": float(max(mac_values)) if mac_values else None,
            "min_mac": float(min(mac_values)) if mac_values else None,
        },
        "rows": rows,
        "unmatched_fem_modes": list(matched.get("unmatched_fem_modes") or []),
        "unmatched_test_modes": list(matched.get("unmatched_test_modes") or []),
    }


def _passes_modal_match_filters(
        row: dict,
        *,
        mac_threshold: float,
        max_freq_error_ratio: Optional[float],
) -> bool:
    resolved_mac_threshold = _normalize_modal_match_mac_threshold(mac_threshold)
    mac = row.get("mac")
    if mac is None or float(mac) < float(resolved_mac_threshold):
        return False
    if max_freq_error_ratio is None:
        return True
    freq_error_ratio = row.get("freq_error_ratio")
    if freq_error_ratio is None:
        return True
    return abs(float(freq_error_ratio)) <= float(max_freq_error_ratio)


def _build_modal_match_row(row: dict, *, status: str, recommended: bool, rank: Optional[int] = None, subcase_name=None) -> dict:
    if not subcase_name or len(subcase_name) == 0:
        step_name = "SUBCASE_1"
    else:
        step_name = subcase_name[-1]
    fem_mode_no = int(row["fem_mode_no"])
    test_mode_no = int(row["test_mode_no"])
    mac = float(row["mac"]) if row.get("mac") is not None else None
    fem_frequency = float(row["freq_fem"]) if row.get("freq_fem") is not None else None
    test_frequency = float(row["freq_test"]) if row.get("freq_test") is not None else None
    freq_error_ratio = float(row["freq_error_ratio"]) if row.get("freq_error_ratio") is not None else None
    flip = bool(row.get("flip", False))
    mac_text = f"{mac:.3f}" if mac is not None else "N/A"
    fem_frequency_text = f"{fem_frequency:.4f}" if fem_frequency is not None else "N/A"
    test_frequency_text = f"{test_frequency:.4f}" if test_frequency is not None else "N/A"
    return {
        "status": status,
        "recommended": bool(recommended),
        "rank": int(rank) if rank is not None else None,
        "fem_mode_no": fem_mode_no,
        "test_mode_no": test_mode_no,
        "mac": mac,
        "freq_fem": fem_frequency,
        "freq_test": test_frequency,
        "freq_error_ratio": freq_error_ratio,
        "flip": flip,
        "fem_step": step_name,
        "title": f"FEA {fem_mode_no} - {fem_frequency_text}Hz, EMA {test_mode_no} - {test_frequency_text} MAC:{mac_text}",
        "fem_frame": fem_mode_no - 1,
        "fem_field": "U",
        "fem_mode": "smooth",
        "fem_component_idx": 2,
        "test_component": "usum",
        "test_order": test_mode_no
    }


def preview_modal_match(project_id: int, *, mac_threshold: float = 0.7,
                        max_candidates_per_mode: int = 3,
                        max_freq_error_ratio: Optional[float] = 0.2) -> dict:
    rows = _load_modal_correlation_rows(project_id)
    if not rows:
        raise ValueError("未找到模态相关性结果，请先完成 MAC 计算")

    candidate_rows_by_fem: Dict[int, List[dict]] = {}
    all_fem_modes = sorted({int(row["fem_mode_no"]) for row in rows})
    all_test_modes = sorted({int(row["test_mode_no"]) for row in rows})

    for row in rows:
        if not _passes_modal_match_filters(
                row,
                mac_threshold=mac_threshold,
                max_freq_error_ratio=max_freq_error_ratio,
        ):
            continue
        candidate_rows_by_fem.setdefault(int(row["fem_mode_no"]), []).append(row)

    preview_rows: List[dict] = []
    unmatched_fem_modes: List[int] = []
    for fem_mode_no in all_fem_modes:
        candidates = list(candidate_rows_by_fem.get(fem_mode_no, []))
        candidates.sort(
            key=lambda item: (
                -(float(item["mac"]) if item.get("mac") is not None else -1.0),
                abs(float(item["freq_error_ratio"])) if item.get("freq_error_ratio") is not None else math.inf,
                int(item["test_mode_no"]),
            )
        )
        limited = candidates[:max(int(max_candidates_per_mode), 1)]
        if not limited:
            unmatched_fem_modes.append(int(fem_mode_no))
            continue
        for rank, item in enumerate(limited, start=1):
            preview_rows.append(
                _build_modal_match_row(
                    item,
                    status="candidate",
                    recommended=(rank == 1),
                    rank=rank,
                )
            )

    matched_test_modes = sorted({int(row["test_mode_no"]) for row in preview_rows})
    unmatched_test_modes = [mode_no for mode_no in all_test_modes if mode_no not in matched_test_modes]

    return {
        "project_id": int(project_id),
        "method": "candidate_preview",
        "mac_threshold": float(_normalize_modal_match_mac_threshold(mac_threshold)),
        "max_freq_error_ratio": None if max_freq_error_ratio is None else float(max_freq_error_ratio),
        "max_candidates_per_mode": max(int(max_candidates_per_mode), 1),
        "rows": preview_rows,
        "summary": {
            "fem_mode_count": len(all_fem_modes),
            "test_mode_count": len(all_test_modes),
            "candidate_count": len(preview_rows),
            "matched_fem_mode_count": len(all_fem_modes) - len(unmatched_fem_modes),
            "unmatched_fem_mode_count": len(unmatched_fem_modes),
            "unmatched_test_mode_count": len(unmatched_test_modes),
        },
        "unmatched_fem_modes": unmatched_fem_modes,
        "unmatched_test_modes": unmatched_test_modes,
    }


def match_modal_modes(project_id: int, *, mac_threshold: float = 0.7,
                      max_freq_error_ratio: Optional[float] = 0.2,
                      method: str = "greedy",
                      subcase_name="SUBCASE_1") -> dict:
    resolved_method = str(method or "greedy").strip().lower()
    if resolved_method != "greedy":
        raise ValidationError(
            "unsupported modal matching method",
            {"method": method, "allowed": ["greedy"]},
        )

    rows = _load_modal_correlation_rows(project_id)
    if not rows:
        raise ValueError("未找到模态相关性结果，请先完成 MAC 计算")

    all_fem_modes = sorted({int(row["fem_mode_no"]) for row in rows})
    all_test_modes = sorted({int(row["test_mode_no"]) for row in rows})
    candidates = [
        row for row in rows
        if _passes_modal_match_filters(
            row,
            mac_threshold=mac_threshold,
            max_freq_error_ratio=max_freq_error_ratio,
        )
    ]
    candidates.sort(
        key=lambda item: (
            -(float(item["mac"]) if item.get("mac") is not None else -1.0),
            abs(float(item["freq_error_ratio"])) if item.get("freq_error_ratio") is not None else math.inf,
            int(item["fem_mode_no"]),
            int(item["test_mode_no"]),
        )
    )

    used_fem = set()
    used_test = set()
    matched_rows: List[dict] = []
    rejected_rows: List[dict] = []

    for item in candidates:
        fem_mode_no = int(item["fem_mode_no"])
        test_mode_no = int(item["test_mode_no"])
        if fem_mode_no in used_fem or test_mode_no in used_test:
            rejected_rows.append(
                _build_modal_match_row(
                    item,
                    status="candidate_conflict",
                    recommended=False,
                )
            )
            continue
        used_fem.add(fem_mode_no)
        used_test.add(test_mode_no)
        matched_rows.append(
            _build_modal_match_row(
                item,
                status="matched",
                recommended=True,
                subcase_name=subcase_name
            )
        )

    matched_rows.sort(key=lambda item: item["fem_mode_no"])
    unmatched_fem_modes = [mode_no for mode_no in all_fem_modes if mode_no not in used_fem]
    unmatched_test_modes = [mode_no for mode_no in all_test_modes if mode_no not in used_test]

    return {
        "project_id": int(project_id),
        "method": resolved_method,
        "mac_threshold": float(_normalize_modal_match_mac_threshold(mac_threshold)),
        "max_freq_error_ratio": None if max_freq_error_ratio is None else float(max_freq_error_ratio),
        "rows": matched_rows,
        "rejected_candidates": rejected_rows,
        "summary": {
            "fem_mode_count": len(all_fem_modes),
            "test_mode_count": len(all_test_modes),
            "matched_pair_count": len(matched_rows),
            "candidate_conflict_count": len(rejected_rows),
            "unmatched_fem_mode_count": len(unmatched_fem_modes),
            "unmatched_test_mode_count": len(unmatched_test_modes),
        },
        "unmatched_fem_modes": unmatched_fem_modes,
        "unmatched_test_modes": unmatched_test_modes,
    }
