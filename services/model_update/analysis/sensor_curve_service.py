"""Measured curves from the dedicated load-level test-data table."""
import math
from decimal import Decimal

from db import get_connection
from src.l3.core.errors import ValidationError


def get_sensor_curve(project_id, sensor):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT load_level, reading_value, unit
            FROM t_mt_py_test_load_level_data
            WHERE project_id = %s AND sensor = %s
            ORDER BY load_level, id""", (project_id, sensor))
        rows = cursor.fetchall()
        units = {row["unit"] for row in rows if row["unit"]}
        if len(units) > 1:
            raise ValidationError("同一传感器的加载级数据存在不同单位，请统一后查询")
        points = []
        for row in rows:
            level = format(Decimal(str(row["load_level"])), "f")
            if "." in level:
                level = level.rstrip("0").rstrip(".")
            value = None if row["reading_value"] is None else float(row["reading_value"])
            if value is not None and not math.isfinite(value):
                value = None
            points.append([level, value])
        curves = [{"label": sensor, "data": points, "xaxis": [point[0] for point in points]}] if points else []
        return {"project_id": project_id, "sensor": sensor, "curve_type": "sensor",
                "data": curves, "unit": next(iter(units), None),
                "source": "t_mt_py_test_load_level_data",
                "summary": {"curve_count": len(curves), "point_count": len(points)}}
    finally:
        cursor.close()
        connection.close()
