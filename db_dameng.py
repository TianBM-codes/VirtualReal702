"""Dameng implementation of the small DB-API surface used by the services.

SQL is tokenized before translation: literals, comments and bound values must
never be rewritten as SQL. MySQL connections do not pass through this module.
"""

import hashlib
import re
import threading


class UnsupportedSQL(ValueError):
    pass


_TOKEN = re.compile(
    r"\s+|--[^\n]*|/\*.*?\*/|'(?:''|\\.|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"`(?:``|[^`])*`|%s|:p\d+|[A-Za-z_][\w$]*|\d+(?:\.\d+)?|<>|!=|<=|>=|.",
    re.S,
)


def tokens(sql):
    return [t for t in _TOKEN.findall(sql) if not t.isspace()
            and not t.startswith(("--", "/*")) and t != ";"]


def ident(name):
    return name.strip('`"').replace('``', '`').replace('""', '"')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def split_list(items):
    result, start, depth = [], 0, 0
    for i, item in enumerate(items):
        depth += (item == "(") - (item == ")")
        if item == "," and depth == 0:
            result.append(items[start:i])
            start = i + 1
    result.append(items[start:])
    return result


def closing(items, start):
    depth = 0
    for i in range(start, len(items)):
        depth += (items[i] == "(") - (items[i] == ")")
        if depth == 0:
            return i
    raise UnsupportedSQL("Unbalanced SQL parentheses")


def render(items):
    return " ".join(items)


def bind(items, params):
    output, values = [], []
    for item in items:
        if re.fullmatch(r":p\d+", item):
            output.append("?")
            values.append(params[int(item[2:])])
        else:
            output.append(item)
    return render(output), tuple(values)


def prepare(sql, params=()):
    items, number = [], 0
    for item in tokens(sql):
        if item == "%s":
            items.append(f":p{number}")
            number += 1
        elif item.startswith("`"):
            items.append(quote(ident(item)))
        else:
            items.append(item)
    if number != len(params):
        raise ValueError(f"SQL expects {number} parameters, received {len(params)}")
    return items


def basic(items):
    result, i = [], 0
    while i < len(items):
        word = items[i].upper()
        if word == "NOW" and items[i + 1:i + 3] == ["(", ")"]:
            result.append("CURRENT_TIMESTAMP")
            i += 3
        elif word == "CHAR" and result and result[-1].upper() == "AS":
            result.extend(["VARCHAR", "(", "4000", ")"])
            i += 1
        else:
            result.append(items[i])
            i += 1
    # LIMIT is a top-level clause; quoted strings/identifiers are opaque tokens.
    depth = 0
    for i, item in enumerate(result):
        depth += (item == "(") - (item == ")")
        if item.upper() == "LIMIT" and depth == 0:
            tail = result[i + 1:]
            if len(tail) == 1:
                return result[:i] + ["FETCH FIRST", tail[0], "ROWS ONLY"]
            if len(tail) == 3 and tail[1] == ",":
                return result[:i] + ["OFFSET", tail[0], "ROWS FETCH NEXT", tail[2], "ROWS ONLY"]
            if len(tail) == 3 and tail[1].upper() == "OFFSET":
                return result[:i] + ["OFFSET", tail[2], "ROWS FETCH NEXT", tail[0], "ROWS ONLY"]
            raise UnsupportedSQL("Unsupported LIMIT clause")
    return result


def column_definition(items):
    result, i = [], 0
    while i < len(items):
        word = items[i].upper()
        if word == "COMMENT":
            i += 2
        elif word == "ON" and [t.upper() for t in items[i:i + 3]] == ["ON", "UPDATE", "CURRENT_TIMESTAMP"]:
            i += 3  # Maintained by an explicit row trigger.
        elif word == "AUTO_INCREMENT":
            # DM requires IDENTITY immediately after the numeric type.
            pos = 2
            if len(result) > 2 and result[2] == "(":
                pos = closing(result, 2) + 1
            result[pos:pos] = ["IDENTITY(1,1)"]
            i += 1
        elif word in {"JSON", "TEXT", "LONGTEXT", "MEDIUMTEXT"}:
            result.append("CLOB")
            i += 1
        elif word in {"LONGBLOB", "MEDIUMBLOB"}:
            result.append("BLOB")
            i += 1
        elif word == "TINYINT":
            result.append("SMALLINT")
            i += 4 if items[i + 1:i + 4] == ["(", "1", ")"] else 1
        elif word == "STORED":
            i += 1
        elif word == "GENERATED" and [t.upper() for t in items[i:i + 3]] == ["GENERATED", "ALWAYS", "AS"]:
            result.append("AS")
            i += 3
        else:
            result.append(items[i])
            i += 1
    return result


def table_spec(sql):
    items = tokens(sql)
    start = items.index("(")
    table = ident(items[start - 1])
    fields, keys, indexes, automatic = {}, [], [], []
    for part in split_list(items[start + 1:closing(items, start)]):
        head = part[0].upper()
        if head in {"PRIMARY", "UNIQUE", "KEY"}:
            pos = part.index("(")
            columns = split_list(part[pos + 1:closing(part, pos)])
            # Preserve MySQL's prefix index semantics with SUBSTR, not a full key.
            expressions = []
            for col in columns:
                name = ident(col[0])
                expressions.append((name, int(col[2]) if len(col) == 4 else None))
            if head != "KEY":
                keys.append(expressions)
            indexes.append((head, ident(part[2] if head == "UNIQUE" else part[1]), expressions))
        else:
            fields[ident(part[0]).lower()] = part
            upper = [t.upper() for t in part]
            if "ON" in upper and "UPDATE" in upper:
                automatic.append(ident(part[0]))
    return table, fields, keys, indexes, automatic


class CaseRow(dict):
    """Keep real column names and accept either driver's identifier casing."""

    def __getitem__(self, key):
        if key in self:
            return super().__getitem__(key)
        return super().__getitem__(next((k for k in self if k.lower() == key.lower()), key))

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class DamengPool:
    """Bound simultaneous sessions; never share a session between threads.

    New sessions avoid leaking SET SCHEMA, transactions or identity state when
    a pooled connection is returned after a failed request.
    """

    def __init__(self, config, ddl):
        self.config = config
        self.slots = threading.BoundedSemaphore(config.get("pool_size", 10))
        self.ddl = tuple(ddl)
        self.specs = {table_spec(sql)[0].lower(): table_spec(sql) for sql in ddl}

    def get_connection(self):
        if not self.slots.acquire(timeout=self.config.get("connect_timeout", 5)):
            raise TimeoutError("Dameng connection limit reached")
        raw = None
        try:
            import dmPython
            raw = dmPython.connect(user=self.config["user"], password=self.config["password"],
                                   server=self.config["host"], port=self.config["port"],
                                   autoCommit=False, local_code=dmPython.PG_UTF8,
                                   login_timeout=self.config.get("connect_timeout", 5) * 1000)
            cursor = raw.cursor()
            try:
                cursor.execute("SELECT SF_GET_CASE_SENSITIVE_FLAG() FROM DUAL")
                if int(cursor.fetchone()[0]):
                    raise RuntimeError("This adapter requires a case-insensitive DM instance (CASE_SENSITIVE=0)")
                cursor.execute("SELECT PARA_VALUE FROM V$DM_INI WHERE PARA_NAME='COMPATIBLE_MODE'")
                if int(cursor.fetchone()[0]) != 4:
                    raise RuntimeError("DM requires COMPATIBLE_MODE=4 for MySQL-compatible nullable unique keys")
                cursor.execute("SELECT CASE WHEN '' IS NULL THEN 1 ELSE 0 END FROM DUAL")
                if int(cursor.fetchone()[0]):
                    raise RuntimeError("DM treats empty strings as NULL; configure MySQL-compatible empty-string semantics")
                cursor.execute("SET SCHEMA " + quote(self.config["database"]))
            finally:
                cursor.close()
            return DamengConnection(raw, self)
        except Exception:
            if raw is not None:
                raw.close()
            self.slots.release()
            raise


class DamengConnection:
    def __init__(self, raw, pool):
        self.raw, self.pool, self.closed = raw, pool, False
        self._metadata = {}

    def cursor(self, dictionary=False, **kwargs):
        if kwargs:
            raise TypeError("Unsupported Dameng cursor options: " + ", ".join(kwargs))
        if self.closed:
            raise RuntimeError("Connection is closed")
        return DamengCursor(self, dictionary)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def refresh_schema_artifacts(self):
        # Run after column migrations too, so triggers never refer to missing
        # columns on an older imported schema.
        with self.cursor() as cursor:
            for sql in self.pool.ddl:
                cursor._create(sql)

    def ping(self, **kwargs):
        with self.cursor() as cursor:
            cursor.execute("SELECT 1 FROM DUAL")
            cursor.fetchall()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                try:
                    self.raw.rollback()
                finally:
                    self.raw.close()
            finally:
                self.pool.slots.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def columns(self, table):
        key = table.lower()
        if key not in self._metadata:
            cursor = self.raw.cursor()
            try:
                cursor.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER=? "
                               "AND UPPER(TABLE_NAME)=UPPER(?) ORDER BY COLUMN_ID",
                               (self.pool.config["database"], table))
                self._metadata[key] = {str(row[0]).lower(): str(row[0]) for row in cursor.fetchall()}
            finally:
                cursor.close()
        return self._metadata[key]


class DamengCursor:
    def __init__(self, conn, dictionary=False):
        self.conn, self.dictionary = conn, dictionary
        self.raw = conn.raw.cursor()
        self.lastrowid = None
        self._rows = None
        self._description = None
        self.rowcount = -1

    @property
    def description(self):
        return self._description if self._rows is not None else self.raw.description

    def _run(self, items, params=()):
        sql, values = bind(basic(items), params)
        explicit_identity = self._explicit_identity(items)
        if explicit_identity:
            self.raw.execute("SET IDENTITY_INSERT " + items[2] + " ON")
        try:
            self.raw.execute(sql, values) if values else self.raw.execute(sql)
            self.rowcount = self.raw.rowcount
            self._inserted_rowid = getattr(self.raw, "lastrowid", None)
        finally:
            if explicit_identity:
                self.raw.execute("SET IDENTITY_INSERT " + items[2] + " OFF")

    def _explicit_identity(self, items):
        if [t.upper() for t in items[:2]] == ["INSERT", "INTO"] and len(items) > 3 and items[3] == "(":
            spec = self.conn.pool.specs.get(ident(items[2]).lower())
            cols = {ident(p[0]).lower() for p in split_list(items[4:closing(items, 3)])}
            return bool(spec and any(name in cols and "AUTO_INCREMENT" in [t.upper() for t in part]
                                    for name, part in spec[1].items()))
        return False

    def _synthetic(self, columns, rows):
        self._description = [(name, None, None, None, None, None, None) for name in columns]
        self._rows = iter(rows)
        self.rowcount = len(rows)

    def execute(self, sql, params=None):
        self._rows = None
        self.lastrowid = None
        params = tuple(params) if params is not None else ()
        items = prepare(sql, params)
        upper = [t.upper() for t in items]
        if upper[:2] == ["SHOW", "COLUMNS"]:
            table = ident(items[3])
            cols = self.conn.columns(table)
            rows = []
            wanted = ident(items[5]).strip("'") if len(items) > 4 else None
            for name in cols.values():
                if wanted is not None and wanted.lower() != name.lower():
                    continue
                self.raw.execute("SELECT DATA_TYPE, DATA_LENGTH, NULLABLE, DATA_DEFAULT "
                                 "FROM ALL_TAB_COLUMNS WHERE OWNER=? AND UPPER(TABLE_NAME)=UPPER(?) "
                                 "AND COLUMN_NAME=?", (self.conn.pool.config["database"], table, name))
                data_type, size, nullable, default = self.raw.fetchone()
                type_text = str(data_type).lower()
                if type_text in {"varchar", "varchar2", "char"}:
                    type_text = f"varchar({size})"
                rows.append((name.lower(), type_text, "YES" if nullable == "Y" else "NO", "", default, ""))
            self._synthetic(["Field", "Type", "Null", "Key", "Default", "Extra"], rows)
        elif any(t.lower() == "information_schema" for t in items):
            self._catalog(items, params)
        elif upper[:2] == ["CREATE", "TABLE"]:
            self._create(sql)
        elif upper[:2] == ["ALTER", "TABLE"]:
            self._alter(items)
        elif upper[:2] == ["RENAME", "TABLE"]:
            self._run(["ALTER TABLE", items[2], "RENAME TO", items[4]])
        elif upper[:2] == ["DROP", "TABLE"]:
            self._run(items)
        elif "DUPLICATE" in upper:
            self._upsert(items, params)
        else:
            self._run(items, params)
            if upper[0] == "INSERT":
                self._identity(ident(items[2]))
        return self

    def _catalog(self, items, params):
        text = render(items)
        match = re.search(r"information_schema\s*\.\s*(\w+)", text, re.I)
        kind = match.group(1).upper()
        if kind == "STATISTICS":
            owner, table, index = params
            objects = self._key_objects(table, index)
            rows = []
            if objects:
                kind, name = objects[0]
                if kind == "constraint":
                    self.raw.execute("SELECT COLUMN_NAME FROM ALL_CONS_COLUMNS WHERE OWNER=? "
                                     "AND CONSTRAINT_NAME=? ORDER BY POSITION", (owner, name))
                else:
                    self.raw.execute("SELECT COLUMN_NAME FROM ALL_IND_COLUMNS WHERE INDEX_OWNER=? "
                                     "AND INDEX_NAME=? ORDER BY COLUMN_POSITION", (owner, name))
                rows = [(str(r[0]).lower(),) for r in self.raw.fetchall()]
            self._synthetic(["COLUMN_NAME"], rows)
            return
        mapping = {"TABLES": ("ALL_TABLES", {"TABLE_SCHEMA": "OWNER"}),
                   "COLUMNS": ("ALL_TAB_COLUMNS", {"TABLE_SCHEMA": "OWNER"})}
        if kind not in mapping:
            raise UnsupportedSQL("Unknown information_schema query: " + kind)
        view, names = mapping[kind]
        output, i = [], 0
        while i < len(items):
            if items[i].lower() == "information_schema":
                output.append(view)
                i += 3
            else:
                word = items[i].upper()
                output.append(names.get(word, items[i]))
                i += 1
        # Catalog names may be lower-case quoted names after migration.
        for word in ("OWNER", "TABLE_NAME", "COLUMN_NAME"):
            output = tokens(re.sub(rf"\b{word}\s*=\s*(:p\d+)",
                                   rf"UPPER({word})=UPPER(\1)", render(output), flags=re.I))
        if kind == "TABLES":
            output = tokens(re.sub(r"TABLE_NAME\s+IN\s*\(\s*(:p\d+)\s*,\s*(:p\d+)\s*\)",
                                   r"UPPER(TABLE_NAME) IN (UPPER(\1),UPPER(\2))", render(output), flags=re.I))
        self._run(output, params)
        rows = self.raw.fetchall()
        names = [r[0] for r in self.raw.description]
        if kind == "TABLES":
            rows = [(str(r[0]).lower(),) for r in rows]
        self._synthetic(names, rows)

    def _identity(self, table):
        # dmPython.lastrowid is a physical ROWID, not MySQL's numeric identity.
        spec = self.conn.pool.specs.get(table.lower())
        column = next((name for name, part in spec[1].items()
                       if "AUTO_INCREMENT" in [t.upper() for t in part]), None) if spec else None
        if not column or not self._inserted_rowid:
            return
        probe = self.conn.raw.cursor()
        try:
            probe.execute("SELECT " + quote(column) + " FROM " + quote(table) + " WHERE ROWID=?",
                          (self._inserted_rowid,))
            row = probe.fetchone()
            self.lastrowid = int(row[0]) if row and row[0] is not None else None
        finally:
            probe.close()

    def _key_objects(self, table, name):
        """Resolve both imported named constraints and app-managed indexes."""
        result = []
        owner = self.conn.pool.config["database"]
        for candidate in (name, index_name(table, name)):
            self.raw.execute("SELECT CONSTRAINT_NAME FROM ALL_CONSTRAINTS WHERE OWNER=? "
                             "AND UPPER(TABLE_NAME)=UPPER(?) AND UPPER(CONSTRAINT_NAME)=UPPER(?) "
                             "AND CONSTRAINT_TYPE='U'", (owner, table, candidate))
            constraints = [("constraint", str(row[0])) for row in self.raw.fetchall()]
            result.extend(constraints)
            if not constraints:
                self.raw.execute("SELECT INDEX_NAME FROM ALL_INDEXES WHERE OWNER=? "
                                 "AND UPPER(TABLE_NAME)=UPPER(?) AND UPPER(INDEX_NAME)=UPPER(?)",
                                 (owner, table, candidate))
                result.extend(("index", str(row[0])) for row in self.raw.fetchall())
        return result

    def _create(self, sql):
        table, fields, keys, indexes, automatic = table_spec(sql)
        self.raw.execute("SELECT 1 FROM ALL_TABLES WHERE OWNER=? AND UPPER(TABLE_NAME)=UPPER(?)",
                         (self.conn.pool.config["database"], table))
        exists = self.raw.fetchone() is not None
        if not exists:
            definitions = [render(column_definition(p)) for p in fields.values()]
            for head, name, cols in indexes:
                if head == "PRIMARY":
                    definitions.append("PRIMARY KEY (" + ",".join(quote(c) for c, _ in cols) + ")")
            self.raw.execute("CREATE TABLE " + quote(table) + " (" + ",".join(definitions) + ")")
        self.conn._metadata.pop(table.lower(), None)
        available = self.conn.columns(table)
        for field, part in fields.items():
            if part[1].upper() != "JSON" or field not in available:
                continue
            name = index_name(table, "json_" + field)
            self.raw.execute("SELECT 1 FROM ALL_CONSTRAINTS WHERE OWNER=? AND CONSTRAINT_NAME=?",
                             (self.conn.pool.config["database"], name))
            if self.raw.fetchone() is None:
                self.raw.execute("ALTER TABLE " + quote(table) + " ADD CONSTRAINT " + quote(name)
                                 + " CHECK (" + quote(field) + " IS JSON)")
        for head, name, cols in indexes:
            if head == "PRIMARY":
                continue
            if not all(c.lower() in available for c, _ in cols):
                continue
            # Index names are schema-scoped in DM (table-scoped in MySQL).
            existing = self._key_objects(table, name)
            name = index_name(table, name)
            if not existing:
                expressions = [f"SUBSTR({quote(c)},1,{n})" if n else quote(c) for c, n in cols]
                self.raw.execute("CREATE " + ("UNIQUE " if head == "UNIQUE" else "") + "INDEX "
                                 + quote(name) + " ON " + quote(table) + " (" + ",".join(expressions) + ")")
        self.conn._metadata.pop(table.lower(), None)
        # Auto timestamps must also work on imported tables lacking triggers.
        for column in automatic:
            if column.lower() not in available:
                continue
            name = index_name(table, "touch_" + column)
            changes = []
            for field, part in fields.items():
                if field == column.lower() or field not in available:
                    continue
                new, old = ":NEW." + quote(field), ":OLD." + quote(field)
                if part[1].upper() in {"JSON", "TEXT", "LONGTEXT", "MEDIUMTEXT", "BLOB"}:
                    different = f"DBMS_LOB.COMPARE({new},{old}) <> 0"
                else:
                    different = f"{new} <> {old}"
                changes.append(f"({different} OR ({new} IS NULL AND {old} IS NOT NULL) "
                               f"OR ({new} IS NOT NULL AND {old} IS NULL))")
            changed = " OR ".join(changes) or "1=0"
            self.raw.execute("CREATE OR REPLACE TRIGGER " + quote(name) + " BEFORE UPDATE ON " + quote(table)
                             + " FOR EACH ROW BEGIN IF NOT UPDATING('" + column.replace("'", "''") + "')"
                             + " AND (" + changed + ") THEN :NEW." + quote(column)
                             + " := CURRENT_TIMESTAMP; END IF; END;")

    def _alter(self, items):
        table = ident(items[2])
        tail = items[3:]
        upper = [t.upper() for t in tail]
        if upper[:2] == ["CHANGE", "COLUMN"]:
            self._run(["ALTER TABLE", items[2], "RENAME COLUMN", tail[2], "TO", tail[3]])
            self._run(["ALTER TABLE", items[2], "MODIFY", "("] + column_definition(tail[3:]) + [")"])
        elif upper[:2] == ["DROP", "INDEX"] or upper[:3] == ["ADD", "UNIQUE", "KEY"]:
            for part in split_list(tail):
                words = [t.upper() for t in part]
                if words[:2] == ["DROP", "INDEX"]:
                    for kind, name in self._key_objects(table, ident(part[2])):
                        if kind == "constraint":
                            self._run(["ALTER TABLE", items[2], "DROP CONSTRAINT", quote(name)])
                        else:
                            self._run(["DROP INDEX", quote(name)])
                elif words[:3] == ["ADD", "UNIQUE", "KEY"]:
                    self._run(["CREATE UNIQUE INDEX", quote(index_name(table, ident(part[3]))),
                               "ON", items[2]] + part[4:])
                else:
                    raise UnsupportedSQL("Unsupported index migration")
        elif upper[:2] in (["ADD", "COLUMN"], ["MODIFY", "COLUMN"]):
            self._run(["ALTER TABLE", items[2], upper[0], "("] + column_definition(tail[2:]) + [")"])
        else:
            raise UnsupportedSQL("Unsupported ALTER TABLE: " + render(items))
        self.conn._metadata.pop(table.lower(), None)

    def _upsert(self, items, params):
        upper = [t.upper() for t in items]
        on = upper.index("DUPLICATE") - 1
        if upper[:2] != ["INSERT", "INTO"] or items[3] != "(":
            raise UnsupportedSQL("Only INSERT INTO table (columns) VALUES (...) upserts are supported")
        table = ident(items[2])
        end = closing(items, 3)
        columns = [ident(p[0]) for p in split_list(items[4:end])]
        value_start = end + 2
        if upper[end + 1] != "VALUES" or items[value_start] != "(":
            raise UnsupportedSQL("Upsert requires one VALUES row; use executemany for batches")
        value_end = closing(items, value_start)
        if value_end != on - 1:
            raise UnsupportedSQL("Multi-row upsert requires executemany")
        values = split_list(items[value_start + 1:value_end])
        if len(values) != len(columns):
            raise UnsupportedSQL("Upsert column/value mismatch")
        value_map = dict(zip((c.lower() for c in columns), values))
        spec = self.conn.pool.specs.get(table.lower())
        if spec is None:
            raise UnsupportedSQL("Upsert table has no registered key definition: " + table)
        clauses = []
        for key in spec[2]:
            if all(c.lower() in value_map for c, _ in key):
                clause = []
                for c, length in key:
                    if clause:
                        clause.append("AND")
                    left = [quote(c)]
                    right = value_map[c.lower()]
                    if length:
                        left = ["SUBSTR", "("] + left + [",", "1", ",", str(length), ")"]
                        right = ["SUBSTR", "("] + right + [",", "1", ",", str(length), ")"]
                    clause.extend(left + ["="] + right)
                clauses.append(["("] + clause + [")"])
        if not clauses:
            raise UnsupportedSQL("Upsert supplies no complete primary/unique key: " + table)
        predicate = []
        for clause in clauses:
            if predicate:
                predicate.append("OR")
            predicate.extend(clause)
        # Match all unique keys, including nullable and prefix keys. Never pick
        # an arbitrary key or silently update multiple conflicting records.
        def conflicting_rows():
            self._run(["SELECT ROWID FROM", items[2], "WHERE"] + predicate + ["FOR UPDATE"], params)
            return self.raw.fetchmany(2)

        rows = conflicting_rows()
        if not rows:
            self.raw.execute("SAVEPOINT odb_upsert")
            try:
                self._run(items[:on], params)
                self._identity(table)
                return
            except Exception as exc:
                self.raw.execute("ROLLBACK TO SAVEPOINT odb_upsert")
                if not any("6602" in str(arg) for arg in exc.args):
                    raise
                rows = conflicting_rows()
                if not rows:
                    raise
        if len(rows) != 1:
            raise UnsupportedSQL("Upsert conflicts with multiple records; reconcile unique keys: " + table)
        updates, i = [], on + 4
        while i < len(items):
            if upper[i] == "VALUES" and items[i + 1] == "(":
                name = ident(items[i + 2]).lower()
                updates.extend(value_map[name])
                i += 4
            else:
                updates.append(items[i])
                i += 1
        # ROWID identifies precisely the one locked row, even when keys change.
        sql, bound = bind(basic(["UPDATE", items[2], "SET"] + updates), params)
        self.raw.execute(sql + " WHERE ROWID=?", bound + (rows[0][0],))
        self.rowcount = self.raw.rowcount
        self._inserted_rowid = rows[0][0]
        self._identity(table)

    def _row(self, row):
        if row is None:
            return None
        values = tuple(value.read() if hasattr(value, "read") else value for value in row)
        if self.dictionary:
            return CaseRow(zip((str(col[0]).lower() for col in self.description), values))
        return values

    def fetchone(self):
        return self._row(next(self._rows, None) if self._rows is not None else self.raw.fetchone())

    def fetchmany(self, size=1000):
        if self._rows is None:
            return [self._row(row) for row in self.raw.fetchmany(size)]
        result = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            result.append(row)
        return result

    def fetchall(self):
        return [self._row(row) for row in (list(self._rows) if self._rows is not None else self.raw.fetchall())]

    def executemany(self, sql, rows):
        # Plain inserts/updates use the driver's array binding. Upserts require
        # per-row locking to retain unique-key and transaction semantics.
        if not any(t.upper() == "DUPLICATE" for t in tokens(sql)):
            iterator = iter(rows)
            total = 0
            while True:
                batch = []
                for _ in range(1000):
                    try:
                        batch.append(tuple(next(iterator)))
                    except StopIteration:
                        break
                if not batch:
                    break
                items = prepare(sql, batch[0])
                query, _ = bind(basic(items), batch[0])
                bound = [bind(basic(prepare(sql, row)), row)[1] for row in batch]
                self._rows = None
                self.lastrowid = None
                explicit = self._explicit_identity(items)
                if explicit:
                    self.raw.execute("SET IDENTITY_INSERT " + items[2] + " ON")
                try:
                    self.raw.executemany(query, bound)
                    total += max(0, self.raw.rowcount)
                finally:
                    if explicit:
                        self.raw.execute("SET IDENTITY_INSERT " + items[2] + " OFF")
            self.rowcount = total
            return self
        count = 0
        for row in rows:
            self.execute(sql, row)
            count += max(0, self.rowcount)
        self.rowcount = count
        return self

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def index_name(table, name):
    """Stable schema-wide name, short enough for DM identifier limits."""
    return "odb_" + hashlib.sha256((table.lower() + ":" + name.lower()).encode()).hexdigest()[:24]
