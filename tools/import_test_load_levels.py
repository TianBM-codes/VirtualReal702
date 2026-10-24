"""Import measured load-level readings from CSV into the configured MySQL DB.

python tools/import_test_load_levels.py --project-id 2026101601 --csv readings.csv
Optional scope: --work-condition-id condition-001 --measurement-id measurement-001
CSV columns: sensor,load_level,value; optional: unit,remark.
Blank value cells are stored as SQL NULL. Existing levels are rejected unless
--update-existing is explicitly supplied. All readings commit in one transaction.
"""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import ensure_test_load_level_schema, get_connection
from services.model_update.analysis.load_level_scope import scope_ids


def read_rows(path):
    rows, seen = [], set()
    with open(path, "r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"sensor", "load_level", "value"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV requires sensor,load_level,value columns")
        for line, row in enumerate(reader, 2):
            sensor = (row.get("sensor") or "").strip()
            unit = (row.get("unit") or "").strip() or None
            remark = (row.get("remark") or "").strip() or None
            try:
                level = Decimal((row.get("load_level") or "").strip())
                if not level.is_finite() or level < 0 or level >= Decimal("1000000000000") or level != level.quantize(Decimal(".00000001")):
                    raise ValueError("load_level must be nonnegative with at most 8 decimal places")
                raw = (row.get("value") or "").strip()
                value = float(raw) if raw else None
                if value is not None and not math.isfinite(value):
                    raise ValueError("value must be finite or blank")
            except (ValueError, InvalidOperation) as exc:
                raise ValueError("CSV line %s: %s" % (line, exc)) from exc
            if not sensor or len(sensor) > 100 or (unit and len(unit) > 32) or (remark and len(remark) > 255):
                raise ValueError("CSV line %s: invalid sensor/unit/remark length" % line)
            key = sensor, level
            if key in seen:
                raise ValueError("CSV line %s: duplicate sensor/load_level" % line)
            seen.add(key)
            rows.append((sensor, level, value, unit, remark))
    if not rows:
        raise ValueError("CSV contains no readings")
    units = {}
    for sensor, _, _, unit, _ in rows:
        if unit:
            units.setdefault(sensor, set()).add(unit)
    if any(len(values) > 1 for values in units.values()):
        raise ValueError("Each sensor must use one unit")
    return rows


def store_rows(project_id, rows, update_existing=False, work_condition_id=None, measurement_id=None):
    condition, measurement = scope_ids(work_condition_id, measurement_id)
    if project_id <= 0 or project_id > 9223372036854775807:
        raise ValueError("project_id must be a positive BIGINT")
    connection = get_connection()
    cursor = connection.cursor()
    try:
        ensure_test_load_level_schema(cursor)
        connection.commit()
        sql = """INSERT INTO t_mt_py_test_load_level_data
            (project_id, sensor, load_level, reading_value, unit, remark, work_condition_id, measurement_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"""
        if update_existing:
            sql += " ON DUPLICATE KEY UPDATE reading_value=VALUES(reading_value), unit=VALUES(unit), remark=VALUES(remark)"
        for row in rows:
            cursor.execute(sql, (project_id, *row, condition, measurement))
        connection.commit()
        return {"project_id": project_id, "work_condition_id": condition, "measurement_id": measurement,
                "row_count": len(rows), "sensor_count": len({row[0] for row in rows})}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--work-condition-id")
    parser.add_argument("--measurement-id")
    parser.add_argument("--update-existing", action="store_true")
    args = parser.parse_args()
    try:
        result = store_rows(args.project_id, read_rows(args.csv), args.update_existing,
                            args.work_condition_id, args.measurement_id)
    except Exception as exc:
        parser.exit(1, "Import failed: %s\n" % exc)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
