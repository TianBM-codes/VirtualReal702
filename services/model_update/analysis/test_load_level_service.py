"""Download, parse and atomically replace a project's fixed-width test data."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from pathlib import Path
from uuid import uuid4

from db import ensure_test_load_level_schema, get_connection
from src.l3.core.errors import NotFoundError, ValidationError
from src.utils.file_fetch import materialize_source_file
from tools.DumpTestDataToDB import parse_txt
from .project_path_service import resolve_project_workspace
from .project_config_service import upsert_project_config
from .load_level_scope import scope_ids, import_metadata


def decode_json(value):
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else (value or {})


def prepare_import(local_path, source, axis_label=None, unit=None, ignore_last_reading=True):
    try:
        parsed = parse_txt(local_path)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError("试验 TXT 解析失败", {"reason": str(exc)}) from exc
    if not parsed.ax_infors or not parsed.gauge_data:
        raise ValidationError("试验文件缺少加载信息或测点读数")
    if unit is None:
        header = parsed.page_headers[0][0] if parsed.page_headers else ""
        match = re.search(r"单位\s*[:：]\s*([^）)]+)", header)
        if match:
            unit = match.group(1).strip()
            if len(unit) > 32:
                raise ValidationError("文件中的单位字段过长，请显式指定 unit")
    # This format repeats axes on pages containing different sensors. Reject
    # inconsistent page axes instead of joining independent loading histories.
    if any(page != parsed.ax_infors for page in parsed.page_axes):
        raise ValidationError("各页加载信息不一致，无法按列对应测点")
    axes = [{"label": name, "value": values} for name, values in parsed.ax_infors.items()]
    count = len(axes[0]["value"])
    if not count or any(len(row["value"]) != count for row in axes):
        raise ValidationError("各行加载信息的列数不一致")
    if axis_label is None:
        axis_label = axes[0]["label"]
    if axis_label not in parsed.ax_infors:
        raise ValidationError("文件中不存在所选横轴", {"axis_label": axis_label})
    if any(not math.isfinite(value) for row in axes for value in row["value"]):
        raise ValidationError("加载信息包含非有限数值")
    levels = parsed.ax_infors[axis_label]
    try:
        if any(abs(Decimal(str(value))) >= Decimal("1000000000000") or
               Decimal(str(value)) != Decimal(str(value)).quantize(Decimal(".00000001")) for value in levels):
            raise ValueError("横轴值超出 load_level 的数值精度")
    except (ValueError, InvalidOperation) as exc:
        raise ValidationError("所选横轴超出数据库数值精度") from exc
    ignored, rows = {}, []
    for name, raw in parsed.gauge_data.items():
        if len(name) > 100 or not all(math.isfinite(value) for value in raw):
            raise ValidationError("测点名称过长或读数包含非有限数值", {"sensor": name})
        values = raw[:-1] if ignore_last_reading else raw
        if len(values) != count:
            raise ValidationError("测点读数与加载信息长度不一致", {
                "sensor": name, "raw_count": len(raw), "axis_count": count,
                "ignore_last_reading": ignore_last_reading})
        if ignore_last_reading:
            ignored[name] = raw[-1]
        rows.extend((name, index, Decimal(str(levels[index])), value, unit)
                    for index, value in enumerate(values))
    metadata = {"source_url_or_path": source, "local_path": str(local_path),
                "sha256": hashlib.sha256(Path(local_path).read_bytes()).hexdigest(),
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "axis_label": axis_label, "axis_rows": axes,
                "sensor_names": list(parsed.gauge_data), "sample_count": count,
                "unit": unit, "table_count": parsed.table_count, "page_records": parsed.page_records,
                "start_time": parsed.start_time,
                "start_datetime": parsed.start_datetime.isoformat() if parsed.start_datetime else None,
                "page_headers": parsed.page_headers, "page_axes": parsed.page_axes,
                "ignore_last_reading": ignore_last_reading, "ignored_trailing_values": ignored,
                "except_gauges": parsed.except_gauges, "broken_gauge": parsed.broken_gauge}
    # frequency=40 is a parser default, not information read from this TXT.
    return metadata, rows


def import_test_load_level(project_id, file_path, axis_label=None, unit=None, ignore_last_reading=True,
                           work_condition_id=None, measurement_id=None):
    condition, measurement = scope_ids(work_condition_id, measurement_id)
    folder = Path(resolve_project_workspace(project_id)) / "test_data" / "load_level" / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    local_path = materialize_source_file(file_path, str(folder), dest_name="source.txt")
    metadata, rows = prepare_import(local_path, file_path, axis_label, unit, ignore_last_reading)
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        ensure_test_load_level_schema(cursor)
        connection.commit()  # MySQL DDL is outside the replacement transaction.
        cursor.execute("INSERT INTO t_mt_py_project_config (pid) VALUES (%s) ON DUPLICATE KEY UPDATE pid=VALUES(pid)",
                       (project_id,))
        cursor.execute("SELECT pid,extra_json FROM t_mt_py_project_config WHERE pid=%s FOR UPDATE", (project_id,))
        existing = cursor.fetchall()
        extra = decode_json(existing[0].get("extra_json")) if existing else {}
        cursor.execute("""DELETE FROM t_mt_py_test_load_level_data
            WHERE project_id=%s AND work_condition_id=%s AND measurement_id=%s""", (project_id, condition, measurement))
        cursor.executemany("""INSERT INTO t_mt_py_test_load_level_data
            (project_id,sensor,sample_index,load_level,reading_value,unit,work_condition_id,measurement_id,remark)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'TXT import')""", [(project_id, *row, condition, measurement) for row in rows])
        if condition or measurement:
            scopes = extra.get("test_load_level_imports", {})
            scopes.setdefault(condition, {})[measurement] = metadata
            update = {"test_load_level_imports": scopes}
        else:
            update = {"test_load_level_import": metadata}
        upsert_project_config(project_id, extra_json=update, cursor=cursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
    return {"project_id": project_id, "sensor_count": len(metadata["sensor_names"]),
            "work_condition_id": condition, "measurement_id": measurement,
            "sample_count": metadata["sample_count"], "row_count": len(rows), "metadata": metadata}


def load_test_data(project_id, work_condition_id=None, measurement_id=None):
    condition, measurement = scope_ids(work_condition_id, measurement_id)
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT extra_json FROM t_mt_py_project_config WHERE pid=%s", (project_id,))
        row = cursor.fetchone()
        metadata = import_metadata(decode_json(row["extra_json"]), condition, measurement) if row else None
        if not metadata:
            raise NotFoundError("工程尚未导入试验 TXT", {"project_id": project_id})
        cursor.execute("""SELECT sensor,sample_index,reading_value FROM t_mt_py_test_load_level_data
            WHERE project_id=%s AND work_condition_id=%s AND measurement_id=%s
            ORDER BY sensor,sample_index,id""", (project_id, condition, measurement))
        readings = {name: [None] * metadata["sample_count"] for name in metadata["sensor_names"]}
        seen = set()
        for record in cursor.fetchall():
            key = record["sensor"], record["sample_index"]
            if key in seen or key[0] not in readings or key[1] is None or not 0 <= key[1] < metadata["sample_count"]:
                raise ValidationError("试验读数与导入元数据不一致，请重新导入 TXT")
            seen.add(key)
            readings[key[0]][key[1]] = record["reading_value"]
        if len(seen) != len(readings) * metadata["sample_count"]:
            raise ValidationError("试验数据记录不完整，请重新导入 TXT")
        return metadata, readings
    finally:
        cursor.close()
        connection.close()
