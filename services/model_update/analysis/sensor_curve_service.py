"""Measured curves from the dedicated load-level test-data table."""
import math
import json
from decimal import Decimal

from db import get_connection
from src.l3.core.errors import ValidationError


def get_sensor_curve(project_id, sensor):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT load_level, sample_index, reading_value, unit
            FROM t_mt_py_test_load_level_data
            WHERE project_id = %s AND sensor = %s
            ORDER BY CASE WHEN sample_index IS NULL THEN load_level ELSE sample_index END, id""", (project_id, sensor))
        rows = cursor.fetchall()
        axis_label, axis = None, None
        if any(row.get("sample_index") is not None for row in rows):
            if any(row.get("sample_index") is None for row in rows):
                raise ValidationError("该测点混有 CSV 和 TXT 记录，请重新导入")
            cursor.execute("SELECT extra_json FROM t_mt_py_project_config WHERE pid=%s", (project_id,))
            config = cursor.fetchone()
            extra = config["extra_json"] if config else {}
            extra = json.loads(extra) if isinstance(extra, str) else (extra or {})
            metadata = extra.get("test_load_level_import", {})
            axis_label = metadata.get("axis_label")
            axis = next((item["value"] for item in metadata.get("axis_rows", []) if item["label"] == axis_label), None)
            if axis is None or [row["sample_index"] for row in rows] != list(range(len(axis))):
                raise ValidationError("试验记录与横轴元数据不一致，请重新导入")
        units = {row["unit"] for row in rows if row["unit"]}
        if len(units) > 1:
            raise ValidationError("同一传感器的加载级数据存在不同单位，请统一后查询")
        points = []
        for index, row in enumerate(rows):
            level = format(Decimal(str(axis[index] if axis is not None else row["load_level"])), "f")
            if "." in level:
                level = level.rstrip("0").rstrip(".")
            value = None if row["reading_value"] is None else float(row["reading_value"])
            if value is not None and not math.isfinite(value):
                value = None
            points.append([level, value])
        curves = [{"label": sensor, "data": points, "xaxis": [point[0] for point in points]}] if points else []
        return {"project_id": project_id, "sensor": sensor, "curve_type": "sensor",
                "data": curves, "unit": next(iter(units), None), "axis_label": axis_label,
                "source": "t_mt_py_test_load_level_data",
                "summary": {"curve_count": len(curves), "point_count": len(points)}}
    finally:
        cursor.close()
        connection.close()
