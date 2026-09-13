#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a deterministic snapshot from the MySQL business database.

This is a side-car diagnostic script. It does not import or modify the app's
runtime db.py connection pool.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import decimal
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

import mysql.connector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
DEFAULT_OUT = TOOLS_ROOT / "db_compare" / "mysql_snapshot.json"


def _load_service_config() -> dict[str, Any]:
    path = os.environ.get("CONFIG_FILE") or PROJECT_ROOT / "service_config.json"
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _cfg(config: dict[str, Any], key: str, default: Any) -> Any:
    return os.environ.get(key, config.get(key, default))


def _tables_from_db_py() -> list[str]:
    db_py = PROJECT_ROOT / "db.py"
    text = db_py.read_text(encoding="utf-8")
    module = ast.parse(text)
    sql_items: list[str] = []
    for node in module.body:
        for target in getattr(node, "targets", []):
            if getattr(target, "id", None) == "CREATE_TABLE_SQL_LIST":
                sql_items.extend(ast.literal_eval(node.value))
    tables: list[str] = []
    for sql in sql_items:
        match = re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([`\w]+)",
            sql,
            re.IGNORECASE,
        )
        if match:
            tables.append(match.group(1).strip("`"))
    return tables


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value.normalize())
    if isinstance(value, bytes):
        return {"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "size": len(value)}
    return value


def _row_digest(row: Iterable[Any]) -> str:
    payload = json.dumps(
        [_normalize_value(item) for item in row],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _combine_row_hashes(rows: Iterable[Iterable[Any]]) -> str:
    mask = (1 << 256) - 1
    xor_value = 0
    sum_value = 0
    count = 0
    for row in rows:
        value = int(_row_digest(row), 16)
        xor_value ^= value
        sum_value = (sum_value + value) & mask
        count += 1
    payload = f"{count}:{xor_value:064x}:{sum_value:064x}"
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _snapshot_table(conn, database: str, table: str) -> dict[str, Any]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT,
                   COLUMN_KEY, EXTRA, ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (database, table),
        )
        columns = cursor.fetchall()
        if not columns:
            return {"exists": False}

        column_names = [str(row[0]) for row in columns]
        quoted_cols = ", ".join("`{}`".format(name.replace("`", "``")) for name in column_names)

        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
        row_count = int(cursor.fetchone()[0])

        cursor.execute(f"SELECT {quoted_cols} FROM `{table}`")
        row_hash = _combine_row_hashes(cursor)

        schema_payload = [
            {
                "name": str(name).lower(),
                "type": str(data_type).lower(),
                "nullable": str(nullable).upper(),
                "default": None if default is None else str(default),
                "key": "" if key is None else str(key),
                "extra": "" if extra is None else str(extra).lower(),
                "ordinal": int(ordinal),
            }
            for name, data_type, nullable, default, key, extra, ordinal in columns
        ]
        return {
            "exists": True,
            "columns": schema_payload,
            "row_count": row_count,
            "row_hash": row_hash,
        }
    finally:
        cursor.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump MySQL table snapshots for DM comparison.")
    config = _load_service_config()
    parser.add_argument("--host", default=_cfg(config, "DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(_cfg(config, "DB_PORT", 3306)))
    parser.add_argument("--user", default=_cfg(config, "DB_USER", "root"))
    parser.add_argument("--password", default=_cfg(config, "DB_PASSWORD", ""))
    parser.add_argument("--database", default=_cfg(config, "DB_DATABASE", "db_simu_real_test"))
    parser.add_argument("--charset", default=_cfg(config, "DB_CHARSET", "utf8mb4"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--tables", nargs="*", default=None, help="Optional explicit table list.")
    args = parser.parse_args()

    tables = args.tables or _tables_from_db_py()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = mysql.connector.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset=args.charset,
        use_pure=True,
    )
    try:
        snapshot = {
            "dialect": "mysql",
            "database": args.database,
            "host": args.host,
            "port": args.port,
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "tables": {},
        }
        for table in tables:
            snapshot["tables"][table] = _snapshot_table(conn, args.database, table)
    finally:
        conn.close()

    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"MySQL snapshot written: {out_path}")
    print(f"Tables checked: {len(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
