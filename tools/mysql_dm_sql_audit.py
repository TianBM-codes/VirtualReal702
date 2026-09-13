#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit app MySQL SQL and generate Dameng counterparts.

The script is read-only by default. It does not import or modify the main app.

What it does:
- scans Python files that use the app MySQL helper (`db.py`);
- extracts literal SQL passed to cursor.execute/executemany plus db.py DDL;
- detects MySQL-only syntax;
- generates a best-effort Dameng SQL counterpart;
- optionally validates parameter-free SELECT statements on MySQL and Dameng.

Parameterized statements need business sample parameters before they can be
executed safely. DML/DDL statements are reported, not executed.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import mysql.connector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = PROJECT_ROOT / "tools" / "db_compare" / "sql_audit.json"
DEFAULT_MD = PROJECT_ROOT / "tools" / "db_compare" / "sql_audit.md"

MYSQL_SOURCE_DIRS = [
    "db.py",
    "webapi",
    "src/modal_service",
    "src/l3/api/routes/projects.py",
    "services/model_update",
]

SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|RENAME|SHOW)\b", re.I)
PARAM_RE = re.compile(r"%s")
MYSQL_FEATURES = [
    ("auto_increment", re.compile(r"\bAUTO_INCREMENT\b", re.I)),
    ("on_duplicate_key_update", re.compile(r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b", re.I)),
    ("values_function", re.compile(r"\bVALUES\s*\(", re.I)),
    ("engine_charset", re.compile(r"\bENGINE\s*=|\bCHARSET\s*=", re.I)),
    ("inline_comment", re.compile(r"\bCOMMENT\s+'", re.I)),
    ("backtick_identifier", re.compile(r"`[^`]+`")),
    ("json_type", re.compile(r"\bJSON\b", re.I)),
    ("tinyint_bool", re.compile(r"\bTINYINT\s*\(\s*1\s*\)", re.I)),
    ("on_update_current_timestamp", re.compile(r"\bON\s+UPDATE\s+CURRENT_TIMESTAMP\b", re.I)),
    ("information_schema", re.compile(r"\binformation_schema\.", re.I)),
    ("show_columns", re.compile(r"^\s*SHOW\s+COLUMNS\b", re.I)),
    ("limit", re.compile(r"\bLIMIT\b", re.I)),
    ("now_function", re.compile(r"\bNOW\s*\(\s*\)", re.I)),
    ("change_column", re.compile(r"\bCHANGE\s+COLUMN\b", re.I)),
    ("key_index_ddl", re.compile(r"^\s*(UNIQUE\s+)?KEY\s+\w+", re.I | re.M)),
]


@dataclass
class SqlEntry:
    id: str
    file: str
    line: int
    call: str
    kind: str
    mysql_sql: str
    dm_sql: str
    features: list[str]
    parameter_count: int
    validation_status: str
    validation_detail: str


def _load_service_config() -> dict[str, Any]:
    path = Path(os.environ.get("CONFIG_FILE") or PROJECT_ROOT / "service_config.json")
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _cfg(config: dict[str, Any], key: str, default: Any) -> Any:
    return os.environ.get(key, config.get(key, default))


def _iter_source_files() -> Iterable[Path]:
    for item in MYSQL_SOURCE_DIRS:
        path = PROJECT_ROOT / item
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py"))


def _looks_like_mysql_file(path: Path, text: str) -> bool:
    if path.name == "db.py":
        return True
    return "from db import" in text or "import db" in text or "get_connection" in text


def _literal_string(node: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{expr}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, env)
        right = _literal_string(node.right, env)
        if left is not None and right is not None:
            return left + right
    return None


def _clean_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).strip()


def _kind(sql: str) -> str:
    match = SQL_START.search(sql)
    return match.group(1).upper() if match else "UNKNOWN"


def _features(sql: str) -> list[str]:
    return [name for name, pattern in MYSQL_FEATURES if pattern.search(sql)]


def _extract_ddl_from_db_py(path: Path) -> list[tuple[int, str, str]]:
    text = path.read_text(encoding="utf-8")
    module = ast.parse(text)
    rows: list[tuple[int, str, str]] = []
    for node in module.body:
        for target in getattr(node, "targets", []):
            if getattr(target, "id", None) != "CREATE_TABLE_SQL_LIST":
                continue
            if isinstance(node.value, ast.List):
                for item in node.value.elts:
                    sql = _literal_string(item, {})
                    if sql:
                        rows.append((getattr(item, "lineno", node.lineno), "ddl_list", sql))
    return rows


class SqlVisitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.env_stack: list[dict[str, str]] = [{}]
        self.items: list[tuple[int, str, str]] = []

    @property
    def env(self) -> dict[str, str]:
        return self.env_stack[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.env_stack.append(dict(self.env_stack[0]))
        self.generic_visit(node)
        self.env_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> Any:
        value = _literal_string(node.value, self.env)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.env[target.id] = value
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        receiver = ""
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            receiver = node.func.value.id.lower()
        is_cursor_call = "cursor" in receiver or receiver in {"_cursor", "local_cursor"}
        if attr in {"execute", "executemany"} and node.args and is_cursor_call:
            sql = _literal_string(node.args[0], self.env)
            if sql and SQL_START.search(sql):
                self.items.append((node.lineno, attr, sql))
        self.generic_visit(node)


def extract_sql_entries() -> list[tuple[Path, int, str, str]]:
    found: list[tuple[Path, int, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if not _looks_like_mysql_file(path, text):
            continue
        module = ast.parse(text)
        visitor = SqlVisitor(path)
        visitor.visit(module)
        for line, call, sql in visitor.items:
            key = (str(path), line, _clean_sql(sql))
            if key not in seen:
                found.append((path, line, call, sql))
                seen.add(key)
        if path.name == "db.py":
            for line, call, sql in _extract_ddl_from_db_py(path):
                key = (str(path), line, _clean_sql(sql))
                if key not in seen:
                    found.append((path, line, call, sql))
                    seen.add(key)
    return found


def _replace_limit(sql: str) -> str:
    sql = re.sub(
        r"\bLIMIT\s+(\d+)\s*,\s*(\d+)\b",
        r"OFFSET \1 ROWS FETCH NEXT \2 ROWS ONLY",
        sql,
        flags=re.I,
    )
    sql = re.sub(r"\bLIMIT\s+(\d+)\b", r"FETCH FIRST \1 ROWS ONLY", sql, flags=re.I)
    return sql


def _convert_show_columns(sql: str) -> str:
    match = re.match(r"\s*SHOW\s+COLUMNS\s+FROM\s+([`\w.]+)\s*$", sql, flags=re.I)
    if not match:
        return sql
    table = match.group(1).split(".")[-1].strip("`")
    return (
        "SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT "
        "FROM ALL_TAB_COLUMNS WHERE UPPER(TABLE_NAME)=UPPER('{}') ORDER BY COLUMN_ID"
    ).format(table)


def _convert_information_schema(sql: str) -> str:
    compact = _clean_sql(sql)
    if re.search(r"FROM\s+information_schema\.TABLES", compact, re.I):
        compact = re.sub(
            r"SELECT\s+TABLE_NAME\s+FROM\s+information_schema\.TABLES\s+"
            r"WHERE\s+TABLE_SCHEMA\s*=\s*%s\s+AND\s+TABLE_NAME\s+IN\s*\(%s,\s*%s\)",
            "SELECT TABLE_NAME FROM ALL_TABLES "
            "WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME) IN (UPPER(?), UPPER(?))",
            compact,
            flags=re.I,
        )
        compact = compact.replace("%s", "?")
        return compact
    if re.search(r"FROM\s+information_schema\.COLUMNS", compact, re.I):
        compact = re.sub(
            r"SELECT\s+1\s+FROM\s+information_schema\.COLUMNS\s+"
            r"WHERE\s+TABLE_SCHEMA\s*=\s*%s\s+AND\s+TABLE_NAME\s*=\s*%s\s+"
            r"AND\s+COLUMN_NAME\s*=\s*%s",
            "SELECT 1 FROM ALL_TAB_COLUMNS "
            "WHERE UPPER(OWNER)=UPPER(?) AND UPPER(TABLE_NAME)=UPPER(?) "
            "AND UPPER(COLUMN_NAME)=UPPER(?)",
            compact,
            flags=re.I,
        )
        compact = compact.replace("%s", "?")
        return compact
    return sql


def _convert_basic_ddl(sql: str) -> str:
    out = sql
    out = re.sub(r"`([^`]+)`", r'"\1"', out)
    out = re.sub(r"\bTINYINT\s*\(\s*1\s*\)", "SMALLINT", out, flags=re.I)
    out = re.sub(r"\bJSON\b", "CLOB", out, flags=re.I)
    out = re.sub(r"\bAUTO_INCREMENT\b", "IDENTITY(1,1)", out, flags=re.I)
    out = re.sub(r"\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP", "", out, flags=re.I)
    out = re.sub(r"\s+COMMENT\s+'[^']*'", "", out, flags=re.I)
    out = re.sub(r"\)\s*ENGINE\s*=\s*\w+\s+DEFAULT\s+CHARSET\s*=\s*\w+\s*;?", ");", out, flags=re.I)
    out = re.sub(r"\)\s*COMMENT\s*=\s*'[^']*'\s*;?", ");", out, flags=re.I)
    out = re.sub(r"^\s*UNIQUE\s+KEY\s+(\w+)\s*\(", r"CONSTRAINT \1 UNIQUE (", out, flags=re.I | re.M)
    out = re.sub(r"^\s*KEY\s+\w+\s*\([^)]+\)\s*,?", "", out, flags=re.I | re.M)
    return out


def _split_csv(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
            current.append(char)
            continue
        if char == ")":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        items.append("".join(current).strip())
    return items


def _load_mysql_unique_keys(args) -> dict[str, list[list[str]]]:
    try:
        conn = _connect_mysql(args)
    except Exception:
        return {}
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT tc.TABLE_NAME, tc.CONSTRAINT_NAME, kcu.COLUMN_NAME, kcu.ORDINAL_POSITION
            FROM information_schema.TABLE_CONSTRAINTS tc
            JOIN information_schema.KEY_COLUMN_USAGE kcu
              ON tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
             AND tc.TABLE_NAME = kcu.TABLE_NAME
             AND tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
            WHERE tc.TABLE_SCHEMA = %s
              AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE')
            ORDER BY tc.TABLE_NAME,
                     CASE WHEN tc.CONSTRAINT_TYPE = 'PRIMARY KEY' THEN 0 ELSE 1 END,
                     tc.CONSTRAINT_NAME,
                     kcu.ORDINAL_POSITION
            """,
            (args.mysql_database,),
        )
        grouped: dict[tuple[str, str], list[tuple[int, str]]] = {}
        order: list[tuple[str, str]] = []
        for table, constraint, column, ordinal in cursor.fetchall() or []:
            key = (str(table), str(constraint))
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append((int(ordinal), str(column)))
        result: dict[str, list[list[str]]] = {}
        for table, constraint in order:
            cols = [col for _, col in sorted(grouped[(table, constraint)])]
            result.setdefault(table.lower(), []).append(cols)
        return result
    finally:
        cursor.close()
        conn.close()


def _convert_upsert_to_merge(sql: str, key_map: dict[str, list[list[str]]]) -> tuple[str | None, str | None]:
    match = re.match(
        r"\s*INSERT\s+INTO\s+([`\w{}\.]+)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*"
        r"ON\s+DUPLICATE\s+KEY\s+UPDATE\s+(.*)\s*$",
        sql,
        flags=re.I | re.S,
    )
    if not match:
        return None, "UPSERT 需要按唯一键改写为 MERGE INTO；该 SQL 含动态结构，脚本未自动转换。"
    table = match.group(1).strip("`")
    if "{expr}" in table:
        return None, "UPSERT 表名是运行时动态值，需要按实际表名生成 MERGE。"
    cols = [col.strip().strip("`") for col in _split_csv(match.group(2))]
    values = _split_csv(match.group(3))
    updates = _split_csv(match.group(4))
    if len(cols) != len(values):
        return None, "UPSERT 插入列和值数量无法静态对应，需要人工确认。"

    key_candidates = key_map.get(table.lower(), [])
    col_lut = {col.lower(): col for col in cols}
    key_cols: list[str] | None = None
    for candidate in key_candidates:
        if all(col.lower() in col_lut for col in candidate):
            key_cols = [col_lut[col.lower()] for col in candidate]
            break
    if not key_cols:
        return None, "UPSERT 未找到可用于 MERGE ON 的主键/唯一键，需要先确认冲突键。"

    source_items = []
    for col, value in zip(cols, values):
        expr = value.strip()
        expr = re.sub(r"\bNOW\s*\(\s*\)", "CURRENT_TIMESTAMP", expr, flags=re.I)
        expr = expr.replace("%s", "?")
        source_items.append(f"{expr} AS {col}")

    update_items = []
    for item in updates:
        m_val = re.match(r"([`\w{}]+)\s*=\s*VALUES\s*\(\s*([`\w{}]+)\s*\)", item, flags=re.I)
        if m_val:
            left = m_val.group(1).strip("`")
            right = m_val.group(2).strip("`")
            if left not in key_cols:
                update_items.append(f"t.{left} = s.{right}")
            continue
        m_expr = re.match(r"([`\w{}]+)\s*=\s*(.+)", item, flags=re.I | re.S)
        if m_expr:
            left = m_expr.group(1).strip("`")
            expr = re.sub(r"\bNOW\s*\(\s*\)", "CURRENT_TIMESTAMP", m_expr.group(2).strip(), flags=re.I)
            if left not in key_cols:
                update_items.append(f"t.{left} = {expr}")

    on_clause = " AND ".join(f"t.{col} = s.{col}" for col in key_cols)
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"s.{col}" for col in cols)
    update_clause = ", ".join(update_items) if update_items else ", ".join(
        f"t.{col} = s.{col}" for col in cols if col not in key_cols
    )
    merge_sql = (
        f"MERGE INTO {table} t "
        f"USING (SELECT {', '.join(source_items)} FROM DUAL) s "
        f"ON ({on_clause}) "
        f"WHEN MATCHED THEN UPDATE SET {update_clause} "
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
    )
    return _clean_sql(merge_sql), None


def mysql_to_dm(sql: str, key_map: dict[str, list[list[str]]] | None = None) -> tuple[str, str]:
    out = sql.strip()
    manual = []
    if re.search(r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b", out, re.I):
        merge_sql, note = _convert_upsert_to_merge(out, key_map or {})
        if merge_sql:
            return merge_sql, ""
        manual.append(note or "UPSERT 需要按唯一键改写为 MERGE INTO。")
    if re.search(r"\binformation_schema\.", out, re.I):
        out = _convert_information_schema(out)
        if re.search(r"\binformation_schema\.", out, re.I):
            manual.append("information_schema 需要改为 ALL_TABLES/ALL_TAB_COLUMNS。")
    if re.search(r"\bCHANGE\s+COLUMN\b", out, re.I):
        manual.append("CHANGE COLUMN 需要拆成达梦 ALTER TABLE RENAME COLUMN / MODIFY。")

    out = _convert_show_columns(out)
    out = _convert_basic_ddl(out)
    out = re.sub(r"`([^`]+)`", r'"\1"', out)
    out = re.sub(r"\bNOW\s*\(\s*\)", "CURRENT_TIMESTAMP", out, flags=re.I)
    out = _replace_limit(out)
    out = out.replace("%s", "?")
    return _clean_sql(out), "；".join(manual)


def _row_hash(rows: Iterable[Iterable[Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = json.dumps([str(item) if item is not None else None for item in row], ensure_ascii=False)
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _connect_mysql(args):
    return mysql.connector.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        charset=args.mysql_charset,
        use_pure=True,
    )


def _connect_dm(args):
    import dmPython  # type: ignore

    conn = dmPython.connect(
        user=args.dm_user,
        password=args.dm_password,
        server=args.dm_host,
        port=args.dm_port,
    )
    cursor = conn.cursor()
    try:
        cursor.execute(f"SET SCHEMA {args.dm_schema}")
    finally:
        cursor.close()
    return conn


def _validate_select(mysql_sql: str, dm_sql: str, mysql_conn, dm_conn) -> tuple[str, str]:
    if PARAM_RE.search(mysql_sql) or "?" in mysql_sql:
        return "skipped", "参数化 SQL 需要业务样例参数"
    if "{expr}" in mysql_sql:
        return "skipped", "动态拼接 SQL 需要运行时上下文"
    if _kind(mysql_sql) != "SELECT":
        return "skipped", "只自动执行只读 SELECT"
    try:
        mcur = mysql_conn.cursor()
        dcur = dm_conn.cursor()
        mcur.execute(mysql_sql)
        dcur.execute(dm_sql)
        m_rows = mcur.fetchmany(1000)
        d_rows = dcur.fetchmany(1000)
        if _row_hash(m_rows) == _row_hash(d_rows):
            return "matched", "前 1000 行结果哈希一致"
        return "mismatch", "前 1000 行结果哈希不一致"
    except Exception as exc:
        return "error", repr(exc)
    finally:
        try:
            mcur.close()
        except Exception:
            pass
        try:
            dcur.close()
        except Exception:
            pass


def build_report(args) -> list[SqlEntry]:
    mysql_conn = dm_conn = None
    key_map = _load_mysql_unique_keys(args)
    if args.validate_readonly:
        mysql_conn = _connect_mysql(args)
        dm_conn = _connect_dm(args)

    entries: list[SqlEntry] = []
    try:
        for idx, (path, line, call, sql) in enumerate(extract_sql_entries(), start=1):
            mysql_sql = _clean_sql(sql)
            dm_sql, manual_note = mysql_to_dm(sql, key_map)
            features = _features(sql)
            status = "not_run"
            detail = manual_note
            if args.validate_readonly and mysql_conn is not None and dm_conn is not None:
                status, validate_detail = _validate_select(mysql_sql, dm_sql, mysql_conn, dm_conn)
                detail = "；".join(part for part in [manual_note, validate_detail] if part)
            elif manual_note:
                status = "manual_required"
            elif PARAM_RE.search(mysql_sql):
                status = "needs_sample_params"
                detail = "参数化 SQL 需要业务样例参数才能做结果一致性验证"
            elif _kind(mysql_sql) in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "RENAME"}:
                status = "not_executed_write_or_ddl"
                detail = "写入/DDL 不在默认审计中执行，避免改动数据库"

            rel = path.relative_to(PROJECT_ROOT).as_posix()
            entries.append(
                SqlEntry(
                    id=f"SQL{idx:04d}",
                    file=rel,
                    line=line,
                    call=call,
                    kind=_kind(mysql_sql),
                    mysql_sql=mysql_sql,
                    dm_sql=dm_sql,
                    features=features,
                    parameter_count=len(PARAM_RE.findall(mysql_sql)),
                    validation_status=status,
                    validation_detail=detail,
                )
            )
    finally:
        if mysql_conn is not None:
            mysql_conn.close()
        if dm_conn is not None:
            dm_conn.close()
    return entries


def write_markdown(entries: list[SqlEntry], path: Path) -> None:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.validation_status] = counts.get(entry.validation_status, 0) + 1
    lines = [
        "# MySQL SQL 到达梦 SQL 审计报告",
        "",
        f"生成时间：{_dt.datetime.now().isoformat(timespec='seconds')}",
        f"SQL 数量：{len(entries)}",
        "",
        "## 状态统计",
        "",
    ]
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")
    lines.extend(["", "## 明细", ""])
    for entry in entries:
        lines.extend(
            [
                f"### {entry.id} {entry.file}:{entry.line}",
                "",
                f"- 类型：`{entry.kind}`",
                f"- 特性：`{', '.join(entry.features) if entry.features else 'portable_or_unknown'}`",
                f"- 参数数量：`{entry.parameter_count}`",
                f"- 验证状态：`{entry.validation_status}`",
                f"- 说明：{entry.validation_detail or '无'}",
                "",
                "MySQL:",
                "",
                "```sql",
                entry.mysql_sql,
                "```",
                "",
                "达梦候选:",
                "",
                "```sql",
                entry.dm_sql,
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    config = _load_service_config()
    parser = argparse.ArgumentParser(description="Audit MySQL SQL and generate Dameng SQL counterparts.")
    parser.add_argument("--out-json", default=str(DEFAULT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_MD))
    parser.add_argument("--validate-readonly", action="store_true")
    parser.add_argument("--mysql-host", default=_cfg(config, "DB_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", type=int, default=int(_cfg(config, "DB_PORT", 3306)))
    parser.add_argument("--mysql-user", default=_cfg(config, "DB_USER", "root"))
    parser.add_argument("--mysql-password", default=_cfg(config, "DB_PASSWORD", ""))
    parser.add_argument("--mysql-database", default=_cfg(config, "DB_DATABASE", "db_simu_real_test"))
    parser.add_argument("--mysql-charset", default=_cfg(config, "DB_CHARSET", "utf8mb4"))
    parser.add_argument("--dm-host", default=os.environ.get("DM_HOST", "localhost"))
    parser.add_argument("--dm-port", type=int, default=int(os.environ.get("DM_PORT", "5236")))
    parser.add_argument("--dm-user", default=os.environ.get("DM_USER", "SYSDBA"))
    parser.add_argument("--dm-password", default=os.environ.get("DM_PASSWORD", ""))
    parser.add_argument("--dm-schema", default=os.environ.get("DM_SCHEMA", "DB_SIMU_REAL_TEST"))
    args = parser.parse_args()

    entries = build_report(args)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "entry_count": len(entries),
        "entries": [asdict(entry) for entry in entries],
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(entries, out_md)
    print(f"SQL audit JSON written: {out_json}")
    print(f"SQL audit Markdown written: {out_md}")
    print(f"Entries: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
