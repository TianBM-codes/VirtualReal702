"""FEM node/DOF matching and octree cache helpers."""

from .fem_catalog_service import *

def _get_latest_octree_meta(cursor, project_id):
    cursor.execute("""
        SELECT source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at
        FROM t_mt_py_fem_node_octree_cache
        WHERE pid = %s
        ORDER BY updated_at DESC
        LIMIT 1
    """, (project_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("未找到节点八叉树缓存，请先导入 inp")
    meta = dict(row)
    meta["source_file_path"] = _normalize_stored_path(meta.get("source_file_path"))
    meta["cache_file_path"] = _normalize_stored_path(meta.get("cache_file_path"))
    return meta


def _ensure_octree_cache_file(cursor, project_id: int, octree_meta: dict) -> str:
    cache_path = _normalize_stored_path(octree_meta.get("cache_file_path"))
    if cache_path and os.path.exists(cache_path):
        return cache_path

    source_file_path = _normalize_stored_path(octree_meta.get("source_file_path"))
    if not source_file_path or not os.path.exists(source_file_path):
        source_file_path = resolve_project_source_inp_path(int(project_id))

    model = parse_inp(source_file_path, resolve_refs=True)
    node_data = _collect_global_nodes(model)
    rebuilt_cache_path = _save_octree_cache(
        project_id=int(project_id),
        source_file_path=source_file_path,
        node_data=node_data,
        force_rebuild=True,
    )

    cursor.execute(
        """
        INSERT INTO t_mt_py_fem_node_octree_cache
        (pid, source_file_path, cache_file_path, node_count, instance_count, bbox_min, bbox_max, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            cache_file_path = VALUES(cache_file_path),
            node_count = VALUES(node_count),
            instance_count = VALUES(instance_count),
            bbox_min = VALUES(bbox_min),
            bbox_max = VALUES(bbox_max),
            updated_at = NOW()
        """,
        (
            int(project_id),
            os.path.abspath(source_file_path),
            os.path.abspath(rebuilt_cache_path),
            int(len(node_data["point_labels"])),
            int(len(node_data["entries"])),
            _json_dumps(node_data["bbox_min"].tolist()),
            _json_dumps(node_data["bbox_max"].tolist()),
        ),
    )
    return os.path.abspath(rebuilt_cache_path)


def match_test_nodes(project_id, max_distance=None, overwrite=True,
                     auto_translate=False, translation=None, rotation=None, auto_rotate=False):
    # Match imported test nodes onto the FE node cloud stored in the octree
    # cache. The saved mapping is reused by DOF matching and correlation steps.
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        log_project_step(
            int(project_id),
            "开始执行节点匹配",
            stage="node_match_started",
            percent=0,
        )
        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache_path = _ensure_octree_cache_file(cursor, int(project_id), octree_meta)

        test_nodes, test_node_source_table = _load_test_nodes_for_matching(cursor, int(project_id))
        if not test_nodes:
            raise ValueError("未找到试验节点，请先导入试验数据")

        cache = _load_octree_cache(cache_path)
        part_lookup = _cache_part_lookup(cache)
        raw_test_coords = np.array(
            [[float(row["x_position"]), float(row["y_position"]), float(row["z_position"])] for row in test_nodes],
            dtype=np.float64,
        )
        match_context = get_node_match_parameter_context(int(project_id), cursor=cursor)
        tolerance = float(match_context["tolerance"])
        recommended_max_distance = float(match_context["maximum_node_point_distance"])
        resolved_max_distance = recommended_max_distance if max_distance is None else float(max_distance)

        manual_transform = translation is not None or rotation is not None
        transform_mode = "none"
        fit_info = None
        if manual_transform:
            # Manual transform overrides auto-fit so users can reproduce or
            # compare a known registration against the automatic estimate.
            applied_translation = np.asarray(translation or [0.0, 0.0, 0.0], dtype=np.float64).reshape(3)
            applied_rotation = _rotation_from_matrix(_rotation_to_matrix(rotation), center=(rotation or {}).get("center"))
            transform_mode = "manual"
        elif auto_translate and auto_rotate and len(raw_test_coords) >= 3:
            # ICP gives the best rigid registration when both translation and
            # rotation are allowed and we have enough points to fit a transform.
            estimate = _estimate_rigid_transform_icp(cache, raw_test_coords)
            applied_translation = estimate["translation"]
            applied_rotation = _rotation_from_matrix(estimate["rotation_matrix"])
            transform_mode = estimate["mode"]
            fit_info = {
                "iterations": estimate["iterations"],
                "rmse": estimate["rmse"],
                "pair_count": estimate["pair_count"],
            }
        elif auto_translate:
            applied_translation, transform_mode = _estimate_translation(cache["point_coords"], raw_test_coords)
            applied_rotation = None
        else:
            applied_translation = np.zeros(3, dtype=np.float64)
            applied_rotation = None

        transformed_coords = _apply_transform(
            raw_test_coords,
            translation=applied_translation,
            rotation=applied_rotation,
        )
        transform_payload = {
            "mode": transform_mode,
            "translation": applied_translation.tolist(),
            "rotation": applied_rotation,
            "fit": fit_info,
        }

        matches = []
        for row, coord_after in zip(test_nodes, transformed_coords):
            # Each test node keeps both the matched FE node and the residual
            # offset after registration so mismatches are visible in the DB.
            point_idx, distance = _octree_nearest(cache, coord_after)
            if point_idx < 0:
                continue
            fem_coord = cache["point_coords"][point_idx]
            fem_label = int(cache["point_labels"][point_idx])
            inst_name = str(cache["point_instances"][point_idx])
            delta = fem_coord - coord_after
            match = {
                "test_node_id": str(row["test_node_id"]),
                "test_node_source": test_node_source_table,
                "instance_name": inst_name,
                "part_name": part_lookup.get((inst_name, fem_label)),
                "fem_node_label": fem_label,
                "fem_coord": fem_coord.tolist(),
                "distance": float(distance),
                "x_offset": float(delta[0]),
                "y_offset": float(delta[1]),
                "z_offset": float(delta[2]),
            }
            if float(distance) <= resolved_max_distance:
                matches.append(match)

        if overwrite:
            # Downstream tables depend on the node mapping, so they are cleared
            # together when the caller requests a fresh node alignment.
            log_project_info(
                int(project_id),
                "节点匹配将覆盖旧结果，并清理相关自由度匹配与响应目录数据",
                stage="node_match_overwrite",
                percent=10,
            )
            cursor.execute("DELETE FROM t_mt_py_fem_node_match WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_dof_match WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_response_catalog WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_node_match
        (pid, test_node_id, instance_name, fem_node_label, distance, x_offset, y_offset, z_offset, transform_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            instance_name = VALUES(instance_name),
            fem_node_label = VALUES(fem_node_label),
            distance = VALUES(distance),
            x_offset = VALUES(x_offset),
            y_offset = VALUES(y_offset),
            z_offset = VALUES(z_offset),
            transform_json = VALUES(transform_json),
            created_at = CURRENT_TIMESTAMP
        """
        transform_json = _json_dumps(transform_payload)
        for item in matches:
            cursor.execute(insert_sql, (
                project_id,
                item["test_node_id"],
                item["instance_name"],
                item["fem_node_label"],
                item["distance"],
                item["x_offset"],
                item["y_offset"],
                item["z_offset"],
                transform_json,
            ))

        insert_sql = """
        INSERT INTO t_mt_py_fem_node_pairs
        (pid, node, point, distance, x_offset, y_offset, z_offset)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            distance = VALUES(distance),
            x_offset = VALUES(x_offset),
            y_offset = VALUES(y_offset),
            z_offset = VALUES(z_offset)
        """
        for item in matches:
            cursor.execute(insert_sql, (
                project_id,
                item["fem_node_label"],
                item["test_node_id"],
                item["distance"],
                item["x_offset"],
                item["y_offset"],
                item["z_offset"],
            ))
        update_work_condition_project_status(
            int(project_id),
            cursor=cursor,
            space_match_status=1,
        )
        conn.commit()
        log_project_step(
            int(project_id),
            f"节点匹配完成，已匹配 {len(matches)}/{len(test_nodes)} 个测点",
            stage="node_match_finished",
            percent=100,
        )

        return {
            "project_id": project_id,
            "tested_points": len(test_nodes),
            "matched_points": len(matches),
            "tolerance": tolerance,
            "maximum_node_point_distance": recommended_max_distance,
            "transform": transform_payload,
            "max_distance": resolved_max_distance,
            "matches_preview": matches[:20],
            "octree_cache_path": cache_path,
            "test_node_source_table": test_node_source_table,
        }
    except Exception as exc:
        conn.rollback()
        log_project_error(
            int(project_id),
            f"节点匹配失败: {exc}",
            stage="failed",
        )
        raise
    finally:
        cursor.close()
        conn.close()


def _coord_key(values) -> tuple:
    arr = np.asarray(values, dtype=np.float64).reshape(3)
    return tuple(float(f"{item:.12g}") for item in arr.tolist())


def get_pair_node_point_result(project_id):
    ensure_tables_exist()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache = _load_octree_cache(octree_meta["cache_file_path"])

        cursor.execute("""
            SELECT id, test_node_id, instance_name, fem_node_label
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY id
        """, (project_id,))
        node_matches = cursor.fetchall()
        if not node_matches:
            raise _required_operation_error(
                "未找到节点匹配结果，请选择模型修正=>节点测点匹配",
                operation="完成测点与有限元节点的空间匹配",
                interface_key="pair_node_point_result",
            )

        if _is_modal_unv_project(int(project_id), cursor=cursor):
            cursor.execute("""
                SELECT CAST(nid AS CHAR) AS test_node_id, x AS x_position, y AS y_position, z AS z_position
                FROM t_mt_py_test_node
                WHERE pid = %s
                ORDER BY nid
            """, (project_id,))
            test_node_rows = cursor.fetchall()
            test_node_lookup = {
                str(row["test_node_id"]): _coord_key([row["x_position"], row["y_position"], row["z_position"]])
                for row in test_node_rows
                if row.get("test_node_id") not in (None, "")
            }
        else:
            cursor.execute("""
                SELECT id, measuring_point_name, x_position, y_position, z_position
                FROM t_mt_measuring_point_info
                WHERE project_id = %s
                ORDER BY id
            """, (project_id,))
            measuring_rows = cursor.fetchall()
            test_node_lookup = {
                str(row["measuring_point_name"]): _coord_key([row["x_position"], row["y_position"], row["z_position"]])
                for row in measuring_rows
                if row.get("measuring_point_name") not in (None, "")
            }

        fem_coord_lookup = {}
        for inst_name, label, coord in zip(cache["point_instances"], cache["point_labels"], cache["point_coords"]):
            fem_coord_lookup[(str(inst_name), int(label))] = [
                float(coord[0]),
                float(coord[1]),
                float(coord[2]),
            ]

        sensor_names = []
        node_xyz = []
        for row in node_matches:
            test_node_id = str(row["test_node_id"])
            coord_key = test_node_lookup.get(test_node_id)
            if coord_key is None:
                continue
            fem_coord = fem_coord_lookup.get((str(row["instance_name"] or ""), int(row["fem_node_label"])))
            if fem_coord is None:
                continue
            sensor_names.append(test_node_id)
            node_xyz.append(fem_coord)

        return {
            "sensor_name": sensor_names,
            "node_xyz": node_xyz,
        }
    finally:
        cursor.close()
        conn.close()


def save_transform_operation(project_id: int, matrix4_fem, matrix4_test):
    ensure_tables_exist()
    resolved_matrix4_fem = _normalize_matrix4(matrix4_fem)
    resolved_matrix4_test = _normalize_matrix4(matrix4_test)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        for transform_type, matrix4 in (
            ("fem", resolved_matrix4_fem),
            ("test", resolved_matrix4_test),
        ):
            cursor.execute(
                """
                INSERT INTO t_mt_py_fem_transform_operation (pid, transform_type, matrix4_json)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    matrix4_json = VALUES(matrix4_json),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(project_id), transform_type, _json_dumps(matrix4)),
            )
        conn.commit()
        return {
            "project_id": int(project_id),
            "matrix4_fem": resolved_matrix4_fem,
            "matrix4_test": resolved_matrix4_test,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_transform_auto_info(project_id: int):
    ensure_tables_exist()

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT transform_type, matrix4_json
            FROM t_mt_py_fem_transform_operation
            WHERE pid = %s
            """,
            (int(project_id),),
        )
        rows = cursor.fetchall() or []
        matrix_by_type = {}
        for row in rows:
            transform_type = row.get("transform_type")
            matrix4_json = row.get("matrix4_json")
            if transform_type is None or matrix4_json is None:
                continue
            matrix_by_type[str(transform_type).strip().lower()] = _normalize_matrix4(_json_loads(matrix4_json))
        identity = np.eye(4, dtype=np.int32).tolist()
        return {
            "matrix4_fem": matrix_by_type.get("fem", identity),
            "matrix4_test": matrix_by_type.get("test", identity),
        }
    finally:
        cursor.close()
        conn.close()


_CHANNEL_DIRECTION_TO_DOF = {
    1: ("UX", "U1", np.array([1.0, 0.0, 0.0], dtype=np.float64)),
    2: ("UY", "U2", np.array([0.0, 1.0, 0.0], dtype=np.float64)),
    3: ("UZ", "U3", np.array([0.0, 0.0, 1.0], dtype=np.float64)),
}


def _resolve_channel_direction_vector(direction_value, data_operate_value, *, measuring_point_name: str, channel_id=None):
    try:
        direction = int(direction_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "通道方向无效，必须为 1/2/3",
            {
                "measuring_point_name": measuring_point_name,
                "channel_id": channel_id,
                "direction": direction_value,
            },
        ) from exc

    direction_spec = _CHANNEL_DIRECTION_TO_DOF.get(direction)
    if direction_spec is None:
        raise ValidationError(
            "通道方向无效，必须为 1/2/3",
            {
                "measuring_point_name": measuring_point_name,
                "channel_id": channel_id,
                "direction": direction,
            },
        )

    sign_text = str(data_operate_value or "").strip()
    if sign_text not in {"+", "-"}:
        raise ValidationError(
            "通道 data_operate 无效，必须为 '+' 或 '-'",
            {
                "measuring_point_name": measuring_point_name,
                "channel_id": channel_id,
                "data_operate": data_operate_value,
            },
        )

    sign = -1.0 if sign_text == "-" else 1.0
    test_dof, fem_dof, base_direction = direction_spec
    return test_dof, fem_dof, (base_direction * sign).astype(np.float64, copy=False)


def _build_modal_unv_dof_amplitudes(cursor, project_id: int) -> Dict[Tuple[str, str], float]:
    # Use the maximum measured modal amplitude of each translational direction
    # as the DOF availability score. If one direction stays below the threshold
    # across all imported modes, that direction is treated as unusable.
    modes = _load_test_mode_vectors(cursor, int(project_id))
    amplitudes: Dict[Tuple[str, str], float] = {}
    for mode_map in modes.values():
        for point_id, vec in mode_map.items():
            vec_abs = np.abs(np.asarray(vec, dtype=np.complex128))
            point_id_text = str(point_id)
            for idx, test_dof in enumerate(TEST_DOF_SEQUENCE):
                key = (point_id_text, test_dof)
                amplitudes[key] = max(float(amplitudes.get(key, 0.0)), float(vec_abs[idx]))
    return amplitudes


def match_test_dofs(project_id, overwrite=True, min_match_score=None):
    ensure_tables_exist()
    auto_created_node_match = _ensure_node_matches(int(project_id))
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        log_project_step(
            int(project_id),
            "开始执行自由度匹配",
            stage="dof_match_started",
            percent=0,
        )
        if auto_created_node_match:
            log_project_info(
                int(project_id),
                "自由度匹配前自动补齐了节点匹配结果",
                stage="dof_match_prepare",
                percent=5,
            )
        cursor.execute("""
            SELECT test_node_id, instance_name, fem_node_label, transform_json
            FROM t_mt_py_fem_node_match
            WHERE pid = %s
            ORDER BY test_node_id
        """, (project_id,))
        node_matches = cursor.fetchall()
        if not node_matches:
            raise _required_operation_error(
                "未找到节点匹配结果，请选择模型修正=>节点测点匹配",
                operation="完成测点与有限元节点的空间匹配",
                interface_key="match_nodes",
            )

        octree_meta = _get_latest_octree_meta(cursor, project_id)
        cache = _load_octree_cache(octree_meta["cache_file_path"])
        part_lookup = _cache_part_lookup(cache)
        node_match_lookup = {str(row["test_node_id"]): row for row in node_matches}
        modal_unv_mode = _is_modal_unv_project(int(project_id), cursor=cursor)

        if overwrite:
            log_project_info(
                int(project_id),
                "自由度匹配将覆盖旧结果，并清理响应目录与模态相关性数据",
                stage="dof_match_overwrite",
                percent=10,
            )
            cursor.execute("DELETE FROM t_mt_py_fem_dof_match WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_response_catalog WHERE pid = %s", (project_id,))
            cursor.execute("DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = %s", (project_id,))

        insert_sql = """
        INSERT INTO t_mt_py_fem_dof_match
        (pid, test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof,
         direction_x, direction_y, direction_z, match_score, transform_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            instance_name = VALUES(instance_name),
            part_name = VALUES(part_name),
            fem_node_label = VALUES(fem_node_label),
            fem_dof = VALUES(fem_dof),
            direction_x = VALUES(direction_x),
            direction_y = VALUES(direction_y),
            direction_z = VALUES(direction_z),
            match_score = VALUES(match_score),
            transform_json = VALUES(transform_json),
            created_at = CURRENT_TIMESTAMP
        """

        if modal_unv_mode:
            score_threshold = 1e-6 if min_match_score is None else float(min_match_score)
            dof_scores = _build_modal_unv_dof_amplitudes(cursor, int(project_id))
            dof_matches = []
            rejected_matches = []
            for row in node_matches:
                test_node_id = str(row["test_node_id"])
                inst_name = str(row["instance_name"] or "")
                fem_node_label = int(row["fem_node_label"])
                part_name = part_lookup.get((inst_name, fem_node_label))
                transform_payload = _json_loads(row["transform_json"]) or {}
                for test_dof, fem_dof, direction in (
                        ("UX", "U1", np.array([1.0, 0.0, 0.0], dtype=np.float64)),
                        ("UY", "U2", np.array([0.0, 1.0, 0.0], dtype=np.float64)),
                        ("UZ", "U3", np.array([0.0, 0.0, 1.0], dtype=np.float64)),
                ):
                    match_score = float(dof_scores.get((test_node_id, test_dof), 0.0))
                    if match_score < score_threshold:
                        if len(rejected_matches) < 20:
                            rejected_matches.append({
                                "test_node_id": test_node_id,
                                "test_dof": test_dof,
                                "match_score": match_score,
                            })
                        continue
                    dof_row = {
                        "test_node_id": test_node_id,
                        "test_dof": test_dof,
                        "instance_name": inst_name,
                        "part_name": part_name,
                        "fem_node_label": fem_node_label,
                        "fem_dof": fem_dof,
                        "direction": direction.tolist(),
                        "match_score": match_score,
                        "transform": transform_payload,
                        "mode": "modal_unv",
                    }
                    dof_matches.append(dof_row)
                    cursor.execute(insert_sql, (
                        project_id,
                        test_node_id,
                        test_dof,
                        inst_name,
                        part_name,
                        fem_node_label,
                        fem_dof,
                        float(direction[0]),
                        float(direction[1]),
                        float(direction[2]),
                        match_score,
                        _json_dumps(transform_payload),
                    ))
            conn.commit()
            log_project_step(
                int(project_id),
                f"自由度匹配完成，已生成 {len(dof_matches)} 个自由度映射",
                stage="dof_match_finished",
                percent=100,
            )
            return {
                "project_id": project_id,
                "node_match_auto_created": auto_created_node_match,
                "node_match_count": len(node_matches),
                "channel_count": 0,
                "dof_match_count": len(dof_matches),
                "dof_matches_preview": dof_matches[:20],
                "rejected_dof_count": int(len(node_matches) * len(TEST_DOF_SEQUENCE) - len(dof_matches)),
                "rejected_dof_preview": rejected_matches,
                "min_match_score": score_threshold,
                "match_mode": "modal_unv",
            }

        cursor.execute("""
            SELECT id, measuring_point_name, sensor_type_id
            FROM t_mt_measuring_point_info
            WHERE project_id = %s
            ORDER BY id, measuring_point_name
        """, (project_id,))
        measuring_rows = cursor.fetchall() or []
        displacement_sensors = [
            row for row in measuring_rows
            if _is_displacement_static_test_sensor_type(row.get("sensor_type_id"))
        ]
        if not displacement_sensors:
            raise ValueError("未找到位移传感器测点")

        missing_node_matches = [
            str(row["measuring_point_name"])
            for row in displacement_sensors
            if str(row.get("measuring_point_name") or "") not in node_match_lookup
        ]
        if missing_node_matches:
            raise ValidationError(
                "存在位移传感器尚未完成节点匹配，无法进行自由度匹配",
                {
                    "missing_measuring_points": missing_node_matches[:20],
                    "missing_count": len(missing_node_matches),
                },
            )

        displacement_sensor_by_id = {
            int(row["id"]): row
            for row in displacement_sensors
            if row.get("id") is not None
        }

        cursor.execute("""
            SELECT id, measure_point_id, direction, data_operate
            FROM t_mt_channel_info
            WHERE project_id = %s
            ORDER BY measure_point_id, id
        """, (project_id,))
        channel_rows = cursor.fetchall() or []
        displacement_channels = [
            row for row in channel_rows
            if row.get("measure_point_id") is not None
               and int(row["measure_point_id"]) in displacement_sensor_by_id
        ]
        if not displacement_channels:
            raise ValueError("未找到位移传感器对应的通道方向配置")

        dof_matches_by_key = {}
        for channel_row in displacement_channels:
            measure_point_id = int(channel_row["measure_point_id"])
            measuring_row = displacement_sensor_by_id[measure_point_id]
            test_node_id = str(measuring_row["measuring_point_name"])
            node_match = node_match_lookup[test_node_id]
            inst_name = str(node_match["instance_name"])
            fem_node_label = int(node_match["fem_node_label"])
            part_name = part_lookup.get((inst_name, fem_node_label))
            transform_payload = _json_loads(node_match["transform_json"]) or {}
            test_dof, fem_dof, direction = _resolve_channel_direction_vector(
                channel_row.get("direction"),
                channel_row.get("data_operate"),
                measuring_point_name=test_node_id,
                channel_id=channel_row.get("id"),
            )

            dof_row = {
                "measure_point_id": measure_point_id,
                "channel_id": channel_row.get("id"),
                "test_node_id": test_node_id,
                "test_dof": test_dof,
                "instance_name": inst_name,
                "part_name": part_name,
                "fem_node_label": fem_node_label,
                "fem_dof": fem_dof,
                "direction": direction.tolist(),
                "match_score": None,
                "transform": transform_payload,
            }
            dof_matches_by_key[(test_node_id, test_dof)] = dof_row
            cursor.execute(insert_sql, (
                project_id,
                test_node_id,
                test_dof,
                inst_name,
                part_name,
                fem_node_label,
                fem_dof,
                float(direction[0]),
                float(direction[1]),
                float(direction[2]),
                1.0,
                _json_dumps(transform_payload),
            ))

        conn.commit()
        dof_matches = list(dof_matches_by_key.values())
        log_project_step(
            int(project_id),
            f"自由度匹配完成，位移测点 {len(displacement_sensors)} 个，通道 {len(displacement_channels)} 个，匹配 {len(dof_matches)} 个自由度",
            stage="dof_match_finished",
            percent=100,
        )
        return {
            "project_id": project_id,
            "node_match_auto_created": auto_created_node_match,
            "node_match_count": len(node_matches),
            "displacement_sensor_count": len(displacement_sensors),
            "channel_count": len(displacement_channels),
            "dof_match_count": len(dof_matches),
            "dof_matches_preview": dof_matches[:20],
        }
    except Exception as exc:
        conn.rollback()
        log_project_error(
            int(project_id),
            f"自由度匹配失败: {exc}",
            stage="failed",
        )
        raise
    finally:
        cursor.close()
        conn.close()


def get_dof_matches(project_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT test_node_id, test_dof, instance_name, part_name, fem_node_label, fem_dof,
                   direction_x, direction_y, direction_z, match_score, transform_json, created_at
            FROM t_mt_py_fem_dof_match
            WHERE pid = %s
            ORDER BY test_node_id, test_dof
        """, (project_id,))
        return {
            "project_id": project_id,
            "dof_matches": cursor.fetchall(),
        }
    finally:
        cursor.close()
        conn.close()
