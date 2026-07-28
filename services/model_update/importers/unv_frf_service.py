import json
import re

import numpy as np

from db import ensure_tables_exist, get_connection
from src.l3.core.errors import NotFoundError, ValidationError
from services.model_update.importers.unv_utils import (
    list_unv_dataset_ids,
    read_unv_blocks as _read_unv_blocks,
)


def _to_float(text: str) -> float:
    return float(str(text).replace("D", "E").replace("d", "E"))


def read_unv_blocks(filename):
    return _read_unv_blocks(filename)


def list_dataset_ids(filename) -> list[str]:
    return list_unv_dataset_ids(filename)


def dof_label(node, direction):
    sign = "+" if direction >= 0 else "-"
    comp_map = {
        1: "UX",
        2: "UY",
        3: "UZ",
        4: "RX",
        5: "RY",
        6: "RZ",
    }
    comp = comp_map.get(abs(direction), f"DOF{direction}")
    return f"{sign}{node}{comp}"


def femtools_like_name(index, response_node, response_dir, reference_node, reference_dir):
    rsp = dof_label(response_node, response_dir)
    ref = dof_label(reference_node, reference_dir)
    return f"TEST FRF {index} ({rsp} : {ref})"


def frf_type_text(y_type, denominator_type):
    y_type = int(y_type)
    denominator_type = int(denominator_type)

    if y_type == 12 and denominator_type == 13:
        return "ACCELERANCE (A/F)"
    if y_type == 14 and denominator_type == 13:
        return "MOBILITY (V/F)"
    if y_type in (11, 17) and denominator_type == 13:
        return "RECEPTANCE (D/F)"
    return f"TYPE {y_type}/{denominator_type}"


def parse_unv58(filename):
    curves = []
    frf_index = 0

    for dataset_id, records in read_unv_blocks(filename):
        if dataset_id != "58":
            continue
        if len(records) < 11:
            raise ValidationError("dataset 58 record count is incomplete", {"file_path": filename})

        r6 = records[5].split()
        r7 = records[6].split()
        r8 = records[7].split()
        r9 = records[8].split()
        r10 = records[9].split()
        r11 = records[10].split()

        ordinate_type = int(r7[0])
        n_points = int(r7[1])
        abscissa_spacing = int(r7[2])
        x_min = _to_float(r7[3])
        x_inc = _to_float(r7[4])

        values = []
        for line in records[11:]:
            for item in line.split():
                values.append(_to_float(item))
        values = np.asarray(values, dtype=float)

        if abscissa_spacing == 0:
            if ordinate_type in (5, 6):
                data = values.reshape(-1, 3)
                freq = data[:, 0]
                real = data[:, 1]
                imag = data[:, 2]
            elif ordinate_type in (2, 4):
                data = values.reshape(-1, 2)
                freq = data[:, 0]
                real = data[:, 1]
                imag = np.zeros_like(real)
            else:
                raise ValidationError("unsupported dataset 58 ordinate_type", {"ordinate_type": ordinate_type})
        else:
            freq = x_min + x_inc * np.arange(n_points)
            if ordinate_type in (5, 6):
                data = values.reshape(-1, 2)
                real = data[:, 0]
                imag = data[:, 1]
            elif ordinate_type in (2, 4):
                real = values[:n_points]
                imag = np.zeros_like(real)
            else:
                raise ValidationError("unsupported dataset 58 ordinate_type", {"ordinate_type": ordinate_type})

        if len(freq) != n_points:
            raise ValidationError(
                "dataset 58 point count mismatch",
                {"header_points": n_points, "parsed_points": len(freq)},
            )

        h = real + 1j * imag

        response_node = int(r6[5])
        response_dir = int(r6[6])
        reference_node = int(r6[8])
        reference_dir = int(r6[9])

        x_type = int(r8[0])
        y_type = int(r9[0])
        denominator_type = int(r10[0])
        z_type = int(r11[0])

        frf_index += 1
        title = records[0].strip() or "NONE"

        curves.append(
            {
                "name": femtools_like_name(
                    frf_index,
                    response_node,
                    response_dir,
                    reference_node,
                    reference_dir,
                ),
                "freq": freq,
                "real": real,
                "imag": imag,
                "complex": h,
                "meta": {
                    "index": frf_index,
                    "ordinate_type": ordinate_type,
                    "n_points": n_points,
                    "abscissa_spacing": abscissa_spacing,
                    "response_node": response_node,
                    "response_dir": response_dir,
                    "reference_node": reference_node,
                    "reference_dir": reference_dir,
                    "x_type": x_type,
                    "y_type": y_type,
                    "denominator_type": denominator_type,
                    "z_type": z_type,
                    "type": frf_type_text(y_type, denominator_type),
                    "title": title,
                },
            }
        )

    return curves


def import_unv_frf_data(file_path: str, project_id: int) -> dict:
    ensure_tables_exist()
    curves = parse_unv58(file_path)
    if not curves:
        return {
            "project_id": int(project_id),
            "frf_curve_count": 0,
            "frf_point_count": 0,
            "curve_names": [],
        }

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        point_count = 0
        curve_names = []
        select_sql = """
        SELECT id
        FROM t_mt_py_test_frf_curve
        WHERE pid = %s AND curve_name = %s
        LIMIT 1
        """
        insert_curve_sql = """
        INSERT INTO t_mt_py_test_frf_curve (
            pid, curve_name, curve_no, title, frf_type, response_node, response_dir,
            reference_node, reference_dir, x_type, y_type, denominator_type, z_type,
            ordinate_type, abscissa_spacing, n_points, source_file, extra_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        update_curve_sql = """
        UPDATE t_mt_py_test_frf_curve
        SET curve_no = %s,
            title = %s,
            frf_type = %s,
            response_node = %s,
            response_dir = %s,
            reference_node = %s,
            reference_dir = %s,
            x_type = %s,
            y_type = %s,
            denominator_type = %s,
            z_type = %s,
            ordinate_type = %s,
            abscissa_spacing = %s,
            n_points = %s,
            source_file = %s,
            extra_json = %s
        WHERE id = %s
        """
        insert_point_sql = """
        INSERT INTO t_mt_py_test_frf_point (pid, curve_id, point_no, frequency, real_value, imag_value)
        VALUES (%s, %s, %s, %s, %s, %s)
        """

        seen_names = set()
        for curve in curves:
            name = str(curve["name"])
            curve_names.append(name)
            meta = dict(curve.get("meta") or {})
            extra_json = json.dumps(meta, ensure_ascii=False)

            cursor.execute(select_sql, (int(project_id), name))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    insert_curve_sql,
                    (
                        int(project_id),
                        name,
                        int(meta["index"]),
                        meta.get("title"),
                        meta.get("type"),
                        meta.get("response_node"),
                        meta.get("response_dir"),
                        meta.get("reference_node"),
                        meta.get("reference_dir"),
                        meta.get("x_type"),
                        meta.get("y_type"),
                        meta.get("denominator_type"),
                        meta.get("z_type"),
                        meta.get("ordinate_type"),
                        meta.get("abscissa_spacing"),
                        int(meta.get("n_points") or 0),
                        str(file_path),
                        extra_json,
                    ),
                )
                curve_id = int(cursor.lastrowid)
            else:
                curve_id = int(row["id"])
                cursor.execute(
                    update_curve_sql,
                    (
                        int(meta["index"]),
                        meta.get("title"),
                        meta.get("type"),
                        meta.get("response_node"),
                        meta.get("response_dir"),
                        meta.get("reference_node"),
                        meta.get("reference_dir"),
                        meta.get("x_type"),
                        meta.get("y_type"),
                        meta.get("denominator_type"),
                        meta.get("z_type"),
                        meta.get("ordinate_type"),
                        meta.get("abscissa_spacing"),
                        int(meta.get("n_points") or 0),
                        str(file_path),
                        extra_json,
                        curve_id,
                    ),
                )

            if name in seen_names:
                cursor.execute("DELETE FROM t_mt_py_test_frf_point WHERE pid = %s AND curve_id = %s", (int(project_id), curve_id))
            else:
                seen_names.add(name)
                cursor.execute("DELETE FROM t_mt_py_test_frf_point WHERE pid = %s AND curve_id = %s", (int(project_id), curve_id))

            for point_no, (freq, real, imag) in enumerate(
                zip(curve["freq"], curve["real"], curve["imag"]),
                start=1,
            ):
                cursor.execute(
                    insert_point_sql,
                    (
                        int(project_id),
                        curve_id,
                        int(point_no),
                        float(freq),
                        float(real),
                        float(imag),
                    ),
                )
                point_count += 1

        conn.commit()
        return {
            "project_id": int(project_id),
            "frf_curve_count": len(curves),
            "frf_point_count": point_count,
            "curve_names": curve_names,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_project_frf_names(project_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT curve_name
            FROM t_mt_py_test_frf_curve
            WHERE pid = %s
            ORDER BY curve_no, curve_name
            """,
            (int(project_id),),
        )
        names = [str(row["curve_name"]) for row in (cursor.fetchall() or [])]
        return {"project_id": int(project_id), "names": names}
    finally:
        cursor.close()
        conn.close()


def _normalize_curve_names(name=None, names=None) -> list[str]:
    merged = []
    if name is not None and str(name).strip():
        merged.append(str(name).strip())
    for item in list(names or []):
        text = str(item).strip()
        if text:
            merged.append(text)
    deduped = []
    seen = set()
    for item in merged:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _display_line_name(curve_name: str) -> str:
    text = str(curve_name or "").strip()
    match = re.match(r"^(?:TEST\s+)?(FRF\s+\d+)\b", text, flags=re.IGNORECASE)
    if match:
        token = match.group(1)
        parts = token.split()
        if len(parts) == 2:
            return f"{parts[0].upper()} {parts[1]}"
    return text


def _sig5(value: float) -> float:
    return float(f"{float(value):.5g}")


def _build_curve_line_payload(rows: list[dict], resolved_index: int) -> dict:
    x_values = []
    series = []
    for row in rows:
        freq = float(row["frequency"])
        real_value = float(row["real_value"])
        imag_value = float(row["imag_value"])
        h = complex(real_value, imag_value)
        if resolved_index == 1:
            value = h.real
        elif resolved_index == 2:
            value = h.imag
        elif resolved_index == 3:
            value = float(np.abs(h))
        else:
            value = float(np.angle(h, deg=True))
        rounded_freq = _sig5(freq)
        rounded_value = _sig5(value)
        x_values.append(rounded_freq)
        series.append([rounded_freq, rounded_value])
    return {"x": x_values, "series": series}


def get_project_frf_curve(project_id: int, name: str = None, index: int = 1, names=None) -> dict:
    requested_names = _normalize_curve_names(name=name, names=names)
    if not requested_names:
        raise ValidationError("frf curve name is required", {"name": name, "names": names})
    resolved_index = int(index)
    if resolved_index not in (1, 2, 3, 4):
        raise ValidationError("unsupported frf curve index", {"index": index, "allowed": [1, 2, 3, 4]})
    y_axis_map = {
        1: "Real",
        2: "Imaginary",
        3: "Magnitude",
        4: "Phase",
    }

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT curve_name
            FROM t_mt_py_test_frf_curve
            WHERE pid = %s
            ORDER BY curve_no, curve_name
            """,
            (int(project_id),),
        )
        names = [str(row["curve_name"]) for row in (cursor.fetchall() or [])]
        lines = []
        missing_names = []
        for curve_name in requested_names:
            cursor.execute(
                """
                SELECT id, curve_name
                FROM t_mt_py_test_frf_curve
                WHERE pid = %s AND curve_name = %s
                LIMIT 1
                """,
                (int(project_id), curve_name),
            )
            curve_row = cursor.fetchone()
            if curve_row is None:
                missing_names.append(curve_name)
                continue

            cursor.execute(
                """
                SELECT frequency, real_value, imag_value
                FROM t_mt_py_test_frf_point
                WHERE pid = %s AND curve_id = %s
                ORDER BY point_no
                """,
                (int(project_id), int(curve_row["id"])),
            )
            rows = cursor.fetchall() or []
            if not rows:
                raise NotFoundError(
                    "FRF curve points were not found",
                    {"project_id": int(project_id), "name": curve_name},
                )
            line_payload = _build_curve_line_payload(rows, resolved_index)
            lines.append(
                {
                    "line_name": _display_line_name(curve_row["curve_name"]),
                    "x": line_payload["x"],
                    "series": line_payload["series"],
                }
            )

        if missing_names:
            raise NotFoundError(
                "some FRF curves were not found",
                {"project_id": int(project_id), "missing_names": missing_names},
            )
        if not lines:
            raise NotFoundError(
                "FRF curve was not found",
                {"project_id": int(project_id), "requested_names": requested_names},
            )

        return {
            "project_id": int(project_id),
            "names": names,
            "x_name": "Frequency[Hz]",
            "y_name": y_axis_map[resolved_index],
            "line_name": str(lines[0]["line_name"]),
            "x": list(lines[0]["x"]),
            "series": list(lines[0]["series"]),
            "line_names": [str(item["line_name"]) for item in lines],
            "lines": lines,
        }
    finally:
        cursor.close()
        conn.close()
