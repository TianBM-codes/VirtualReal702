#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a deterministic snapshot from a Dameng database and compare it.

Connection priority:
1. dmPython, if installed.
2. pyodbc, if installed and --odbc-dsn is provided.

The script is intentionally independent from the main application.
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
DEFAULT_OUT = TOOLS_ROOT / "db_compare" / "dm_snapshot.json"


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


def _combine_row_hashes(cursor) -> str:
    mask = (1 << 256) - 1
    xor_value = 0
    sum_value = 0
    count = 0
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        for row in rows:
            value = int(_row_digest(row), 16)
            xor_value ^= value
            sum_value = (sum_value + value) & mask
            count += 1
    payload = f"{count}:{xor_value:064x}:{sum_value:064x}"
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _connect(args):
    if args.odbc_dsn:
        try:
            import pyodbc  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyodbc is not installed, cannot use --odbc-dsn") from exc
        return pyodbc.connect(
            f"DSN={args.odbc_dsn};UID={args.user};PWD={args.password}",
            autocommit=False,
        )

    try:
        import dmPython  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "dmPython is not installed. Install the Dameng Python driver, "
            "or install pyodbc and pass --odbc-dsn."
        ) from exc
    return dmPython.connect(
        user=args.user,
        password=args.password,
        server=args.host,
        port=args.port,
    )


def _fetchall(cursor, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    cursor.execute(sql, params)
    rows = cursor.fetchall() or []
    return [tuple(row) for row in rows]


def _resolve_dm_table(cursor, schema: str, table: str) -> tuple[str, str] | None:
    rows = _fetchall(
        cursor,
        """
        SELECT OWNER, TABLE_NAME
        FROM ALL_TABLES
        WHERE UPPER(OWNER) = UPPER(?) AND UPPER(TABLE_NAME) = UPPER(?)
        """,
        (schema, table),
    )
    if not rows:
        return None
    return str(rows[0][0]), str(rows[0][1])


def _snapshot_table(conn, schema: str, table: str) -> dict[str, Any]:
    cursor = conn.cursor()
    try:
        resolved = _resolve_dm_table(cursor, schema, table)
        if resolved is None:
            return {"exists": False}
        schema_name, table_name = resolved
        columns = _fetchall(
            cursor,
            """
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT, COLUMN_ID
            FROM ALL_TAB_COLUMNS
            WHERE OWNER = ? AND TABLE_NAME = ?
            ORDER BY COLUMN_ID
            """,
            (schema_name, table_name),
        )
        if not columns:
            return {"exists": False}

        column_names = [str(row[0]) for row in columns]
        quoted_table = f'"{schema_name}"."{table_name}"'
        quoted_cols = ", ".join(f'"{name}"' for name in column_names)

        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        row_count = int(cursor.fetchone()[0])

        cursor.execute(f"SELECT {quoted_cols} FROM {quoted_table}")
        row_hash = _combine_row_hashes(cursor)

        schema_payload = [
            {
                "name": str(name).lower(),
                "type": str(data_type).lower(),
                "nullable": "YES" if str(nullable).upper() in {"Y", "YES"} else "NO",
                "default": None if default is None else str(default).strip(),
                "key": "",
                "extra": "",
                "ordinal": int(ordinal),
            }
            for name, data_type, nullable, default, ordinal in columns
        ]
        return {
            "exists": True,
            "columns": schema_payload,
            "row_count": row_count,
            "row_hash": row_hash,
        }
    finally:
        cursor.close()


def _compare(left_path: Path, right_path: Path, report_path: Path | None) -> int:
    left = json.loads(left_path.read_text(encoding="utf-8"))
    right = json.loads(right_path.read_text(encoding="utf-8"))
    left_tables = left.get("tables", {})
    right_tables = right.get("tables", {})
    all_tables = sorted(set(left_tables) | set(right_tables))
    mismatches: list[dict[str, Any]] = []
    for table in all_tables:
        a = left_tables.get(table, {"exists": False})
        b = right_tables.get(table, {"exists": False})
        item: dict[str, Any] = {"table": table, "issues": []}
        if bool(a.get("exists")) != bool(b.get("exists")):
            item["issues"].append("exists differs")
        if a.get("row_count") != b.get("row_count"):
            item["issues"].append(f"row_count differs: {a.get('row_count')} != {b.get('row_count')}")
        if a.get("row_hash") != b.get("row_hash"):
            item["issues"].append("row_hash differs")
        a_cols = [col.get("name") for col in a.get("columns", [])]
        b_cols = [col.get("name") for col in b.get("columns", [])]
        if a_cols != b_cols:
            item["issues"].append("column order/name differs")
        if item["issues"]:
            mismatches.append(item)

    report = {
        "left": str(left_path),
        "right": str(right_path),
        "table_count": len(all_tables),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")
        print(f"Compare report written: {report_path}")
    print(text)
    return 0 if not mismatches else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump Dameng table snapshots and optionally compare them.")
    parser.add_argument("--host", default=os.environ.get("DM_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DM_PORT", "5236")))
    parser.add_argument("--user", default=os.environ.get("DM_USER", "SYSDBA"))
    parser.add_argument("--password", default=os.environ.get("DM_PASSWORD", ""))
    parser.add_argument("--schema", default=os.environ.get("DM_SCHEMA", os.environ.get("DM_USER", "SYSDBA")))
    parser.add_argument("--odbc-dsn", default=os.environ.get("DM_ODBC_DSN"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--tables", nargs="*", default=None, help="Optional explicit table list.")
    parser.add_argument("--compare-with", default=None, help="Compare this snapshot with another JSON snapshot.")
    parser.add_argument("--report", default=str(TOOLS_ROOT / "db_compare" / "compare_report.json"))
    args = parser.parse_args()

    tables = args.tables or _tables_from_db_py()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(args)
    try:
        snapshot = {
            "dialect": "dm",
            "schema": args.schema,
            "host": args.host,
            "port": args.port,
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "tables": {},
        }
        for table in tables:
            snapshot["tables"][table] = _snapshot_table(conn, args.schema, table)
    finally:
        conn.close()

    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Dameng snapshot written: {out_path}")
    print(f"Tables checked: {len(tables)}")

    if args.compare_with:
        return _compare(Path(args.compare_with), out_path, Path(args.report) if args.report else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
